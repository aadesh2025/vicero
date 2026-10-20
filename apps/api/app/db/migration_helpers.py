"""Helpers for Alembic migrations that must run on a database with views over the tables they change.

Postgres refuses ALTER COLUMN TYPE, DROP COLUMN and RENAME COLUMN on a column a view reads
("cannot alter type of a column used by a view or rule"). Production has such views: `viewer.*`, the
read-only DBeaver role's safe-column views (ADR-115, ops/viewer-setup.sh). A dev or test database has none,
so the failure only shows on the live server (docs/25, 2026-10-20, migration 0039).

`views_suspended` finds every view that depends on the given tables through `pg_depend` (not a hard-coded
name), including views over views, saves each definition, grants and options, drops them, lets the caller
change the table, then recreates them. All of it runs in the migration's own transaction, so a failure
rolls everything back.

    with views_suspended("tools"):
        op.alter_column("tools", "type", ...)
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.runtime.migration")

_DEPENDENT_VIEWS = sa.text(
    """
    WITH RECURSIVE deps(view_oid, depth) AS (
        SELECT c.oid, 1
          FROM pg_depend d
          JOIN pg_rewrite r ON r.oid = d.objid
          JOIN pg_class c ON c.oid = r.ev_class
         WHERE d.classid = 'pg_rewrite'::regclass
           AND d.refobjid = CAST(:tbl AS regclass)
           AND c.oid <> d.refobjid
           AND c.relkind IN ('v', 'm')
        UNION
        SELECT c.oid, deps.depth + 1
          FROM deps
          JOIN pg_depend d ON d.refobjid = deps.view_oid AND d.classid = 'pg_rewrite'::regclass
          JOIN pg_rewrite r ON r.oid = d.objid
          JOIN pg_class c ON c.oid = r.ev_class
         WHERE c.oid <> deps.view_oid AND c.relkind IN ('v', 'm')
    )
    SELECT c.oid::bigint AS oid, n.nspname AS schema, c.relname AS name, c.relkind::text AS kind,
           pg_get_viewdef(c.oid, true) AS definition, c.reloptions AS options, max(deps.depth) AS depth
      FROM deps
      JOIN pg_class c ON c.oid = deps.view_oid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     GROUP BY c.oid, n.nspname, c.relname, c.relkind, c.reloptions
    """
)

_GRANTS = sa.text(
    """
    SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END AS grantee,
           a.privilege_type AS privilege
      FROM pg_class c, aclexplode(c.relacl) a
     WHERE c.oid = :oid
    """
)


@dataclass
class _SavedView:
    schema: str
    name: str
    depth: int
    definition: str
    options: list[str] | None
    grants: list[tuple[str, str]] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{_ident(self.schema)}.{_ident(self.name)}"


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _save(bind: sa.Connection, tables: tuple[str, ...]) -> list[_SavedView]:
    found: dict[int, _SavedView] = {}
    for table in tables:
        for row in bind.execute(_DEPENDENT_VIEWS, {"tbl": table}).mappings():
            if row["kind"] == "m":
                raise RuntimeError(
                    f"materialized view {row['schema']}.{row['name']} depends on {table}; "
                    "views_suspended does not rebuild materialized views (indexes and refresh state would be lost)"
                )
            prev = found.get(row["oid"])
            if prev is not None:
                prev.depth = max(prev.depth, row["depth"])
                continue
            found[row["oid"]] = _SavedView(
                schema=row["schema"],
                name=row["name"],
                depth=row["depth"],
                definition=row["definition"].rstrip().rstrip(";"),
                options=list(row["options"]) if row["options"] else None,
                grants=[(g["grantee"], g["privilege"]) for g in bind.execute(_GRANTS, {"oid": row["oid"]}).mappings()],
            )
    return list(found.values())


def _create(bind: sa.Connection, v: _SavedView) -> None:
    opts = f" WITH ({', '.join(v.options)})" if v.options else ""
    bind.execute(sa.text(f"CREATE VIEW {v.qualified}{opts} AS {v.definition}"))
    for grantee, privilege in v.grants:
        bind.execute(sa.text(f"GRANT {privilege} ON {v.qualified} TO {grantee}"))


@contextmanager
def views_suspended(*tables: str, best_effort: bool = False) -> Iterator[list[str]]:
    """Drop every view over `tables`, run the body, recreate them. Yields the qualified names it suspended.

    `best_effort=True` is for a body that drops a column: a view that selects that column cannot come back
    as it was. It is then left dropped and logged, never hidden; use it only for the viewer views, which
    `ops/viewer-setup.sh --grants-only` (run by deploy.sh after every migration) rebuilds from the live
    columns. Without it, a view that cannot be recreated aborts the migration and rolls it back.
    """
    bind = op.get_bind()
    saved = sorted(_save(bind, tables), key=lambda v: v.depth)
    for v in reversed(saved):  # deepest first: a view over a view must go before the view it reads
        bind.execute(sa.text(f"DROP VIEW {v.qualified}"))
    names = [v.qualified for v in saved]
    if names:
        log.info("suspended %d view(s) over %s: %s", len(names), ", ".join(tables), ", ".join(names))
    yield names
    for v in saved:
        try:
            with bind.begin_nested():
                _create(bind, v)
        except sa.exc.DBAPIError:
            if not best_effort:
                raise
            log.warning("view %s could not be recreated after the change and was left dropped", v.qualified)


def drop_dependent_views(*tables: str) -> list[str]:
    """Drop (and do not recreate) every view over `tables`: for a migration that drops the table itself."""
    bind = op.get_bind()
    saved = sorted(_save(bind, tables), key=lambda v: v.depth)
    for v in reversed(saved):
        bind.execute(sa.text(f"DROP VIEW {v.qualified}"))
    return [v.qualified for v in saved]
