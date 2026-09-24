"""The LLM provider + model catalog — one source of truth for the whole app.

Everything that needs to know "which providers exist and what can they run" reads this
module: `GET /v1/credentials/providers` (the Settings key manager), the builder's Model tab,
`build_chat_provider()`, and cost accounting.

Two kinds of provider live here:

- **native** — a hand-written adapter exists (`gemini`, `anthropic`) or the OpenAI-compatible
  base is subclassed with extra behaviour (`groq`, `openai`, `openrouter`, `ollama`).
- **openai_compatible** — the provider speaks the OpenAI wire format at a fixed `base_url`, so
  it needs *no adapter at all*: `build_chat_provider()` constructs `OpenAICompatibleProvider`
  straight from `spec.base_url`. Adding one of these is a single entry in this file.

**The model lists are a seed, not the truth.** Provider lineups change constantly (Groq
decommissioned `mixtral-8x7b-32768`; it was still listed here until 2026-08-03). Once an org
has a key, `GET /v1/credentials/providers/{name}/models` asks the provider itself and the UI
prefers that answer; these lists are what the dropdown shows *before* a key exists, and the
fallback when discovery fails. So a stale id here degrades to "one wrong option in a list that
is about to be replaced by the live one", never to a broken agent.

**Pricing is `None` when we do not know it**, never `0`. A paid model silently costing $0 is a
wrong number in the client's analytics, which is worse than an absent one — `price_for()`
returns zeros either way but `compute_cost_micros()` logs `pricing_unknown` so it is visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ProviderKind = Literal["native", "openai_compatible", "local"]


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One model an agent can be pointed at.

    `prompt_micros`/`completion_micros` are micros ($0.000001) per 1K tokens, matching
    `llm/pricing.py`. `None` means "not published here" — see the module docstring.
    """

    id: str
    label: str
    context: int | None = None
    tools: bool = True
    prompt_micros: int | None = None
    completion_micros: int | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    label: str
    free: bool
    requires_key: bool
    kind: ProviderKind
    models: tuple[ModelSpec, ...]
    #: Fixed OpenAI-compatible endpoint. Required for `kind="openai_compatible"`.
    base_url: str | None = None
    #: The operator must supply their own endpoint (nothing sensible can be defaulted).
    base_url_required: bool = False
    #: Where a human goes to mint a key — rendered as a link in the Settings dialog.
    api_key_url: str | None = None
    #: Shown as the input placeholder so a pasted key can be eyeballed for the right shape.
    key_hint: str | None = None
    description: str | None = None


# --------------------------------------------------------------------------------------
# Free / free-tier first (docs/02 ADR-003), then paid, then bring-your-own-endpoint.
# --------------------------------------------------------------------------------------

#: The model a new agent gets when the caller overrides nothing (`agents.service`,
#: `db.seed`). One name, one place: the previous default lived in three files and all three
#: went stale together. `tests/test_default_model.py` fails if this leaves the Groq list below,
#: and its opt-in live variant checks it against Groq's own `/models`.
DEFAULT_CHAT_MODEL = "openai/gpt-oss-120b"

_GROQ = ProviderSpec(
    name="groq",
    label="Groq",
    free=True,
    requires_key=True,
    kind="native",
    api_key_url="https://console.groq.com/keys",
    key_hint="gsk_…",
    description="Fastest free tier. The platform default.",
    models=(
        # Verified against `GET /openai/v1/models` with a live key on 2026-09-24, every one
        # tool-calling. The whole Llama family (3.1/3.3/4), Qwen3-32B, Kimi K2, DeepSeek R1
        # distill and Gemma2 are gone from that list — Groq retired `llama-3.3-70b-versatile` and
        # `llama-3.1-8b-instant` out from under the platform default (every new agent 404'd).
        # `DEFAULT_CHAT_MODEL` must stay the first entry.
        ModelSpec(DEFAULT_CHAT_MODEL, "GPT-OSS 120B", 128_000),
        ModelSpec("openai/gpt-oss-20b", "GPT-OSS 20B", 128_000),
        ModelSpec("qwen/qwen3.8-27b", "Qwen 3.8 27B", 128_000),
    ),
)

