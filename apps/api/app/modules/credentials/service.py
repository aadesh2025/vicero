"""Provider-credential service (BYO keys), encrypted at rest."""

from __future__ import annotations

import asyncio
import re
import uuid
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rbac
from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.errors import AppError
from app.core.ssrf import is_blocked_destination
from app.llm import catalog
from app.llm.base import ProviderError
from app.llm.catalog import ModelSpec, ProviderSpec
from app.llm.registry import PROVIDER_CATALOG, build_chat_provider, env_key_for, resolve_credential
from app.llm.types import ChatRequest, Message, ToolSpec
from app.models import ProviderCredential
from app.modules.credentials import schemas
from app.modules.orgs.deps import OrgContext

__all__ = [
    "PROVIDER_CATALOG",
    "create_credential",
    "delete_credential",
    "delete_provider_key",
    "list_credentials",
    "list_provider_models",
    "list_providers",
    "test_credential",
    "update_credential",
    "upsert_provider_key",
]

# Model ids a chat agent can never be pointed at, filtered out of *live* discovery. Providers
# return one flat list for every modality, so an unfiltered OpenAI or Gemini response offers
# embedding and speech models in the builder's model dropdown.
#
# This is necessarily incomplete — the id is the only signal the /models response gives us, and
# a vendor can name an audio model anything (`canopylabs/orpheus-*` on Groq was found this way,
# during live verification, after passing every marker below). It removes the common noise; it
# is not a guarantee that every listed model can hold a conversation.
_NON_CHAT_MARKERS = (
    "embed",
    "whisper",
    "tts",
    "orpheus",
    "dall-e",
    "imagen",
    "moderation",
    "rerank",
    "guard",
    "stable-diffusion",
    "aqa",
)


def _mask(key: str) -> str:
    return "••••••••" + key[-4:] if len(key) > 4 else "••••"


def _out(cred: ProviderCredential) -> schemas.CredentialOut:
    masked = _mask(decrypt(cred.api_key_enc)) if cred.api_key_enc else "••••"
    return schemas.CredentialOut(
        id=cred.id,
        provider=cred.provider,
        label=cred.label,
        masked_key=masked,
        base_url=cred.base_url,
        is_default=cred.is_default,
        created_at=cred.created_at,
    )


def _require_spec(provider: str) -> ProviderSpec:
    spec = catalog.get_provider(provider)
    if spec is None:
        raise AppError("credentials.unknown_provider", f"Unknown provider '{provider}'.", 400)
    return spec


def _model_out(spec: ModelSpec, *, free: bool) -> schemas.ModelOut:
    return schemas.ModelOut(
        id=spec.id,
        label=spec.label,
        context=spec.context,
        tools=spec.tools,
        note=spec.note,
        pricing_known=free or (spec.prompt_micros is not None and spec.completion_micros is not None),
    )


async def list_credentials(session: AsyncSession, ctx: OrgContext) -> list[schemas.CredentialOut]:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    return [_out(c) for c in await _org_credentials(session, ctx.org.id)]


