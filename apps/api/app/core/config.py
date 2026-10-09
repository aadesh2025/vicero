"""Application settings, loaded from environment / .env (see docs/ENV.md)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_comment_only(value: str) -> bool:
    """True when a raw env value is nothing but an unfilled placeholder comment.

    `.env.example` documents unset variables as `KEY=<spaces># [HUMAN] note`. python-dotenv
    strips a trailing comment only when the value has *something* before it — its rule is
    `re.sub(r"\\s+#.*", "", value)`, which needs whitespace ahead of the `#`. On a blank line
    the spaces after `=` are already eaten as the separator, so the `#` lands at position 0,
    the rule can't match, and the comment text becomes the value. `KEY=dev  # note` is
    unaffected (it parses to `dev`), which is why this went unnoticed for so long.
    """
    return value.lstrip().startswith("#")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "info"
    secret_key: str = Field(default="dev-insecure-change-me")
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # --- Datastores (compose defaults; override via env) ---
    database_url: str = "postgresql+asyncpg://vicero:vicero@localhost:5432/vicero"
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM providers (free-first). None = feature stubbed, logged, skipped. ---
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    # Comma-separated hostnames/IPs a tenant-supplied provider `base_url` (Custom endpoint,
    # Ollama override, or a catalog provider's override) may point at even though they resolve
    # to a private/loopback address, e.g. "host.docker.internal,10.0.0.5". Empty = tenants can
    # only reach public endpoints. Operator-only: it is environment config, never a tenant
    # setting. Does not affect `OLLAMA_BASE_URL`, which is trusted platform config.
    provider_private_hosts: str = ""
    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"
    # One HTTP GET at startup, confirming the configured embedding endpoint answers AND has the
    # model pulled (docs/15 PROD-2). It exists because production pointed at an `ollama` service
    # that was never declared: every ingest failed and no configuration looked wrong. Capped at a
    # couple of seconds, never fails startup. Off in the test suite — a diagnostic must not be
    # the thing that makes the suite do network I/O.
    embedding_probe_enabled: bool = True

    # --- Knowledge base / RAG ---
    # Directory for uploaded/ingested document files (created on demand).
    upload_dir: str = "./var/uploads"
    # Ceiling on characters of retrieved context injected into a prompt (token budgeting).
    rag_context_char_budget: int = 8000
    # Weight of the keyword (Postgres FTS) list in the hybrid RRF fusion; the dense list is
    # always 1.0. Textbook RRF weights every list equally, which assumes the two retrievers are
    # comparable. Measured on the frozen eval corpus they are not — dense NDCG@10 0.898 against
    # keyword 0.660 — and equal weighting dragged hybrid to 0.816, i.e. *below* dense-only.
    #
    # 0.05 is the highest weight at which hybrid does not regress against dense-only on the
    # only evidence there is (ADR-058 records the full sweep, including what it does NOT
    # establish). Deliberately conservative rather than tuned until it looked good: the seed
    # corpus is 36 short documents and cannot show where keyword search earns its keep. Re-fit
    # it with `make eval-retrieval-full` against a real client KB before raising it.
    rag_rrf_fts_weight: float = 0.05

    # --- Docling ingestion (docs/14 K1) ---
    # Layout-aware extraction, reading order, table structure and OCR, replacing the flat
    # `pypdf` text stream. Runs in the Celery worker, so it cannot touch the p50 first-token
    # budget — the risk here is a bad extraction, not a slow chat.
    #
    # OFF by default and rolled out per docs/14 §12. `LegacyConverter` is never deleted, so
    # every step back is a flag flip. Any Docling failure falls back to it rather than failing
    # the document: a client must never be told their upload is broken because an internal
    # service was down.
    docling_enabled: bool = False
    # A `docling-serve` base URL. INTERNAL ONLY — do not publish its port (docs/14 §9): it
    # accepts arbitrary documents and URLs, so a public port is SSRF plus resource exhaustion.
    # Enabled with this empty warns at startup and silently takes the legacy path.
    docling_endpoint: str = ""
    # Turns a scanned PDF from `LoaderError: no extractable text` into a working document.
    docling_do_ocr: bool = True
    # Reconstructs row/column relationships. Without it a pricing table becomes word soup and
    # numbers lose their row — the content shape that produces confidently wrong price answers.
    docling_do_table_structure: bool = True
    # Docling's own guidance is 90-120s. Ingest is a background job, so this buys correctness
    # on a large document rather than costing anyone latency.
    docling_timeout_seconds: float = 120.0
    # Token budget per chunk on the Docling path (docs/14 K2). Tokens, not characters: the
    # structural chunker splits on the tokenizer, which is the whole point — `chunk_size`'s
    # 1000 characters means ~250 tokens of English and ~800 of Tamil, and only one of those
    # fits an embedding window predictably.
    #
    # 512 targets the retrieval sweet spot rather than the model's ceiling. A chunk large
    # enough to hold three topics retrieves for all three and answers none of them well;
    # `merge_peers` is what stops the opposite failure of one-sentence fragments.
    docling_chunk_max_tokens: int = 512
    # Where the chunk's heading path goes: `embed` (embedding input only, docs/14 §4.2's rule)
    # or `inline` (also prefixed to the stored chunk). Measured, not chosen on principle — see
    # ADR-065 and the K2-5 table in docs/14. The short version: `embed` improves the dense half
    # and *removes* the heading from the FTS-indexed text, so it makes the keyword half worse.
    docling_chunk_heading_mode: str = "embed"
    # Page cap for PDF ingest (docs/14 K5-2). 0 disables it.
    #
    # A 500-page PDF does not fail cleanly, it fails *slowly*: it occupies the worker for the
    # whole `DOCLING_TIMEOUT_SECONDS`, times out, falls back to the legacy extractor, and the
    # client gets a document some minutes later with none of the structure they uploaded it for.
    # Refusing up front with a message that names the number is a better answer than a timeout,
    # because "split it into parts" is an action the client can actually take.
    #
    # 800 rather than something tighter: this is a backstop against the pathological case, not
    # a product limit. A real client manual runs to a few hundred pages and must still ingest.
    max_pdf_pages: int = 800

    # --- Reranking (docs/13 R1, docs/14 K4) ---
    # Stage 4: a cross-encoder reorders the fused RRF candidates. OFF platform-wide and off per
    # agent, because it costs a network round trip *before* generation starts — straight out of
    # the NFR-1 p50 417 ms first-token budget. Enabling it is a latency decision, not a default
    # anyone should inherit. Fails open to RRF ordering (ADR-051's rule, restated in ADR-063).
    rerank_enabled: bool = False
    # A `/rerank` endpoint. Self-hosted text-embeddings-inference is the recommended shape
    # (docs/14 §5.3): client KB text never leaves the deployment, and bge-reranker-v2-m3 is
    # trained multilingual, which matters where Tamil is a first language (docs/11 §9.2a).
    # Empty with rerank_enabled=true is a misconfiguration and warns at startup.
    rerank_endpoint: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # PLATFORM credential, never the org's (ADR-063). A self-hosted TEI container needs none.
    rerank_api_key: str | None = None
    # How many fused candidates to rerank. The cookbook uses 50; more candidates is more recall
    # for the cross-encoder to rescue, at a linear cost in the reranker.
    rerank_candidate_k: int = 50
    # Availability budget. Wider than L2's 300ms because a cross-encoder over 50 passages is
    # real work, but still bounded: on timeout the turn proceeds on RRF ordering.
    rerank_timeout_ms: int = 800

    # Run Celery tasks inline (no broker/worker) — handy in dev/tests. Off in prod.
    celery_task_always_eager: bool = False
    # Force every chat + embedding call onto the deterministic Fake provider,
    # regardless of the agent's configured provider. Test/E2E only — lets CI run
    # the full product flows with no paid keys and no local model pulls. Never in prod.
    llm_force_fake: bool = False

    # --- Chat runtime / memory ---
    # How many recent turns (user+assistant messages) to keep verbatim in the prompt.
    memory_window_messages: int = 12
    # Once a conversation exceeds this many messages, older turns are summarized.
    memory_summary_threshold: int = 24
    # Summarizer provider/model — deliberately a small/fast model, NOT the agent's model
    # (so a heavy local model like qwen3:14b never gets used for background summaries).
    summary_provider: str = "groq"
    summary_model: str = "openai/gpt-oss-20b"

    # --- Guardrails (docs/11) ---
    # Hard ceiling on a single visitor message. Also the LLM10 unbounded-consumption control:
    # without it, one caller can push an arbitrarily large prompt through a paid provider.
    max_user_message_chars: int = 8000
    # L1 static pre-filter on the visitor's own message (direct prompt injection, OWASP LLM01).
    # Off means Vicero defends retrieved content but not the person typing — the docs/11 §1.1
    # asymmetry. Kept switchable because a false positive costs a real customer a real answer.
    guard_input_enabled: bool = True
    # L5 output guardrail: system-prompt leakage + persona breaks.
    guard_output_enabled: bool = True
    # Fraction of the assembled system prompt's 8-grams that may appear in a reply before it
    # is treated as leakage. Low enough to catch paraphrase-free quoting, high enough that a
    # reply legitimately reusing the agent's own vocabulary is not suppressed.
    guard_output_leak_threshold: float = 0.35
    # L5 PII egress: redact emails/phones from a reply unless the org allowlisted them.
    guard_pii_egress_enabled: bool = True
    # Regions used to read phone numbers written *without* a country code. Numbers written
    # with an explicit +CC are found regardless. Comma-separated ISO codes, most likely first.
    guard_pii_phone_regions: str = "IN,US,GB"
    # Street-address detection is materially less precise than email/phone ("12 Month Plan"
    # reads as a house number), so it ships flag-only: counted in a document's `pii_flags`,
    # never redacted from a reply, until the false-positive rate is measured on real traffic.
    guard_pii_redact_addresses: bool = False

    # --- L2 injection classifier (docs/11 §4-L2, Phase C) ---
    # Catches what the L1 regexes structurally cannot: paraphrase, and every language other
    # than English. Measured on this deployment, L1 scores 0/6 on both.
    guard_injection_enabled: bool = True
    # Model id, never inline: Groq deprecated `llama-guard-4-12b` in Feb 2026 and this repo
    # shipped a stale `mixtral-8x7b-32768` for months. Priced in `llm/catalog.GUARD_MODELS`.
    guard_injection_model: str = "meta-llama/llama-prompt-guard-2-86m"
    # The model returns a probability in [0,1]. Measured separation on real traffic shapes is
    # enormous — benign < 0.005, attacks > 0.998 — so 0.5 sits in empty space and the exact
    # value is not delicate. Raise it if a client's phrasing trips it.
    guard_injection_threshold: float = 0.5
    # Availability budget. A guard that is slow must not make the product slow: on timeout the
    # turn proceeds unguarded (fail open, ADR-051) with a loud log and a metric.
    guard_injection_timeout_ms: int = 300
    # Repeat probes are free. Keyed on sha256 of the normalised text, so an attacker retrying
    # the same payload costs one call, not one per attempt.
    guard_injection_cache_ttl_seconds: int = 3600

    # --- L3 policy & emotion classifier (docs/11 §4-L3, Phase E) ---
    # Grades each customer message against the markdown in `app/chat/policies/` — abuse,
    # off-topic, PII request, and a four-level distress read that drives the attention queue.
    guard_policy_enabled: bool = True
    guard_policy_model: str = "openai/gpt-oss-safeguard-20b"
    # It is a reasoning model (~200-300ms observed), so the budget is wider than L2's.
    guard_policy_timeout_ms: int = 1200
    # Distress detection is a triage aid, never a clinical instrument. `crisis` suppresses the
    # bot's reply entirely, so it must stay rare; this switch exists to turn the whole layer
    # off for a client who has not agreed to watch the queue it fills (docs/11 §9).
    guard_distress_enabled: bool = True

    # --- Web access (docs/11 §L7, Phase G) ---
    # OFF by default, platform-wide, and per-agent off as well. This spends money and reaches
    # the open internet on a customer's behalf, so it is a per-client decision.
    web_search_enabled: bool = False
    web_search_api_key: str | None = None
    web_search_endpoint: str = "https://api.tavily.com/search"
    web_search_timeout_seconds: float = 8.0
    # Per-org monthly ceiling (OWASP LLM10, unbounded consumption).
    web_search_monthly_quota: int = 1000

    # --- Tools (Phase 9) ---
    # Max tool-call iterations per turn before the runtime forces a final answer.
    tool_max_iterations: int = 4
    # Per-tool execution timeout (seconds) for HTTP / built-in tools.
    tool_timeout_seconds: float = 15.0

    # --- Agentic runtime (docs/17 Phase 1, ADR-070/ADR-073) ---
    # Platform-wide gate. Deliberately off-by-default at BOTH levels — unlike
    # guard_injection_enabled's "on unless an org opts out" polarity, this switch and
    # Organization.agentic_loop_enabled must *both* be explicitly true before any turn uses
    # the budgeted multi-step loop or MCP tools (see app.chat.budget.agentic_loop_enabled).
    # This is new attack surface (docs/17 §2) and new cost exposure, so nothing is on by a
    # single flip either way.
    agentic_loop_enabled: bool = False
    # docs/17 §5 defaults, shipped as one global default (ADR-073): no free/paid tiering yet
    # — max_cost_usd alone already bounds a trial org's worst case per turn.
    agentic_max_steps: int = 5
    agentic_max_tool_calls: int = 5
    agentic_max_runtime_s: float = 30.0
    agentic_max_cost_usd: float = 0.05
    # MCP calls involve a subprocess spawn or a network round trip to a third-party server,
    # materially slower than an HTTP/builtin tool — a separate, wider timeout rather than
    # sharing tool_timeout_seconds.
    mcp_tool_timeout_seconds: float = 20.0
    # docs/17 Phase 2 gap closure: how deep a chain of `sub_agent` workflow nodes may nest
    # before AgentBudget.call_depth trips it (ADR — see docs/DECISIONS.md). Bounds a direct
    # self-call or an indirect cycle (A→B→A) the same way, since depth doesn't care which.
    workflow_max_call_depth: int = 5
    # Longest a `delay` node may schedule a real wait for (default 24h). Rejected loudly above
    # this rather than silently clamped — a workflow author configuring an absurd wait should
    # see an error, not a truncated one.
    workflow_max_delay_seconds: float = 86400.0

    # How often (seconds) the Celery beat sweep re-enqueues due `pending` webhook deliveries.
    webhook_sweep_interval_seconds: float = 60.0

    # --- n8n ---
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str | None = None
    n8n_webhook_signing_secret: str | None = None
    # Refuse to bind an n8n workflow as a tool unless its JSON shows the Vicero signature check
    # in the request path (RISK-REGISTER R15, `app.integrations.n8n_signature`). On by default;
    # a pasted webhook URL is resolved to its workflow via the n8n API, so with no `N8N_API_KEY`
    # (or a URL that matches nothing) it cannot be verified and is refused. Turn off only for a
    # deliberately unsigned dev n8n.
    n8n_require_signature_check: bool = True
    # Client-visible automations (docs/26). `AUTOMATION_REPORT_SECRET` signs the run reports n8n pushes to
    # POST /internal/automations/runs (HMAC-SHA256 over "<timestamp>.<raw body>"). Unset = that endpoint answers
    # 503 and nothing is recorded: it never falls back to another secret.
    automation_report_secret: str | None = None
    # An n8n tool may be bound to / run a webhook only if it belongs to a registered, Active automation of the
    # same org (and agent). Off only for keyless CI and dev, where no registry exists.
    n8n_require_registered_automation: bool = True
    # Celery beat job that fills gaps in the run log from the n8n API (registered workflows only).
    automation_pull_enabled: bool = True

    # --- Auth / rate limiting ---
    auth_rate_limit: int = 30
    auth_rate_window: int = 60  # seconds
    oauth_redirect_base: str = "http://localhost:8000"

    # --- OAuth (None = provider hidden/denied) ---
    google_client_id: str | None = None
    google_client_secret: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    facebook_client_id: str | None = None
    facebook_client_secret: str | None = None

    # --- Self-serve signup + free trial (docs/18, ADR-088) ---
    # Master switch. On: signing up provisions one 10-day trial workspace and non-staff users
    # may own one. Off: the previous staff-provisioned-only behaviour, untouched — also the
    # operator's kill switch if signups are being abused. Plan *limits* are not settings; they
    # live in app/core/plans.py so there is exactly one place to read them.
    self_serve_enabled: bool = True
    # Reverse proxies allowed to tell us a visitor's real address in `X-Forwarded-For` (comma-separated
    # IPs / CIDRs; see app/core/clientip.py). The header is ignored from any other peer. The default is
    # this machine, which covers the web app running beside the API in dev; when both run in Docker
    # add the compose network (the compose files do). Never put a public range here.
    trusted_proxies: str = "127.0.0.1,::1"
    # Display-only geo currency (docs/22 §3, ADR-106). The country a visitor appears to be in picks which
    # price list the pricing page SHOWS — never what is charged or which plan an org gets. Headers are
    # read only when TRUST_GEO_HEADERS is true, which is safe only when the API is reachable solely
    # through a proxy that sets/overwrites one of them (docs/ENV.md).
    trust_geo_headers: bool = False
    # Offline IP -> country database (MaxMind .mmdb format; DB-IP "country lite" works and needs no key).
    # Empty = off. Used only when no trusted header gave a country, and only for DISPLAY (ADR-112).
    # Fetch with `python scripts/fetch_geoip_db.py`.
    geoip_db_path: str = ""
    # The one Meta app Vicero owns (docs/26-META-ONE-CLICK-CONNECT.md, ADR-113). The secret signs/verifies the
    # shared webhook (`/api/meta/webhook`), the Data Deletion and Deauthorize callbacks, and the server-side
    # token exchange. One-click connect is "enabled" only when app id + secret + verify token are all set;
    # WhatsApp additionally needs the Embedded Signup configuration id. Per-channel `app_secret` (the manual
    # flow's own webhooks) is unaffected.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_verify_token: str = ""
    meta_embedded_signup_config_id: str = ""
    # One place for the Graph API version used by the channel adapters and the connect flow.
    meta_graph_version: str = "v23.0"
    # False until Meta has approved the app: the UI then tells customers only listed testers can connect.
    meta_app_live: bool = False
    geo_country_headers: str = "CF-IPCountry,X-Vercel-IP-Country,CloudFront-Viewer-Country,X-Country-Code"
    # Abuse controls. Per-IP cap on new self-serve accounts per 24h; 0 disables.
    signups_per_ip_per_day: int = 3
    # Refuse throwaway-mailbox domains (modules/auth/policy.py) so one person cannot mint
    # unlimited trials. On by default; turn off for an internal deployment.
    block_disposable_emails: bool = True
    # Where the maintained throwaway-domain list comes from (refreshed weekly by Celery beat into
    # Redis; the short built-in set in modules/auth/policy.py is the offline fallback). A soft signal
    # only — email verification is the real control (modules/auth/disposable.py, ADR-091).
    disposable_list_url: str = (
        "https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/"
        "main/disposable_email_blocklist.conf"
    )
    # Look up a new address's mail servers and *record* (never block on) ones that don't exist or
    # belong to a listed throwaway service. Time-bounded and fail-open.
    disposable_mx_check_enabled: bool = True
    disposable_mx_timeout: float = 2.0
    # Failed password logins per email before that email is locked out for the window below.
    login_lockout_failures: int = 10
    login_lockout_window: int = 900  # seconds
    # Per-org ceiling on public chat messages per minute, so one bot cannot burn a whole
    # trial in seconds. Counted per organization, unlike the per-IP limit on the route.
    org_chat_rate_limit: int = 30
    org_chat_rate_window: int = 60  # seconds

    # --- Test-only switches (never enable in production) ---
    # Lets a non-staff user create an organization. Vicero is provisioned per client, so
    # production must leave this off — with it on, anyone who can reach /signup can hand
    # themselves a workspace. It exists because the test suites bootstrap a tenant per test
    # through the public API, the same reason `llm_force_fake` exists.
    allow_self_serve_orgs: bool = False

    # --- Email ---
    # `console` logs to an in-memory outbox (dev/test). `smtp` delivers for real through any
    # SMTP relay — Resend/Postmark/SendGrid/SES/Mailgun all speak it, so switching provider is
    # an env change, never a code change.
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_from: str = ""
    # How long a request will wait to hand an email to the queue before giving up on it.
    # Small on purpose: the queue being unreachable must not become the user's problem.
    email_enqueue_timeout_seconds: float = 2.0

    # --- Observability ---
    sentry_dsn: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _drop_placeholder_comments(cls, data: object) -> object:
        """An unfilled `.env` placeholder must read as *unset*, never as its own comment text.

        Root cause of a live outage (ADR-044): `GEMINI_API_KEY=<blank># [HUMAN] Google Gemini
        free tier` resolved to the literal string `# [HUMAN] Google Gemini free tier`, which is
        non-empty — so it sailed past every "is a key configured?" guard and was sent to Google
        as a real API key. Gemini rejected it, and the published agent answered every visitor
        with empty content and HTTP 200 for an unknown period. `SENTRY_DSN` failed the same way
        (`sentry_init_failed: Unsupported scheme ''`).

        Only a *comment-only* value is dropped, and dropping it lets the field's own default
        apply. Deliberately NOT "strip everything after the first `#`": values legitimately
        contain that character — `SECRET_KEY=abc#def`, a DB password, a URL fragment — and a
        blanket strip would silently truncate them, trading this bug for a worse one. The one
        false positive is a real secret that *starts* with `#`; it degrades to "not configured",
        which is loud and well-trodden, rather than to garbage-that-looks-configured.
        """
        if not isinstance(data, dict):
            return data
        return {k: v for k, v in data.items() if not (isinstance(v, str) and _is_comment_only(v))}

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _blank_port_is_the_default(cls, v: object) -> object:
        """`SMTP_PORT=` (blank, as shipped in .env.example) means "use the default", not a crash.

        Same blank-is-unset convention ADR-020 applied to env-provided provider keys — an
        unfilled placeholder must never stop the app booting.
        """
        return 587 if v is None or (isinstance(v, str) and not v.strip()) else v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