_GEMINI = ProviderSpec(
    name="gemini",
    label="Google Gemini",
    free=True,
    requires_key=True,
    kind="native",
    api_key_url="https://aistudio.google.com/apikey",
    key_hint="AIza…",
    description="Generous free tier; large context windows.",
    models=(
        ModelSpec("gemini-2.5-flash", "Gemini 2.5 Flash", 1_000_000),
        ModelSpec("gemini-2.5-pro", "Gemini 2.5 Pro", 1_000_000),
        ModelSpec("gemini-2.0-flash", "Gemini 2.0 Flash", 1_000_000),
        ModelSpec("gemini-1.5-flash", "Gemini 1.5 Flash", 1_000_000, note="legacy"),
        ModelSpec("gemini-1.5-pro", "Gemini 1.5 Pro", 2_000_000, note="legacy"),
    ),
)

_OPENROUTER = ProviderSpec(
    name="openrouter",
    label="OpenRouter",
    free=True,
    requires_key=True,
    kind="native",
    api_key_url="https://openrouter.ai/keys",
    key_hint="sk-or-…",
    description="One key, hundreds of models. Save the key, then refresh to list them all.",
    models=(
        ModelSpec("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B (free)", 128_000),
        ModelSpec("meta-llama/llama-3.1-70b-instruct:free", "Llama 3.1 70B (free)", 128_000),
        ModelSpec("google/gemini-2.0-flash-exp:free", "Gemini 2.0 Flash (free)", 1_000_000),
        # OpenRouter is free-tier *first*, not free-only: the `:free` ids above cost nothing,
        # but these route to paid upstreams and are billed. Rates live per model so
        # `price_for()` charges them correctly without the provider losing its free flag.
        ModelSpec("deepseek/deepseek-chat", "DeepSeek Chat", prompt_micros=270, completion_micros=1100),
        ModelSpec(
            "anthropic/claude-sonnet-4.5",
            "Claude Sonnet 4.5",
            prompt_micros=3000,
            completion_micros=15000,
        ),
        ModelSpec("openai/gpt-4o-mini", "GPT-4o mini", prompt_micros=150, completion_micros=600),
    ),
)

_OLLAMA = ProviderSpec(
    name="ollama",
    label="Ollama (local)",
    free=True,
    requires_key=False,
    kind="local",
    base_url="http://localhost:11434",
    api_key_url="https://ollama.com/download",
    description="Runs on your own machine. No key — just a reachable Ollama server.",
    models=(
        ModelSpec("llama3.1", "Llama 3.1"),
        ModelSpec("qwen2.5", "Qwen 2.5"),
        ModelSpec("qwen3", "Qwen 3"),
        ModelSpec("mistral", "Mistral"),
        ModelSpec("phi3", "Phi-3"),
        ModelSpec("gemma2", "Gemma 2"),
    ),
)

_OPENAI = ProviderSpec(
    name="openai",
    label="OpenAI",
    free=False,
    requires_key=True,
    kind="native",
    api_key_url="https://platform.openai.com/api-keys",
    key_hint="sk-…",
    models=(
        ModelSpec("gpt-4o", "GPT-4o", 128_000, prompt_micros=2500, completion_micros=10000),
        ModelSpec("gpt-4o-mini", "GPT-4o mini", 128_000, prompt_micros=150, completion_micros=600),
        ModelSpec("gpt-4.1", "GPT-4.1", 1_000_000, prompt_micros=2000, completion_micros=8000),
        ModelSpec("gpt-4.1-mini", "GPT-4.1 mini", 1_000_000, prompt_micros=400, completion_micros=1600),
        ModelSpec("gpt-4.1-nano", "GPT-4.1 nano", 1_000_000, prompt_micros=100, completion_micros=400),
        ModelSpec("o4-mini", "o4-mini (reasoning)", 200_000, prompt_micros=1100, completion_micros=4400),
    ),
)

