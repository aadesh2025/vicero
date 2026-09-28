"""BotForge API application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routers import health
from app.channels.router import router as channels_router
from app.chat import guard_models
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.llm import embeddings
from app.modules.admin.router import router as admin_router
from app.modules.agent_tests.router import test_run_router as agent_test_run_router
from app.modules.agent_tests.router import test_runs_by_agent_router as agent_test_runs_by_agent_router
from app.modules.agent_tests.router import tests_router as agent_tests_router
from app.modules.agents.router import router as agents_router
from app.modules.agents.router import templates_router as agent_templates_router
from app.modules.analytics.router import router as analytics_router
from app.modules.apikeys.router import router as apikeys_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.billing.router import router as billing_router
from app.modules.campaigns.router import router as campaigns_router
from app.modules.canned_responses.router import router as canned_responses_router
from app.modules.contacts.router import router as contacts_router
from app.modules.conversations.router import router as conversations_router
from app.modules.credentials.router import router as credentials_router
from app.modules.help_articles.router import router as help_articles_router
from app.modules.inbox.router import router as inbox_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.macros.router import inbox_router as macros_inbox_router
from app.modules.macros.router import router as macros_router
from app.modules.orgs.router import router as orgs_router
from app.modules.public.router import router as public_router
from app.modules.workflow_tests.router import test_run_router as workflow_test_run_router
from app.modules.workflow_tests.router import test_runs_by_workflow_router as workflow_test_runs_by_workflow_router
from app.modules.workflow_tests.router import tests_router as workflow_tests_router
from app.rag import converters, rerank
from app.realtime.hub import hub
from app.tools.mcp_router import router as mcp_router
from app.tools.router import router as tools_router
from app.webhooks.router import router as webhooks_router
from app.workflows.router import agent_workflows_router
from app.workflows.router import router as workflows_router
from app.workflows.router import runs_router as workflow_runs_router

log = get_logger("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log.info("startup", env=settings.env, version=__version__)
    _warn_missing_secrets()
    # Needs the network, so it cannot live in `_warn_missing_secrets`. Capped at a couple of
    # seconds and it swallows everything — see `embeddings.probe_reachable`. Off in the suite via
    # `EMBEDDING_PROBE_ENABLED`, which conftest clears.
    await embeddings.probe_reachable()
    # Same shape, different reason: not a reachability check but a real conversion kicked off so
    # the first real client upload isn't the one that pays for a cold model load (docs/14 K1-5
    # follow-up). Gated by `docling_enabled` exactly like real conversion — a no-op when off,
    # which is every deployment today. `conftest.py` sets `docling_enabled = False` for the suite.
    await converters.probe_reachable()
    # Bridge the realtime hub over Redis so operator↔widget delivery works across API replicas.
    await hub.connect(settings.redis_url)
    yield
    await hub.close()
    log.info("shutdown")


def _warn_missing_secrets() -> None:
    """Loudly note stubbed features so the human knows what to fill in (CLAUDE.md §7)."""
    if settings.llm_force_fake:
        # Announce this at the top and unmissably. A fake-forced process answers every chat
        # with a literal "echo: <your message>" stub, which reads exactly like a broken or
        # lobotomised model rather than a config choice — an hour was lost to a Playground
        # pointed at the E2E instance before anyone suspected the flag.
        log.warning(
            "llm_force_fake_enabled",
            effect="EVERY chat reply is a stub echo and EVERY embedding is fake",
            impact="test/CI only — never serve a Playground, widget or customer from this process",
            fix="unset LLM_FORCE_FAKE to use the agent's configured provider",
        )
        if settings.is_prod:
            log.error("llm_force_fake_in_production", effect="all AI replies are stubs")
    if settings.secret_key == "dev-insecure-change-me" and settings.is_prod:
        log.warning("insecure_secret_key", hint="Set SECRET_KEY in production")
    if not settings.groq_api_key:
        log.warning("missing_key", provider="groq", effect="stubbed; set GROQ_API_KEY")
    if not settings.n8n_api_key:
        log.warning("missing_key", service="n8n", effect="disabled; set N8N_API_KEY")
    _warn_malformed_keys()
    guard_models.warn_if_unconfigured()
    rerank.warn_if_misconfigured()
    converters.warn_if_misconfigured()
    embeddings.warn_if_misconfigured()


# A credential can't contain these and still be valid, so their presence means a typo or a
# half-pasted value — the state that used to masquerade as "configured" (ADR-044).
_MALFORMED = (" ", "\t", "#")


def _warn_malformed_keys() -> None:
    """Catch a key that is present but obviously junk, instead of 400ing forever in silence.

    Placeholder comments no longer reach here (config.py drops them), so anything matching now
    is genuine bad input. Values are never logged — only the setting name.
    """
    for name in (
        "groq_api_key",
        "gemini_api_key",
        "openrouter_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "n8n_api_key",
    ):
        value = getattr(settings, name, None)
        if isinstance(value, str) and value.strip() and any(c in value for c in _MALFORMED):
            log.warning(
                "malformed_key",
                setting=name.upper(),
                effect="requests using it will be rejected by the provider",
                fix="re-copy the key; it contains whitespace or a '#'",
            )


def _init_sentry() -> None:
    """Wire Sentry error tracking when a DSN is configured (no-op otherwise)."""
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.env,
            release=__version__,
            traces_sample_rate=0.1,
            send_default_pii=False,
        )
        log.info("sentry_enabled", env=settings.env)
    except Exception as exc:  # never let observability wiring break startup
        log.warning("sentry_init_failed", error=str(exc))


def _serve_schema() -> bool:
    """Whether the interactive schema routes exist at all.

    When they don't, FastAPI is given `None` for each URL, which removes the route: prod
    answers 404 rather than 403, so nothing confirms the endpoint was ever there.
    """
    return not settings.is_prod


def create_app() -> FastAPI:
    _init_sentry()
    app = FastAPI(
        title="BotForge API",
        version=__version__,
        description="AI chatbot & automation platform — backend API.",
        lifespan=lifespan,
        # Swagger, ReDoc and the raw schema are dev tools. Served in prod they publish the
        # entire surface — every `/v1/admin/*` route, every request shape — to anyone who
        # asks, which is a free reconnaissance map and nothing a tenant needs: the public
        # API reference on the docs site is generated from a committed snapshot of this same
        # schema (scripts/generate-openapi.py), so turning these off costs nobody anything.
        docs_url="/docs" if _serve_schema() else None,
        redoc_url="/redoc" if _serve_schema() else None,
        openapi_url="/openapi.json" if _serve_schema() else None,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # In dev, accept any localhost port so the web dev server works whatever port it grabs.
        allow_origin_regex=r"http://localhost:\d+" if not settings.is_prod else None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth_router)
    app.include_router(orgs_router)
    app.include_router(credentials_router)
    app.include_router(agents_router)
    app.include_router(agent_templates_router)
    app.include_router(agent_tests_router)
    app.include_router(agent_test_runs_by_agent_router)
    app.include_router(agent_test_run_router)
    app.include_router(knowledge_router)
    app.include_router(conversations_router)
    app.include_router(tools_router)
    app.include_router(mcp_router)
    app.include_router(agent_workflows_router)
    app.include_router(workflows_router)
    app.include_router(workflow_runs_router)
    app.include_router(workflow_tests_router)
    app.include_router(workflow_test_runs_by_workflow_router)
    app.include_router(workflow_test_run_router)
    app.include_router(public_router)
    app.include_router(channels_router)
    app.include_router(inbox_router)
    app.include_router(analytics_router)
    app.include_router(billing_router)
    app.include_router(apikeys_router)
    app.include_router(canned_responses_router)
    app.include_router(macros_router)
    app.include_router(macros_inbox_router)
    app.include_router(contacts_router)
    app.include_router(help_articles_router)
    app.include_router(campaigns_router)
    app.include_router(webhooks_router)
    app.include_router(audit_router)
    app.include_router(admin_router)

    return app


app = create_app()
