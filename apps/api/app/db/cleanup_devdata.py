"""Remove ephemeral dev/test data from a local database.

Throwaway rows accumulate in the dev DB from manual testing and the Playwright E2E suite.
This utility deletes:

  * the ``live_demo`` feature flag created during Phase 17 testing,
  * every fixture user in ``FIXTURE_EMAIL_PATTERNS`` and the organizations they created (org
    FKs cascade, so all agents/conversations/channels/etc. under those orgs go with them).

Two domains are swept, and the reason each is safe is different:

  ``@example.com``     Playwright fixtures. Created constantly, never by a human.
  ``@vicero.local``  The seed account (``app/db/seed.py``). Its demo org is recreated in full
                       by ``make seed``, so deleting it loses nothing that one command cannot
                       rebuild.

**``@vicero.dev`` is deliberately NOT swept, and must not be added.** That is
``PROVISION_STAFF_EMAIL`` — the staff login ``scripts/provision-client.mjs`` uses to stand up
**real clients**. Every org it provisions carries ``created_by = provision@vicero.dev``, so a
pattern sweep on that domain would delete real client workspaces the first time one was
provisioned through the script. Its leftover test orgs (``Acme Co``, ``Globex Inc``) look like
fixtures but are indistinguishable at the schema level from a real tenant, so they are removed
by name when an operator decides to, never by a rule.

It is **idempotent** and only ever touches those fixture domains + the named flag — it can
never delete a real tenant. Run it with::

    uv run python -m app.db.cleanup_devdata          # delete
    uv run python -m app.db.cleanup_devdata --dry-run  # report only

Nuke-everything alternative (also clears throwaway rows): ``alembic downgrade base &&
alembic upgrade head && python -m app.db.seed``.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.logging import configure_logging, get_logger
from app.db.session import SessionFactory

log = get_logger("db.cleanup")

#: Fixture accounts safe to delete unattended. Anything added here is deleted *along with the
#: organizations it created*, so a domain only belongs on this list if no real tenant can ever
#: be created by an account on it. See the module docstring on ``@vicero.dev``.
FIXTURE_EMAIL_PATTERNS = ["%@example.com", "%@vicero.local"]

# Nullable, no-cascade user references. Nulled before deleting users so a stray cross-org
# reference can never block the delete. (All test content normally lives in deleted orgs.)
_USER_REF_COLUMNS: list[tuple[str, str]] = [
    ("agents", "created_by"),
    ("agent_versions", "created_by"),
    ("tools", "created_by"),
    ("channels", "created_by"),
    ("knowledge_bases", "created_by"),
    ("documents", "created_by"),
    ("conversations", "assigned_to"),
    ("handoffs", "assigned_to"),
    ("invitations", "invited_by"),
    ("audit_logs", "actor_user_id"),
]


async def cleanup(dry_run: bool = False) -> dict[str, int]:
    async with SessionFactory() as session:
        users = 0
        orgs = 0
        for pattern in FIXTURE_EMAIL_PATTERNS:
            users += int(
                (
                    await session.execute(
                        text("SELECT count(*) FROM users WHERE email LIKE :p"), {"p": pattern}
                    )
                ).scalar_one()
            )
            orgs += int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM organizations WHERE created_by IN "
                            "(SELECT id FROM users WHERE email LIKE :p)"
                        ),
                        {"p": pattern},
                    )
                ).scalar_one()
            )
        flags = (await session.execute(text("SELECT count(*) FROM feature_flags WHERE key = 'live_demo'"))).scalar_one()

        counts = {"users": int(users), "orgs": int(orgs), "live_demo_flags": int(flags)}
        if dry_run:
            log.info("cleanup_dry_run", **counts)
            return counts

        # 1. The named Phase-17 test flag.
        await session.execute(text("DELETE FROM feature_flags WHERE key = 'live_demo'"))
        for pattern in FIXTURE_EMAIL_PATTERNS:
            # 2. Orgs created by fixture users → cascades to their agents/conversations/etc.
            await session.execute(
                text(
                    "DELETE FROM organizations WHERE created_by IN "
                    "(SELECT id FROM users WHERE email LIKE :p)"
                ),
                {"p": pattern},
            )
            # 3. Defensively clear any lingering no-cascade references to the doomed users.
            for tbl, col in _USER_REF_COLUMNS:
                await session.execute(
                    text(
                        f"UPDATE {tbl} SET {col} = NULL WHERE {col} IN "
                        f"(SELECT id FROM users WHERE email LIKE :p)"
                    ),
                    {"p": pattern},
                )
            # 4. Delete the fixture users (sessions/oauth/memberships cascade on user_id).
            await session.execute(text("DELETE FROM users WHERE email LIKE :p"), {"p": pattern})
        await session.commit()

        log.info("cleanup_done", **counts)
        return counts


if __name__ == "__main__":
    configure_logging()
    result = asyncio.run(cleanup(dry_run="--dry-run" in sys.argv))
    print(f"cleanup: {result}")