async def _org_credentials(session: AsyncSession, org_id: uuid.UUID) -> list[ProviderCredential]:
    """Every org-level (non agent-scoped) credential, newest first."""
    stmt = (
        select(ProviderCredential)
        .where(ProviderCredential.organization_id == org_id, ProviderCredential.agent_id.is_(None))
        .order_by(ProviderCredential.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _clear_default(session: AsyncSession, org_id: uuid.UUID, provider: str) -> None:
    stmt = select(ProviderCredential).where(
        ProviderCredential.organization_id == org_id,
        ProviderCredential.provider == provider,
        ProviderCredential.agent_id.is_(None),
        ProviderCredential.is_default.is_(True),
    )
    for c in (await session.execute(stmt)).scalars().all():
        c.is_default = False


async def _check_base_url(base_url: str | None) -> None:
    """Refuse an endpoint URL that would make the API host call something it should not.

    The request-time guard in `llm/openai_compatible.py` is the real boundary (it also covers
    rows saved before this check and DNS that changes later); this gives the editor an error at
    save time instead of a failed chat turn. A `?` or `#` is refused because the provider appends
    `/models` or `/chat/completions` to this URL: a trailing `?` turns that suffix into a query
    string and hands the caller control of the whole path.
    """
    url = (base_url or "").strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError("credentials.base_url_invalid", "The endpoint must be an http(s) URL.", 422)
    if parsed.query or parsed.fragment or "?" in url or "#" in url:
        raise AppError("credentials.base_url_invalid", "The endpoint URL cannot contain ? or #.", 422)
    if await asyncio.to_thread(is_blocked_destination, parsed.hostname, settings.provider_private_hosts):
        raise AppError(
            "credentials.base_url_blocked",
            "That endpoint is a private, loopback or unresolvable address. Ask the platform "
            "operator to allow it (PROVIDER_PRIVATE_HOSTS) or use a public URL.",
            422,
        )


async def create_credential(
    session: AsyncSession, ctx: OrgContext, data: schemas.CredentialCreate
) -> schemas.CredentialOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    spec = _require_spec(data.provider)
    if spec.base_url_required and not (data.base_url or "").strip():
        raise AppError(
            "credentials.base_url_required", f"{spec.label} needs the URL of your endpoint.", 400
        )
    await _check_base_url(data.base_url)
    if data.is_default:
        await _clear_default(session, ctx.org.id, data.provider)
    cred = ProviderCredential(
        organization_id=ctx.org.id,
        provider=data.provider,
        label=data.label,
        api_key_enc=encrypt(data.api_key),
        base_url=data.base_url,
        is_default=data.is_default,
        created_by=ctx.user.id,
    )
    session.add(cred)
    await session.flush()
    return _out(cred)


async def upsert_provider_key(
    session: AsyncSession, ctx: OrgContext, provider: str, data: schemas.ProviderKeyUpsert
) -> schemas.CredentialOut:
    """Save the org's key for one provider, replacing it if there already is one.

    This is what the Settings page calls. Keeping it to one row per provider means the key an
    operator can see is unambiguously the key their agents run on — with several rows,
    `resolve_credential()` picks the default (or the oldest) and the UI would be showing a
    credential that may not be the one in use.
    """
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    spec = _require_spec(provider)

    key = (data.api_key or "").strip()
    base_url = (data.base_url or "").strip() or None

    existing = [c for c in await _org_credentials(session, ctx.org.id) if c.provider == provider]
    cred = next((c for c in existing if c.is_default), existing[0] if existing else None)

    # An omitted key is only an error when there is nothing stored to fall back on: the form
    # renders the existing key masked, so editing just the label must not require re-typing a
    # secret the operator can no longer read.
    if spec.requires_key and not key and (cred is None or cred.api_key_enc is None):
        raise AppError("credentials.api_key_required", f"{spec.label} requires an API key.", 400)
    if spec.base_url_required and not base_url:
        raise AppError(
            "credentials.base_url_required", f"{spec.label} needs the URL of your endpoint.", 400
        )
    await _check_base_url(base_url)

    if cred is None:
        cred = ProviderCredential(
            organization_id=ctx.org.id,
            provider=provider,
            label=data.label,
            api_key_enc=encrypt(key) if key else None,
            base_url=base_url,
            is_default=True,
            created_by=ctx.user.id,
        )
        session.add(cred)
    else:
        # An empty api_key on an existing credential means "leave the stored key alone" — the
        # form shows a masked value, so submitting it unchanged must not wipe a working key.
        if key:
            cred.api_key_enc = encrypt(key)
        cred.base_url = base_url
        if data.label is not None:
            cred.label = data.label
        cred.is_default = True

    await _clear_default(session, ctx.org.id, provider)
    cred.is_default = True
    await session.flush()
    return _out(cred)


async def _get_owned(session: AsyncSession, ctx: OrgContext, cred_id: uuid.UUID) -> ProviderCredential:
    cred = await session.get(ProviderCredential, cred_id)
    if cred is None or cred.organization_id != ctx.org.id:
        raise AppError("credentials.not_found", "Credential not found.", 404)
    return cred


async def update_credential(
    session: AsyncSession, ctx: OrgContext, cred_id: uuid.UUID, data: schemas.CredentialUpdate
) -> schemas.CredentialOut:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    cred = await _get_owned(session, ctx, cred_id)
    if data.label is not None:
        cred.label = data.label
    if data.base_url is not None:
        await _check_base_url(data.base_url)
        cred.base_url = data.base_url
    if data.api_key is not None:
        cred.api_key_enc = encrypt(data.api_key)
    if data.is_default is not None:
        if data.is_default:
            await _clear_default(session, ctx.org.id, cred.provider)
        cred.is_default = data.is_default
    return _out(cred)


async def delete_credential(session: AsyncSession, ctx: OrgContext, cred_id: uuid.UUID) -> None:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    cred = await _get_owned(session, ctx, cred_id)
    await session.delete(cred)


async def delete_provider_key(session: AsyncSession, ctx: OrgContext, provider: str) -> None:
    """Remove every org-level credential for one provider (the Settings 'Remove' action)."""
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    _require_spec(provider)
    for cred in await _org_credentials(session, ctx.org.id):
        if cred.provider == provider:
            await session.delete(cred)


async def test_credential(
    session: AsyncSession, ctx: OrgContext, cred_id: uuid.UUID
) -> schemas.CredentialTestResult:
    rbac.require_permission(ctx.role, rbac.TOOLS_MANAGE)
    cred = await _get_owned(session, ctx, cred_id)
    api_key = decrypt(cred.api_key_enc) if cred.api_key_enc else None
    provider = build_chat_provider(cred.provider, api_key=api_key, base_url=cred.base_url)
    try:
        models = await provider.list_models()
    except ProviderError as exc:
        status = "invalid_key" if exc.status in (401, 403) else (
            "unreachable" if exc.status is None or exc.status >= 500 else "chat_unavailable"
        )
        return schemas.CredentialTestResult(
            ok=False, error=_safe_error(exc), status=status
        )

    if not models:
        return schemas.CredentialTestResult(
            ok=False, models=[], error="Provider returned no chat models.", status="chat_unavailable"
        )

    chat_models = [model for model in models if _is_chat_model(model.id)]
    if not chat_models:
        return schemas.CredentialTestResult(
            ok=False, models=[], error="Provider returned no chat models.", status="chat_unavailable"
        )
    model = chat_models[0]
    request = ChatRequest(
        model=model.id,
        messages=[Message(role="user", content="Reply with OK.")],
        max_tokens=8,
    )
    try:
        await provider.chat(request)
    except ProviderError as exc:
        status = "invalid_key" if exc.status in (401, 403) else (
            "unreachable" if exc.status is None or exc.status >= 500 else "chat_unavailable"
        )
        return schemas.CredentialTestResult(
            ok=False, models=[m.id for m in chat_models], chat_ok=False,
            error=_safe_error(exc), status=status,
        )

    tools_ok: bool | None = None
    capability = catalog.tool_capability(cred.provider, model.id)
    if model.tools is True or capability is True:
        try:
            await provider.chat(
                request.model_copy(
                    update={
                        "tools": [
                            ToolSpec(
                                name="health_check",
                                description="A no-op capability check.",
                                parameters={"type": "object", "properties": {}},
                            )
                        ],
                        "tool_choice": "none",
                    }
                )
            )
            tools_ok = True
        except ProviderError as exc:
            tools_ok = False
            return schemas.CredentialTestResult(
                ok=False,
                models=[m.id for m in chat_models],
                chat_ok=True,
                tools_ok=False,
                error=_safe_error(exc),
                status="tools_unsupported",
            )
    return schemas.CredentialTestResult(
        ok=True,
        models=[m.id for m in chat_models],
        chat_ok=True,
        tools_ok=tools_ok,
        status="ok",
    )


async def list_providers(session: AsyncSession, ctx: OrgContext) -> list[schemas.ProviderInfo]:
    """The full catalog, each entry annotated with whether *this org* can run it.

    `configured` is what the builder's Model tab filters on, so it has to answer the real
    question — "will a turn on this provider work?" — not just "is there a row in the
    credentials table". A deployment-wide env key counts (that is how the platform's own Groq
    agents run today, with no credential row at all), and a provider needing no key counts too.
    """
    rbac.require_permission(ctx.role, rbac.READ)
    # Every role needs to know *which* providers are usable — the builder's model picker is
    # gated on `agents:write`, not on key management — but the key fragment itself stays with
    # the roles that manage keys.
    can_see_keys = rbac.has_permission(ctx.role, rbac.TOOLS_MANAGE)
    by_provider: dict[str, ProviderCredential] = {}
    for row in await _org_credentials(session, ctx.org.id):
        current = by_provider.get(row.provider)
        if current is None or (row.is_default and not current.is_default):
            by_provider[row.provider] = row

    out: list[schemas.ProviderInfo] = []
    for spec in catalog.PROVIDERS:
        cred = by_provider.get(spec.name)
        masked: str | None = None
        key_source: schemas.KeySource
        if cred is not None and (cred.api_key_enc or not spec.requires_key):
            key_source = "org"
            if can_see_keys and cred.api_key_enc:
                masked = _mask(decrypt(cred.api_key_enc))
        elif env_key_for(spec.name):
            key_source = "env"
        elif not spec.requires_key and not spec.base_url_required:
            key_source = "not_required"
        else:
            key_source = "none"

        out.append(
            schemas.ProviderInfo(
                name=spec.name,
                label=spec.label,
                free=spec.free,
                requires_key=spec.requires_key,
                models=[m.id for m in spec.models],
                available_models=[_model_out(m, free=spec.free) for m in spec.models],
                configured=key_source != "none",
                key_source=key_source,
                masked_key=masked,
                credential_id=cred.id if cred is not None else None,
                base_url=(cred.base_url if cred is not None else None) or spec.base_url,
                base_url_required=spec.base_url_required,
                api_key_url=spec.api_key_url,
                key_hint=spec.key_hint,
                description=spec.description,
            )
        )
    return out


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


def _safe_error(exc: Exception) -> str:
    """A provider failure the operator can act on, without echoing key material.

    Upstream 401 bodies quote the rejected key back at you — OpenAI returns
    `Incorrect API key provided: sk-live-*******8888`. Partially masked is still a fragment of
    a live secret in an API response and a log line, and the body can carry account ids too, so
    key-shaped runs are stripped and the rest is truncated. The status and reason survive,
    which is the part that tells someone what to fix.
    """
    text = _KEYISH.sub("[key]", str(exc))
    return text[:200].strip()


# Deliberately looser than `guardrails._SECRET_PATTERNS`, which matches whole live keys: this
# has to catch the *masked* forms providers echo back (`sk-live-*******8888`).
_KEYISH = re.compile(r"\b(sk|gsk|xai|sk-ant|sk-or)[-_][A-Za-z0-9*_\-]{4,}", re.I)


async def list_provider_models(
    session: AsyncSession, ctx: OrgContext, provider: str
) -> schemas.ProviderModels:
    """Ask the provider what it can actually run, falling back to the static catalog.

    The catalog's model lists go stale on the vendor's schedule, not ours (Groq retired
    `mixtral-8x7b-32768` while it was still listed). Where a key exists we can just ask. A
    failure here is not an error state for the caller: an unreachable provider still has to
    render a usable dropdown, so the fallback list ships with `source="catalog"` and the reason
    in `error` rather than a 5xx.
    """
    rbac.require_permission(ctx.role, rbac.READ)
    spec = _require_spec(provider)
    seed = {m.id: _model_out(m, free=spec.free) for m in spec.models}
    fallback = list(seed.values())

    api_key, base_url = await resolve_credential(session, ctx.org.id, provider)
    if spec.requires_key and not api_key:
        return schemas.ProviderModels(
            provider=provider, source="catalog", models=fallback, error="No API key configured."
        )

    try:
        client = build_chat_provider(provider, api_key=api_key, base_url=base_url)
        discovered = await client.list_models()
    except (ProviderError, AppError) as exc:
        return schemas.ProviderModels(
            provider=provider, source="catalog", models=fallback, error=_safe_error(exc)
        )

    models = [
        seed.get(
            m.id,
            schemas.ModelOut(id=m.id, label=m.id, tools=m.tools, pricing_known=spec.free),
        )
        for m in discovered
        if _is_chat_model(m.id)
    ]
    if not models:
        return schemas.ProviderModels(
            provider=provider, source="catalog", models=fallback, error="Provider returned no models."
        )
    return schemas.ProviderModels(provider=provider, source="live", models=models)
