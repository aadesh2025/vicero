"""Signup abuse controls and login hardening (docs/18 §3, §10)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.auth.policy import (
    is_disposable_email,
    normalize_email,
    password_problem,
)
from tests.selfserve_helpers import STRONG, signup, unique_email, users_named

pytestmark = pytest.mark.usefixtures("self_serve")


# ── pure policy ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "normal"),
    [
        ("Alice@Example.com", "alice@example.com"),
        ("a.l.i.c.e@gmail.com", "alice@gmail.com"),
        ("alice+promo@gmail.com", "alice@gmail.com"),
        ("A.Lice+x.y@googlemail.com", "alice@gmail.com"),
        ("first.last+tag@company.com", "first.last@company.com"),  # dots only mean nothing at Gmail
    ],
)
def test_email_normalisation(raw: str, normal: str) -> None:
    assert normalize_email(raw) == normal


def test_dotted_local_parts_elsewhere_are_different_mailboxes() -> None:
    assert normalize_email("a.b@company.com") != normalize_email("ab@company.com")


@pytest.mark.parametrize("email", ["x@mailinator.com", "x@sub.mailinator.com", "X@YOPMAIL.COM"])
def test_disposable_domains_are_recognised(email: str) -> None:
    assert is_disposable_email(email)


def test_a_real_domain_that_merely_contains_a_listed_name_is_fine() -> None:
    assert not is_disposable_email("x@notmailinator.com")
    assert not is_disposable_email("x@example.com")


@pytest.mark.parametrize("pw", ["short", "password123", "Password123", "12345678", "aaaaaaaaaa"])
def test_weak_passwords_are_refused(pw: str) -> None:
    assert password_problem(pw) is not None


def test_a_reasonable_password_passes_and_the_email_local_part_does_not() -> None:
    assert password_problem(STRONG) is None
    assert password_problem("alicewonder", "alicewonder@example.com") is not None


# ── over the API ─────────────────────────────────────────────────────────────
async def test_weak_password_is_refused_at_signup(client: AsyncClient) -> None:
    resp = await client.post("/v1/auth/signup", json={"email": unique_email(), "password": "password123"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "auth.weak_password"


async def test_disposable_email_is_refused_at_signup_and_magic_link(client: AsyncClient) -> None:
    for path, body in (
        ("/v1/auth/signup", {"email": "burner@mailinator.com", "password": STRONG}),
        ("/v1/auth/magic-link", {"email": "burner@yopmail.com"}),
    ):
        resp = await client.post(path, json=body)
        assert resp.status_code == 400, path
        assert resp.json()["error"]["code"] == "auth.email_not_allowed"


async def test_the_blocklist_can_be_switched_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "block_disposable_emails", False)
    resp = await client.post("/v1/auth/signup", json={"email": "internal@mailinator.com", "password": STRONG})
    assert resp.status_code == 200


async def test_one_trial_per_person_across_gmail_variants(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    stem = unique_email("dots").split("@")[0].replace(".", "")
    await signup(client, f"{stem}@gmail.com")
    for variant in (f"{stem[:2]}.{stem[2:]}@gmail.com", f"{stem}+again@gmail.com", f"{stem}@googlemail.com"):
        resp = await client.post("/v1/auth/signup", json={"email": variant, "password": STRONG})
        assert resp.status_code == 409, variant
        assert await users_named(db_session, variant) == []


async def test_signups_per_ip_per_day_are_capped(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "signups_per_ip_per_day", 2)
    for _ in range(2):
        await signup(client)
    third = await client.post("/v1/auth/signup", json={"email": unique_email(), "password": STRONG})
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "auth.signup_rate_limited"


# ── no account enumeration ───────────────────────────────────────────────────
async def test_login_does_not_reveal_whether_the_email_exists(client: AsyncClient) -> None:
    known = unique_email("known")
    await signup(client, known)
    wrong_password = await client.post("/v1/auth/login", json={"email": known, "password": "not-it-at-all"})
    no_such_user = await client.post(
        "/v1/auth/login", json={"email": unique_email("ghost"), "password": "not-it-at-all"}
    )
    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json() == no_such_user.json()


async def test_forgot_password_answers_identically_for_unknown_addresses(client: AsyncClient) -> None:
    known = unique_email("kn")
    await signup(client, known)
    a = await client.post("/v1/auth/password/forgot", json={"email": known})
    b = await client.post("/v1/auth/password/forgot", json={"email": unique_email("no")})
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json()


async def test_magic_link_and_resend_do_not_reveal_existence(client: AsyncClient) -> None:
    known = unique_email("m")
    await signup(client, known)
    a = await client.post("/v1/auth/magic-link", json={"email": known})
    b = await client.post("/v1/auth/magic-link", json={"email": unique_email("mm")})
    assert a.json() == b.json()
    c = await client.post("/v1/auth/verify-email/resend", json={"email": known})
    d = await client.post("/v1/auth/verify-email/resend", json={"email": unique_email("mmm")})
    assert c.json() == d.json()


# ── lockout + per-email limits ───────────────────────────────────────────────
async def test_repeated_failures_lock_the_account_out(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "login_lockout_failures", 3)
    email = unique_email("lock")
    await signup(client, email)
    for _ in range(3):
        bad = await client.post("/v1/auth/login", json={"email": email, "password": "wrong-password"})
        assert bad.status_code == 401
    locked = await client.post("/v1/auth/login", json={"email": email, "password": "wrong-password"})
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "auth.too_many_attempts"
    # Even the right password waits out the window — otherwise lockout would only slow a guesser
    # who happens to hit the right answer last.
    assert (await client.post("/v1/auth/login", json={"email": email, "password": STRONG})).status_code == 429


async def test_a_wrong_email_is_locked_out_the_same_way(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the lockout itself would be an oracle for which addresses have accounts."""
    monkeypatch.setattr(settings, "login_lockout_failures", 2)
    ghost = unique_email("ghost")
    for _ in range(2):
        await client.post("/v1/auth/login", json={"email": ghost, "password": "wrong-password"})
    assert (await client.post("/v1/auth/login", json={"email": ghost, "password": "x"})).status_code == 429


async def test_a_successful_login_is_not_blocked_by_other_peoples_failures(client: AsyncClient) -> None:
    email = unique_email("ok")
    await signup(client, email)
    await client.post("/v1/auth/login", json={"email": unique_email("other"), "password": "nope-nope"})
    assert (await client.post("/v1/auth/login", json={"email": email, "password": STRONG})).status_code == 200


async def test_magic_link_requests_are_limited_per_address(client: AsyncClient) -> None:
    email = unique_email("spam")
    codes = [(await client.post("/v1/auth/magic-link", json={"email": email})).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5
    assert codes[5:] == [429, 429]


async def test_password_reset_requests_are_limited_per_address(client: AsyncClient) -> None:
    email = unique_email("rst")
    await signup(client, email)
    codes = [(await client.post("/v1/auth/password/forgot", json={"email": email})).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


async def test_password_reset_enforces_the_password_rules(client: AsyncClient) -> None:
    import re

    from app.core.email import get_email_backend

    email = unique_email("rp")
    await signup(client, email)
    await client.post("/v1/auth/password/forgot", json={"email": email})
    token = re.search(r"Token: (\S+)", get_email_backend().outbox[-1].body)
    assert token
    weak = await client.post("/v1/auth/password/reset", json={"token": token.group(1), "password": "password123"})
    assert weak.status_code == 400
    ok = await client.post("/v1/auth/password/reset", json={"token": token.group(1), "password": "A-new-Str0ng-one!"})
    assert ok.status_code == 200