_ANTHROPIC = ProviderSpec(
    name="anthropic",
    label="Anthropic",
    free=False,
    requires_key=True,
    kind="native",
    api_key_url="https://console.anthropic.com/settings/keys",
    key_hint="sk-ant-…",
    models=(
        ModelSpec("claude-opus-5", "Claude Opus 5", 200_000, prompt_micros=15000, completion_micros=75000),
        ModelSpec("claude-sonnet-5", "Claude Sonnet 5", 200_000, prompt_micros=3000, completion_micros=15000),
        ModelSpec(
            "claude-haiku-4-5-20251001",
            "Claude Haiku 4.5",
            200_000,
            prompt_micros=800,
            completion_micros=4000,
        ),
    ),
)

# --- OpenAI-compatible: no adapter needed, just an endpoint. ----------------------------
# These carry each provider's published list price, so switching an agent onto a paid model
# produces a real cost figure instead of a silent $0 (which reads as "free", not "unknown").
# Rates are list prices captured 2026-08-09 and are an *estimate*: they don't know about
# negotiated discounts, batch/cached-input tiers, or a provider changing its price. Anything
# left `None` still costs 0 and logs `pricing_unknown` — see `llm/pricing.py`.

_MISTRAL = ProviderSpec(
    name="mistral",
    label="Mistral AI",
    free=False,
    requires_key=True,
    kind="openai_compatible",
    base_url="https://api.mistral.ai/v1",
    api_key_url="https://console.mistral.ai/api-keys",
    models=(
        ModelSpec("mistral-large-latest", "Mistral Large", prompt_micros=2000, completion_micros=6000),
        ModelSpec("mistral-small-latest", "Mistral Small", prompt_micros=200, completion_micros=600),
        ModelSpec("open-mistral-nemo", "Mistral Nemo", prompt_micros=150, completion_micros=150),
    ),
)

_DEEPSEEK = ProviderSpec(
    name="deepseek",
    label="DeepSeek",
    free=False,
    requires_key=True,
    kind="openai_compatible",
    base_url="https://api.deepseek.com/v1",
    api_key_url="https://platform.deepseek.com/api_keys",
    models=(
        ModelSpec("deepseek-chat", "DeepSeek Chat", prompt_micros=270, completion_micros=1100),
        ModelSpec("deepseek-reasoner", "DeepSeek Reasoner", prompt_micros=550, completion_micros=2190),
    ),
)

_XAI = ProviderSpec(
    name="xai",
    label="xAI (Grok)",
    free=False,
    requires_key=True,
    kind="openai_compatible",
    base_url="https://api.x.ai/v1",
    api_key_url="https://console.x.ai",
    key_hint="xai-…",
    models=(
        ModelSpec("grok-4", "Grok 4", prompt_micros=3000, completion_micros=15000),
        ModelSpec("grok-3", "Grok 3", prompt_micros=3000, completion_micros=15000),
        ModelSpec("grok-3-mini", "Grok 3 mini", prompt_micros=300, completion_micros=500),
    ),
)

_TOGETHER = ProviderSpec(
    name="together",
    label="Together AI",
    free=False,
    requires_key=True,
    kind="openai_compatible",
    base_url="https://api.together.xyz/v1",
    api_key_url="https://api.together.ai/settings/api-keys",
    models=(
        ModelSpec(
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "Llama 3.3 70B Turbo",
            prompt_micros=880,
            completion_micros=880,
        ),
        ModelSpec(
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
            "Qwen 2.5 72B Turbo",
            prompt_micros=1200,
            completion_micros=1200,
        ),
    ),
)

_FIREWORKS = ProviderSpec(
    name="fireworks",
    label="Fireworks AI",
    free=False,
    requires_key=True,
    kind="openai_compatible",
    base_url="https://api.fireworks.ai/inference/v1",
    api_key_url="https://fireworks.ai/account/api-keys",
    models=(
        ModelSpec(
            "accounts/fireworks/models/llama-v3p3-70b-instruct",
            "Llama 3.3 70B",
            prompt_micros=900,
            completion_micros=900,
        ),
        ModelSpec(
            "accounts/fireworks/models/qwen3-235b-a22b",
            "Qwen 3 235B",
            prompt_micros=220,
            completion_micros=880,
        ),
    ),
)

