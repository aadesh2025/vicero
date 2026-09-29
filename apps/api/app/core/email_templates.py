"""The transactional emails Vicero sends, as (subject, text, html) triples.

Deliberately f-strings over a templating engine: four emails don't justify a Jinja
dependency or a template loader, and a reviewer can see the whole email in one place.

**The plain-text part keeps a `Token: <raw>` line.** The dev console outbox is how tests and
the local flow recover a token without a mail server, and a dozen test files parse exactly
that marker. It leaks nothing new — the same token is already in the link on the line above.
The HTML part shows only the button, which is what a real recipient sees.
"""

from __future__ import annotations

from html import escape

_BRAND = "Vicero"

_FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
_BODY_STYLE = f"margin:0;padding:24px;background:#f5f5f4;font-family:{_FONT};"
_CARD_STYLE = "max-width:480px;margin:0 auto;background:#ffffff;border-radius:12px;padding:32px;"
_WORDMARK_STYLE = (
    "margin:0 0 24px;font-size:15px;font-weight:700;letter-spacing:-0.01em;color:#1c1917;"
)
_HEADING_STYLE = "margin:0 0 12px;font-size:20px;line-height:1.3;font-weight:650;color:#1c1917;"
_PARAGRAPH_STYLE = "margin:0 0 24px;font-size:14px;line-height:1.6;color:#57534e;"
_BUTTON_STYLE = (
    "display:inline-block;padding:11px 20px;border-radius:8px;background:#1f2937;"
    "color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;"
)
_FOOTER_STYLE = "margin:24px 0 0;font-size:12px;line-height:1.6;color:#a8a29e;"
_FALLBACK_STYLE = (
    "margin:8px 0 0;font-size:12px;line-height:1.6;color:#a8a29e;word-break:break-all;"
)


def _shell(heading: str, paragraph: str, cta_label: str, link: str, footer: str) -> str:
    """One HTML shell for every email: wordmark, heading, paragraph, button, footer.

    Table-free, inline-styled, and system-font — the lowest-common-denominator that survives
    Gmail/Outlook without a build step. Every interpolation is escaped.
    """
    return (
        "<!doctype html>\n"
        "<html>\n"
        f'  <body style="{_BODY_STYLE}">\n'
        f'    <div style="{_CARD_STYLE}">\n'
        f'      <p style="{_WORDMARK_STYLE}">{escape(_BRAND)}</p>\n'
        f'      <h1 style="{_HEADING_STYLE}">{escape(heading)}</h1>\n'
        f'      <p style="{_PARAGRAPH_STYLE}">{escape(paragraph)}</p>\n'
        f'      <a href="{escape(link, quote=True)}" style="{_BUTTON_STYLE}">'
        f"{escape(cta_label)}</a>\n"
        f'      <p style="{_FOOTER_STYLE}">{escape(footer)}</p>\n'
        f'      <p style="{_FALLBACK_STYLE}">Or paste this link into your browser: '
        f"{escape(link)}</p>\n"
        "    </div>\n"
        "  </body>\n"
        "</html>\n"
    )


def invitation_email(org_name: str, role: str, link: str, token: str) -> tuple[str, str, str]:
    subject = f"You're invited to {org_name}"
    text = f"Join {org_name} as {role}: {link}\nToken: {token}"
    html = _shell(
        heading=f"You've been invited to {org_name}",
        paragraph=f"You're invited to join {org_name} on {_BRAND} as {role}. "
        "This invitation expires in 7 days.",
        cta_label="Accept invitation",
        link=link,
        footer="If you weren't expecting this invitation, you can ignore this email.",
    )
    return subject, text, html


def verification_email(link: str, token: str) -> tuple[str, str, str]:
    subject = "Verify your email"
    text = f"Confirm your email: {link}\nToken: {token}"
    html = _shell(
        heading="Confirm your email address",
        paragraph=f"Confirm this address to finish setting up your {_BRAND} account.",
        cta_label="Verify email",
        link=link,
        footer="If you didn't create a Vicero account, you can ignore this email.",
    )
    return subject, text, html


def password_reset_email(link: str, token: str) -> tuple[str, str, str]:
    subject = "Reset your password"
    text = f"Reset your password: {link}\nToken: {token}"
    html = _shell(
        heading="Reset your password",
        paragraph="Choose a new password for your account. This link can only be used once.",
        cta_label="Reset password",
        link=link,
        footer="If you didn't ask for a password reset, you can ignore this email — "
        "your password is unchanged.",
    )
    return subject, text, html


def magic_link_email(link: str, token: str) -> tuple[str, str, str]:
    subject = "Your sign-in link"
    text = f"Sign in: {link}\nToken: {token}"
    html = _shell(
        heading="Your sign-in link",
        paragraph="Use the link below to sign in. It can only be used once, and expires shortly.",
        cta_label="Sign in",
        link=link,
        footer="If you didn't request this link, you can ignore this email.",
    )
    return subject, text, html


# ── Free-trial lifecycle (docs/18 §8) ─────────────────────────────────────────
# One function, one copy block per notice kind, so the whole set can be read (and reworded) in
# one place. The visitor never sees any of this — these go to the workspace owner only.
def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def trial_notice_email(
    kind: str,
    *,
    org_name: str,
    link: str,
    days_left: int = 0,
    used: int = 0,
    limit: int = 0,
    unanswered: int = 0,
) -> tuple[str, str, str]:
    left = _plural(days_left, "day")
    missed = (
        f" {_plural(unanswered, 'visitor message')} arrived that nobody answered."
        if unanswered
        else ""
    )
    if kind == "trial_day7":
        subject = f"Your {_BRAND} trial ends in {left}"
        heading = f"{left.capitalize()} left in your free trial"
        body = (
            f"Your free trial of {org_name} ends in {left}. After that your agent stops "
            "replying to visitors, though everything you've built stays safe. Upgrade any "
            "time to keep it running."
        )
    elif kind == "trial_day9":
        subject = f"Last day of your {_BRAND} trial"
        heading = f"Your trial ends in {left}"
        body = (
            f"Your free trial of {org_name} ends in {left}. Once it does, your agent stops "
            "answering visitors. Upgrade now so there's no gap."
        )
    elif kind == "trial_ended":
        subject = f"Your {_BRAND} free trial has ended"
        heading = "Your free trial has ended"
        body = (
            f"{org_name}'s agent has stopped replying to visitors. Nothing was deleted."
            f"{missed} Upgrade to switch it back on."
        )
    elif kind == "messages_80":
        subject = f"You've used 80% of your {_BRAND} trial messages"
        heading = "80% of your trial messages are used"
        body = (
            f"{org_name} has used {used} of {limit} trial messages. When they run out your "
            "agent stops replying, so upgrade before then to avoid missing visitors."
        )
    elif kind == "messages_100":
        subject = f"You've used all your {_BRAND} trial messages"
        heading = "You've used all your trial messages"
        body = (
            f"{org_name} has used all {limit} trial messages, so your agent has stopped "
            f"replying to visitors. Nothing was deleted.{missed} Upgrade to switch it back on."
        )
    else:
        raise ValueError(f"unknown trial notice {kind!r}")
    text = f"{heading}\n\n{body}\n\nUpgrade: {link}"
    html = _shell(
        heading=heading,
        paragraph=body,
        cta_label="Upgrade",
        link=link,
        footer=f"You're receiving this because you own the {org_name} workspace on {_BRAND}.",
    )
    return subject, text, html
