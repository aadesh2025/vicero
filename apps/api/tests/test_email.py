"""Email delivery: the SMTP backend, the templates, and the queue_email dispatch rule.

No test touches a real relay. `aiosmtplib.send` is patched out, which is where the network
would be, so what's actually asserted is the MIME we hand it — the part that breaks silently
in a real inbox if it's wrong.
"""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from typing import Any

import pytest

from app.core import email as email_mod
from app.core.email import ConsoleEmailBackend, EmailMessage, SmtpEmailBackend, queue_email
from app.core.email_templates import (
    invitation_email,
    magic_link_email,
    password_reset_email,
    verification_email,
)
from app.core.errors import AppError

pytestmark = pytest.mark.anyio


class _Relay:
    """Stands in for the SMTP server: records the MIME message and the connection kwargs."""

    def __init__(self) -> None:
        self.sent: list[tuple[MimeMessage, dict[str, Any]]] = []

    async def send(self, mime: MimeMessage, **kwargs: Any) -> tuple[dict[str, Any], str]:
        self.sent.append((mime, kwargs))
        return {}, "250 OK"


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> _Relay:
    import aiosmtplib

    r = _Relay()
    monkeypatch.setattr(aiosmtplib, "send", r.send)
    return r


def _configure_smtp(monkeypatch: pytest.MonkeyPatch, **over: object) -> None:
    values: dict[str, object] = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "apikey",
        "smtp_pass": "secret",
        "smtp_from": "Vicero <noreply@vicero.test>",
    }
    values.update(over)
    for key, value in values.items():
        monkeypatch.setattr(email_mod.settings, key, value)


async def test_unconfigured_smtp_raises_rather_than_dropping_the_mail(
    monkeypatch: pytest.MonkeyPatch, relay: _Relay
) -> None:
    """A missing [HUMAN] secret must fail loudly (CLAUDE.md §7), not swallow an invitation."""
    _configure_smtp(monkeypatch, smtp_host="", smtp_from="")

    with pytest.raises(AppError) as excinfo:
        await SmtpEmailBackend().send(EmailMessage(to="a@example.com", subject="s", body="b"))

    assert excinfo.value.code == "email.not_configured"
    assert relay.sent == []  # nothing was handed to the relay


async def test_a_blank_host_with_a_set_sender_still_refuses(
    monkeypatch: pytest.MonkeyPatch, relay: _Relay
) -> None:
    _configure_smtp(monkeypatch, smtp_host="   ")
    with pytest.raises(AppError):
        await SmtpEmailBackend().send(EmailMessage(to="a@example.com", subject="s", body="b"))


async def test_send_builds_a_multipart_message_with_both_parts(
    monkeypatch: pytest.MonkeyPatch, relay: _Relay
) -> None:
    _configure_smtp(monkeypatch)

    await SmtpEmailBackend().send(
        EmailMessage(
            to="person@example.com",
            subject="You're invited to Acme",
            body="Join Acme: https://app.test/x",
            html_body="<p>Join Acme</p>",
        )
    )

    assert len(relay.sent) == 1
    mime, kwargs = relay.sent[0]
    assert mime["To"] == "person@example.com"
    assert mime["From"] == "Vicero <noreply@vicero.test>"
    assert mime["Subject"] == "You're invited to Acme"
    assert mime.is_multipart()
    types = [p.get_content_type() for p in mime.walk()]
    assert "text/plain" in types and "text/html" in types
    # A client that can't render HTML must still get the link.
    plain = mime.get_body(preferencelist=("plain",))
    assert plain is not None
    assert "https://app.test/x" in plain.get_content()
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587


@pytest.mark.parametrize(
    ("port", "start_tls", "use_tls"),
    [(587, True, False), (2587, True, False), (465, False, True)],
)
async def test_tls_mode_follows_the_port(
    monkeypatch: pytest.MonkeyPatch, relay: _Relay, port: int, start_tls: bool, use_tls: bool
) -> None:
    """465 is implicit TLS, everything else negotiates STARTTLS — and TLS is never optional."""
    _configure_smtp(monkeypatch, smtp_port=port)
    await SmtpEmailBackend().send(EmailMessage(to="a@example.com", subject="s", body="b"))
    _, kwargs = relay.sent[0]
    assert kwargs["start_tls"] is start_tls
    assert kwargs["use_tls"] is use_tls


async def test_text_only_message_is_not_forced_multipart(
    monkeypatch: pytest.MonkeyPatch, relay: _Relay
) -> None:
    _configure_smtp(monkeypatch)
    await SmtpEmailBackend().send(EmailMessage(to="a@example.com", subject="s", body="plain"))
    mime, _ = relay.sent[0]
    assert not mime.is_multipart()
    assert mime.get_content_type() == "text/plain"


