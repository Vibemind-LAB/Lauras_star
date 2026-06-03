-- PostgreSQL Row-Level Security for multi-tenant isolation (docs/14-enterprise.md).
-- Applied only on the Postgres backend (PostgresDatabase.apply_rls); SQLite has no RLS
-- and the desktop path is untouched. This is defense in depth: the API layer already
-- filters by the principal's org, RLS enforces it again at the database.
--
-- Rows are visible only when the session GUC app.current_org is unset/empty (local owner
-- or admin) or equals the row's org_id. FORCE makes the policy apply even to the table
-- owner (so it is effective in single-role deployments). Idempotent (re-appliable).
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS projects_org_isolation ON projects;
CREATE POLICY projects_org_isolation ON projects
  USING (
    current_setting('app.current_org', true) IS NULL
    OR current_setting('app.current_org', true) = ''
    OR org_id = current_setting('app.current_org', true)
  )
  WITH CHECK (
    current_setting('app.current_org', true) IS NULL
    OR current_setting('app.current_org', true) = ''
    OR org_id = current_setting('app.current_org', true)
  );
