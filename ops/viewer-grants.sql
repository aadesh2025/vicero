-- Grants and safe-column views for the read-only `viewer` role (docs/25 section 13, ADR-115).
-- The one source of truth: ops/viewer-setup.sh pipes this file to psql on the server, and
-- tests/test_viewer_migrations.py runs it against a scratch database in CI. The role must exist and the caller
-- must have granted CONNECT. Idempotent: the views are rebuilt on every run, so a column added by a migration
-- is judged again.
--
-- Rules:
--   * a public table with NO secret column is granted as it is;
--   * a table WITH a secret column is not granted; `viewer.<table>` is a view with the safe columns only;
--   * token tables and `sessions` are not exposed at all;
--   * hidden_pairs lists columns that are hidden by name because the data in them can carry a credential even
--     though the column name does not say so (free-form JSON or URLs people fill in).
SET client_min_messages = warning;
ALTER ROLE viewer SET default_transaction_read_only = on;
ALTER ROLE viewer SET statement_timeout = '30s';
ALTER ROLE viewer SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE viewer SET search_path = viewer, public;

CREATE SCHEMA IF NOT EXISTS viewer;
GRANT USAGE ON SCHEMA public, viewer TO viewer;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM viewer;
REVOKE CREATE ON SCHEMA public FROM viewer;

DO $$
DECLARE
  r record;
  safe text;
  hidden_table CONSTANT text := '(^|_)tokens?$|^sessions$';
  secret_col   CONSTANT text := '(_enc|_encrypted|password|token_hash|key_hash|secret|access_token|refresh_token)';
  hidden_pairs CONSTANT text[] := ARRAY[
    'channels.config',            -- channel credentials
    'tools.config',               -- an API tool can carry a literal header or key
    'connections.default_headers',-- custom auth headers
    'mcp_servers.url_or_command', -- a URL with a key in its query, or a stdio command line
    'inbound_hooks.public_id',    -- the unguessable part of a hook's URL
    'handoffs.payload'            -- the raw arguments of a tool call waiting for approval
  ];
BEGIN
  -- Rebuild the views every run so a column added by a migration is judged again.
  FOR r IN SELECT viewname FROM pg_views WHERE schemaname = 'viewer' LOOP
    EXECUTE format('DROP VIEW IF EXISTS viewer.%I', r.viewname);
  END LOOP;

  FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relispartition = false
  LOOP
    IF r.relname ~ hidden_table THEN CONTINUE; END IF;

    SELECT string_agg(format('%I', a.attname), ', ' ORDER BY a.attnum)
      INTO safe
      FROM pg_attribute a
     WHERE a.attrelid = format('public.%I', r.relname)::regclass AND a.attnum > 0 AND NOT a.attisdropped
       AND a.attname !~ secret_col
       AND NOT (r.relname || '.' || a.attname = ANY (hidden_pairs));

    IF NOT EXISTS (
         SELECT 1 FROM pg_attribute a
          WHERE a.attrelid = format('public.%I', r.relname)::regclass AND a.attnum > 0 AND NOT a.attisdropped
            AND (a.attname ~ secret_col OR (r.relname || '.' || a.attname = ANY (hidden_pairs))))
    THEN
      EXECUTE format('GRANT SELECT ON public.%I TO viewer', r.relname);
    ELSIF safe IS NOT NULL THEN
      EXECUTE format('CREATE VIEW viewer.%I AS SELECT %s FROM public.%I', r.relname, safe, r.relname);
      EXECUTE format('GRANT SELECT ON viewer.%I TO viewer', r.relname);
    END IF;
  END LOOP;
END $$;