async def test_blank_credentials_are_sent_as_none_not_empty_strings(
    monkeypatch: pytest.MonkeyPatch, relay: _Relay
) -> None:
    """An unauthenticated relay (local Postfix) needs no user/pass; "" would attempt AUTH."""
    _configure_smtp(monkeypatch, smtp_user="", smtp_pass="")
    await SmtpEmailBackend().send(EmailMessage(to="a@example.com", subject="s", body="b"))
    _, kwargs = relay.sent[0]
    assert kwargs["username"] is None
    assert kwargs["password"] is None


@pytest.mark.parametrize(
    ("template", "marker"),
    [
        (invitation_email("Acme", "editor", "https://app.test/i?token=T0K", "T0K"), "Acme"),
        (verification_email("https://app.test/verify?token=T0K", "T0K"), "Confirm"),
        (password_reset_email("https://app.test/reset?token=T0K", "T0K"), "Reset"),
        (magic_link_email("https://app.test/magic?token=T0K", "T0K"), "Sign in"),
    ],
)
def test_every_template_carries_the_link_the_token_line_and_html(
    template: tuple[str, str, str], marker: str
) -> None:
    """The `Token:` line is load-bearing: the console outbox is how dev/tests recover it."""
    subject, text, html = template
    assert subject
    assert "https://app.test/" in text
    assert "Token: T0K" in text
    assert marker in text or marker in html
    assert html.startswith("<!doctype html>")
    assert "T0K" in html  # the CTA link still works from the HTML part


def test_template_html_escapes_interpolated_values() -> None:
    """An org name is user input and lands in an HTML document."""
    _, _, html = invitation_email('Acme <script>alert(1)</script>', "editor", "https://a.test", "T")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


async def test_console_mode_sends_inline_so_the_outbox_is_populated_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(email_mod.settings, "email_backend", "console")
    backend = email_mod.get_email_backend()
    assert isinstance(backend, ConsoleEmailBackend)
    before = len(backend.outbox)

    await queue_email(EmailMessage(to="a@example.com", subject="s", body="b"))

    assert len(backend.outbox) == before + 1


async def test_smtp_mode_hands_off_to_the_worker_instead_of_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request path must not wait on a relay — it enqueues."""
    monkeypatch.setattr(email_mod.settings, "email_backend", "smtp")
    monkeypatch.setattr(email_mod.settings, "celery_task_always_eager", False)
    calls: list[tuple[object, ...]] = []

    from app.worker import tasks as worker_tasks

    monkeypatch.setattr(
        worker_tasks.send_email_task,
        "apply_async",
        lambda **kw: calls.append((kw["args"], kw["retry"])),
        raising=False,
    )

    await queue_email(
        EmailMessage(to="a@example.com", subject="s", body="b", html_body="<p>b</p>")
    )

    # retry=False is load-bearing: Celery's default policy turns a dead broker into a
    # multi-second hang on the caller's request.
    assert calls == [(["a@example.com", "s", "b", "<p>b</p>"], False)]


async def test_a_slow_broker_is_abandoned_instead_of_hanging_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured: kombu retries a dead broker for ~30s. The request must not wait for that."""
    import time

    monkeypatch.setattr(email_mod.settings, "email_backend", "smtp")
    monkeypatch.setattr(email_mod.settings, "celery_task_always_eager", False)
    monkeypatch.setattr(email_mod.settings, "email_enqueue_timeout_seconds", 0.2)

    def _hang(**_kw: object) -> None:
        time.sleep(5)

    from app.worker import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks.send_email_task, "apply_async", _hang, raising=False)

    started = time.monotonic()
    await queue_email(EmailMessage(to="a@example.com", subject="s", body="b"))
    assert time.monotonic() - started < 2.0  # returned on the timeout, not after the 5s sleep


async def test_a_dead_broker_never_fails_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signup must succeed even if the queue is unreachable."""
    monkeypatch.setattr(email_mod.settings, "email_backend", "smtp")
    monkeypatch.setattr(email_mod.settings, "celery_task_always_eager", False)

    def _boom(**_kw: object) -> None:
        raise ConnectionError("redis is down")

    from app.worker import tasks as worker_tasks

    monkeypatch.setattr(worker_tasks.send_email_task, "apply_async", _boom, raising=False)

    await queue_email(EmailMessage(to="a@example.com", subject="s", body="b"))  # must not raise


async def test_eager_mode_sends_inline_rather_than_through_asyncio_run(
    monkeypatch: pytest.MonkeyPatch, relay: _Relay
) -> None:
    """Eager tasks run in the caller's thread, where the tasks module's `asyncio.run` would
    raise inside the already-running loop (the trap that broke eager ingestion)."""
    monkeypatch.setattr(email_mod.settings, "email_backend", "smtp")
    monkeypatch.setattr(email_mod.settings, "celery_task_always_eager", True)
    _configure_smtp(monkeypatch)
    email_mod.get_email_backend.cache_clear()

    try:
        await queue_email(EmailMessage(to="a@example.com", subject="s", body="b"))
    finally:
        email_mod.get_email_backend.cache_clear()

    assert len(relay.sent) == 1  # delivered, no RuntimeError