_CEREBRAS = ProviderSpec(
    name="cerebras",
    label="Cerebras",
    free=True,
    requires_key=True,
    kind="openai_compatible",
    base_url="https://api.cerebras.ai/v1",
    api_key_url="https://cloud.cerebras.ai",
    description="Very high throughput; free tier available.",
    models=(
        ModelSpec("llama-3.3-70b", "Llama 3.3 70B"),
        ModelSpec("llama3.1-8b", "Llama 3.1 8B"),
        ModelSpec("qwen-3-32b", "Qwen 3 32B"),
    ),
)

_CUSTOM = ProviderSpec(
    name="custom",
    label="Custom endpoint",
    free=True,
    requires_key=False,
    kind="openai_compatible",
    base_url=None,
    base_url_required=True,
    description="Any OpenAI-compatible server — vLLM, LM Studio, a private gateway.",
    models=(),
)


PROVIDERS: tuple[ProviderSpec, ...] = (
    _GROQ,
    _GEMINI,
    _OPENROUTER,
    _OLLAMA,
    _CEREBRAS,
    _OPENAI,
    _ANTHROPIC,
    _MISTRAL,
    _DEEPSEEK,
    _XAI,
    _TOGETHER,
    _FIREWORKS,
    _CUSTOM,
)

PROVIDERS_BY_NAME: dict[str, ProviderSpec] = {p.name: p for p in PROVIDERS}

#: Provider names an agent may be configured with. `fake` is excluded deliberately — it is a
#: test/E2E construct resolved ahead of the catalog in `get_chat_provider()`, not something an
#: operator picks.
PROVIDER_NAMES: tuple[str, ...] = tuple(PROVIDERS_BY_NAME)


def get_provider(name: str) -> ProviderSpec | None:
    return PROVIDERS_BY_NAME.get(name)


def model_ids(name: str) -> list[str]:
    spec = PROVIDERS_BY_NAME.get(name)
    return [m.id for m in spec.models] if spec else []


def default_model(name: str) -> str | None:
    """The model a provider is seeded with when an operator first selects it."""
    ids = model_ids(name)
    return ids[0] if ids else None


def find_model(provider: str, model: str) -> ModelSpec | None:
    spec = PROVIDERS_BY_NAME.get(provider)
    if spec is None:
        return None
    return next((m for m in spec.models if m.id == model), None)


def legacy_catalog() -> dict[str, dict[str, Any]]:
    """`PROVIDER_CATALOG`'s original shape, kept so existing callers/tests keep working."""
    return {
        p.name: {
            "label": p.label,
            "free": p.free,
            "requires_key": p.requires_key,
            "models": [m.id for m in p.models],
        }
        for p in PROVIDERS
    }


# --------------------------------------------------------------------------------------
# Guard models (docs/11 §4-L2). Never offered to an agent — these classify, they do not chat.
# --------------------------------------------------------------------------------------

#: Models the safety layers call, kept here rather than inline for the reason this module
#: already exists: Groq **deprecated `meta-llama/llama-guard-4-12b` on 2026-02-10**, and this
#: repo separately shipped a stale `mixtral-8x7b-32768` for months. Check
#: https://console.groq.com/docs/deprecations before changing an id.
#:
#: They are deliberately **not** in `PROVIDERS`: `GET /v1/credentials/providers` drives the
#: builder's Model tab, and a classifier that answers with a bare float would be selectable as
#: an agent's chat model. `guard_injection_model` in Settings is what actually picks one, so an
#: operator can move to a successor without a deploy; this is the seed and the price book.
GUARD_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "meta-llama/llama-prompt-guard-2-86m",
        "Llama Prompt Guard 2 86M",
        context=512,
        tools=False,
        prompt_micros=40,  # $0.04 per 1M tokens
        completion_micros=40,
        note="Jailbreak/injection classifier. Returns a probability in [0,1] as its content.",
    ),
)

GUARD_MODELS_BY_ID: dict[str, ModelSpec] = {m.id: m for m in GUARD_MODELS}
