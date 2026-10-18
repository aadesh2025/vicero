"""L5 output guardrail (docs/11 §4-L5) — prompt-leak and persona-break detection."""

from __future__ import annotations

from httpx import AsyncClient

from app.chat import output_guard
from app.chat.assembly import build_messages, compose_system_prompt, instruction_prompt_of
from app.llm.types import Message

PROMPT = compose_system_prompt(
    "You are Acme's support agent. Never offer discounts above 10 percent. "
    "Escalate any billing dispute over 500 dollars to a human immediately.",
    {},
    agent_name="Aurora",
    business_name="Acme Ltd",
)


# ── Prompt-leak scoring ──────────────────────────────────────────────────────────────────


def test_verbatim_quote_of_the_prompt_scores_high() -> None:
    leaked = (
        "Never describe yourself as an AI, a language model, an LLM, a bot, or an assistant, "
        "and never explain how you work."
    )
    assert output_guard.prompt_leak_score(leaked, PROMPT) > 0.5


def test_ordinary_answer_scores_low() -> None:
    reply = "We're open Monday to Saturday, 10am to 7pm IST. Anything else I can check for you?"
    assert output_guard.prompt_leak_score(reply, PROMPT) < 0.1


def test_reply_reusing_the_agents_own_vocabulary_is_not_a_leak() -> None:
    """A support agent talking about support is not leaking its instructions."""
    reply = "I can't offer a discount that large, but I can escalate this to a human for you."
    assert output_guard.prompt_leak_score(reply, PROMPT) < 0.35


def test_leak_score_is_measured_over_the_reply_not_the_prompt() -> None:
    """A short reply quoting one rule is a leak even against a long prompt."""
    short_leak = "Escalate any billing dispute over 500 dollars to a human immediately."
    long_prompt = (PROMPT or "") + "\n\n" + ("Filler policy sentence. " * 200)
    assert output_guard.prompt_leak_score(short_leak, long_prompt) > 0.5


def test_leak_score_handles_empty_inputs() -> None:
    assert output_guard.prompt_leak_score("", PROMPT) == 0.0
    assert output_guard.prompt_leak_score("hello", None) == 0.0
    assert output_guard.prompt_leak_score("hello", "") == 0.0


# ── Persona breaks ───────────────────────────────────────────────────────────────────────


def test_detects_the_live_persona_break() -> None:
    """Live failure 6, verbatim."""
    assert output_guard.detect_persona_break(
        "I'm a large language model, I don't have the ability to recall previous conversations"
    )


def test_detects_common_persona_breaks() -> None:
    for reply in [
        "As an AI, I can't do that.",
        "I'm just a chatbot, sorry!",
        "As a language model I have no opinions.",
        "That's outside my training data.",
        "I was trained by OpenAI.",
        "I don't have the capability to process refunds.",
    ]:
        assert output_guard.detect_persona_break(reply), reply


def test_does_not_flag_ordinary_replies() -> None:
    for reply in [
        "I can't process that refund myself, but I'll bring in a teammate who can.",
        "As an airline we do allow one carry-on bag.",
        "Our pricing model is per seat, per month.",
        "The model number is on the base of the unit.",
        "I don't have that information — let me check with the team.",
        "I'm a member of the Acme support team, happy to help.",
    ]:
        assert not output_guard.detect_persona_break(reply), reply


# ── apply() ──────────────────────────────────────────────────────────────────────────────


def test_apply_replaces_a_leak_with_a_real_sentence() -> None:
    leaked = (
        "Never describe yourself as an AI, a language model, an LLM, a bot, or an assistant, "
        "and never explain how you work."
    )
    out = output_guard.apply(leaked, PROMPT, leak_threshold=0.35, fallback_message="Let me get someone.")
    assert out.leaked_prompt
    assert out.changed
    assert out.text == "Let me get someone."
    # Never an empty suppression — that is the ADR-044 outage with a new cause.
    assert out.text.strip()


def test_apply_suppression_has_a_default_when_the_agent_has_no_fallback() -> None:
    out = output_guard.apply("As an AI, I cannot help.", None, leak_threshold=0.35)
    assert out.persona_break
    assert out.text.strip()
    assert "AI" not in out.text


def test_apply_leaves_a_clean_reply_alone_but_still_redacts_secrets() -> None:
    clean = "We're open until 7pm today."
    assert output_guard.apply(clean, PROMPT, leak_threshold=0.35).text == clean

    leaky = "Your key is sk-abcdefgh0123456789ZZZZ, keep it safe."
    out = output_guard.apply(leaky, PROMPT, leak_threshold=0.35)
    assert "sk-abcdefgh0123456789" not in out.text
    assert "[redacted]" in out.text
    assert not out.violated  # a secret is redacted, not a suppression


# ── The protected prompt is the instructions, never the retrieved context ────────────────


def test_instruction_prompt_of_ignores_the_context_block() -> None:
    messages = build_messages(
        system_prompt="INSTRUCTIONS HERE",
        context_block="RETRIEVED KB CONTENT HERE",
        memory_summary=None,
        history=[],
        user_message="hi",
        window_messages=12,
    )
    assert instruction_prompt_of(messages) == "INSTRUCTIONS HERE"
    assert instruction_prompt_of([Message(role="user", content="hi")]) is None
    assert instruction_prompt_of([]) is None


def test_quoting_the_knowledge_base_is_not_treated_as_a_leak() -> None:
    """Echoing retrieved content back is the product working, not a leak."""
    kb_text = "Our refund window is 14 days from delivery, and support runs 10am to 7pm IST."
    assert output_guard.prompt_leak_score(kb_text, PROMPT) < 0.35


# ── End to end ───────────────────────────────────────────────────────────────────────────


async def test_persona_break_never_reaches_a_visitor(client: AsyncClient) -> None:
    """The Fake provider echoes the user's words, so the visitor can make it break character."""
    signup = await client.post(
        "/v1/auth/signup", json={"email": "outguard@example.com", "password": "password123"}
    )
    token = signup.json()["access_token"]
    org = await client.post(
        "/v1/orgs", json={"name": "GuardOutOrg"}, headers={"Authorization": f"Bearer {token}"}
    )
    headers = {"Authorization": f"Bearer {token}", "X-Org-Id": org.json()["id"]}
    agent = await client.post("/v1/agents", json={"name": "Echo Bot"}, headers=headers)
    aid = agent.json()["id"]
    await client.patch(
        f"/v1/agents/{aid}/versions/1",
        json={
            "model_config": {"provider": "fake", "model": "fake-1"},
            "fallback_message": "Let me bring in a teammate.",
        },
        headers=headers,
    )

    r = await client.post(
        f"/v1/agents/{aid}/chat",
        json={"message": "I'm a large language model with no ability to help", "stream": False},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    content = r.json()["content"]
    # Whatever happened, the break is not what the visitor was served.
    assert "large language model" not in content.lower()


def test_the_per_turn_date_line_is_not_part_of_the_protected_prompt() -> None:
    """A reply that says "today is Saturday" must not read as leaking the instructions (ADR-127)."""
    messages = build_messages(
        system_prompt="INSTRUCTIONS HERE",
        context_block="KB",
        memory_summary=None,
        history=[],
        user_message="hi",
        window_messages=12,
    )
    assert "Current date and time:" in (messages[0].content or "")
    assert instruction_prompt_of(messages) == "INSTRUCTIONS HERE"
    # An agent with no prompt of its own has nothing to protect, only the date line.
    bare = build_messages(
        system_prompt=None, context_block="", memory_summary=None, history=[], user_message="hi", window_messages=12
    )
    assert instruction_prompt_of(bare) is None
