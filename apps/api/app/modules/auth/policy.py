"""Signup policy: email normalisation, disposable-domain blocklist, weak-password list (docs/18 §3, §10).

Kept as small, dependency-free data + pure functions so they are trivially testable and the
lists can grow without touching the auth flow.
"""

from __future__ import annotations

from app.db.base import normalize_email

# Re-exported so callers can keep importing the policy helpers from one place.
__all__ = ["normalize_email"]


# Common throwaway-mailbox providers. Deliberately a short, well-known list rather than a
# scraped one: a stale giant blocklist blocks real customers, and this control only has to make
# minting trials annoying, not impossible (per-IP limits and email verification do the rest).
DISPOSABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "mailinator.com", "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
        "sharklasers.com", "grr.la", "10minutemail.com", "10minutemail.net", "tempmail.com",
        "temp-mail.org", "temp-mail.io", "tempmail.net", "throwawaymail.com", "yopmail.com",
        "yopmail.net", "trashmail.com", "trashmail.net", "getnada.com", "nada.email",
        "maildrop.cc", "dispostable.com", "fakeinbox.com", "mintemail.com", "mohmal.com",
        "emailondeck.com", "spamgourmet.com", "mailnesia.com", "mytemp.email", "burnermail.io",
        "moakt.com", "tempail.com", "mail.tm", "discard.email", "inboxkitten.com",
        "tempinbox.com", "mailcatch.com", "spam4.me", "anonaddy.me", "1secmail.com",
        "1secmail.net", "1secmail.org", "byom.de", "dropmail.me", "emltmp.com", "tmpmail.org",
    }
)


def is_disposable_email(email: str) -> bool:
    domain = email.strip().lower().rpartition("@")[2]
    # Match the registered domain and any subdomain of a listed one.
    return any(domain == d or domain.endswith("." + d) for d in DISPOSABLE_DOMAINS)


# The passwords people actually pick. Not a breach corpus — a cheap floor that rejects the
# handful of values responsible for most credential-stuffing successes. All lowercase; the
# check lowercases the candidate, so "Password1" is caught too.
COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "password", "password1", "password12", "password123", "password1234", "passw0rd",
        "12345678", "123456789", "1234567890", "123123123", "11111111", "00000000",
        "qwertyui", "qwerty123", "qwertyuiop", "1q2w3e4r", "1q2w3e4r5t", "q1w2e3r4",
        "iloveyou", "iloveyou1", "letmein1", "letmein123", "welcome1", "welcome123",
        "admin123", "administrator", "abc12345", "abcd1234", "abcdefgh", "monkey123",
        "dragon123", "football1", "baseball1", "superman1", "trustno1", "sunshine1",
        "princess1", "changeme", "changeme123", "botforge", "botforge123", "aurozen123",
        "asdfghjk", "zxcvbnm1", "starwars1", "whatever1", "internet1", "computer1",
    }
)

MIN_PASSWORD_LENGTH = 8


def password_problem(password: str, email: str | None = None) -> str | None:
    """Why a password is unacceptable, or None. The message is safe to show the user."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        return "That password is too common. Choose something less guessable."
    if email:
        local = email.strip().lower().partition("@")[0]
        if len(local) >= 4 and lowered == local:
            return "Your password can't be your email address."
    if len(set(password)) == 1:
        return "That password is too simple. Choose something less guessable."
    return None
