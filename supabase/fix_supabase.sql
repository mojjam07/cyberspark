-- =====================================================================
-- CyberSpark Enroll — Supabase fix script
-- Run in: Supabase Dashboard -> SQL Editor -> New query -> paste -> Run
--
-- WHAT THIS FIXES
--  1. "relation api_order does not exist" / "already exists" errors on
--     `python manage.py migrate`. Cause: the database still holds tables
--     + migration history from an OLDER version of this project. The old
--     history reuses migration names (0001_initial, ...), so Django thinks
--     tables exist that were never created and skips them.
--  2. Supabase exposes every table in the `public` schema through its
--     auto-generated REST API (anon / authenticated roles). Django tables
--     (users, orders, payment proofs, ...) must NOT be reachable that way.
--
-- HOW TO USE
--   Tip: the Supabase SQL editor only displays the result of the LAST
--   statement it runs and may hide RAISE NOTICE messages, so the easiest
--   way is to highlight ONE part at a time and press "Run" (or Ctrl+Enter).
--   1. Run PART 1, then PART 2, then PART 3 (one at a time).
--   2. Deploy / run `python manage.py migrate` (Render does this for you in
--      the Procfile `release:` step).
--   3. Run PART 3 again (it is safe to repeat) so the freshly created
--      tables also get row-level security enabled.
--
-- SAFETY
--   * PART 2 only touches tables whose names start with `api_` and rows in
--     django_migrations where app = 'api'. Django's own tables (auth_user,
--     sessions, admin log...) and your users/passwords are NOT touched.
--   * PART 2 does nothing on a healthy database (it only resets when it
--     detects the stale/inconsistent state described above).
--   * Resetting deletes app data (courses, lessons, orders, reviews,
--     enrollments). Re-create it with `python manage.py seed_demo_data` or
--     the /manage/ staff dashboard.
-- =====================================================================


-- ---------------------------------------------------------------------
-- PART 1 — DIAGNOSE (read-only). Look at the results before continuing.
-- ---------------------------------------------------------------------

-- 1a. App tables currently in the database
SELECT tablename AS api_tables_present
FROM pg_tables
WHERE schemaname = 'public' AND tablename LIKE 'api\_%'
ORDER BY tablename;

-- 1b. Does Django's migration-history table exist yet?
SELECT CASE WHEN to_regclass('public.django_migrations') IS NULL
            THEN 'No django_migrations table — brand new database, nothing stale to fix'
            ELSE 'django_migrations exists — run query 1c below'
       END AS migration_history_status;

-- 1c. Migration history Django has recorded for this app.
--     (Only run this if 1b says the table exists; it errors on a new DB.)
--     Old-project markers to look for: 0002_course_is_featured and
--     0003_populate_dummy_data — if you see either, PART 2 will reset.
-- SELECT app, name, applied FROM django_migrations WHERE app = 'api' ORDER BY id;


-- ---------------------------------------------------------------------
-- PART 2 — RESET STALE APP TABLES (only runs when needed)
-- Set force_reset to true to wipe the api_* tables unconditionally.
-- ---------------------------------------------------------------------
DO $$
DECLARE
  force_reset    boolean := false;
  has_history    boolean := to_regclass('public.django_migrations') IS NOT NULL;
  any_api_tables boolean;
  any_api_hist   boolean := false;
  old_markers    boolean := false;
  r              record;
BEGIN
  SELECT EXISTS (
    SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'api\_%'
  ) INTO any_api_tables;

  IF has_history THEN
    SELECT EXISTS (SELECT 1 FROM public.django_migrations WHERE app = 'api')
      INTO any_api_hist;
    -- Migration names that only existed in the OLD version of the project
    SELECT EXISTS (
      SELECT 1 FROM public.django_migrations
      WHERE app = 'api'
        AND name IN ('0002_course_is_featured', '0003_populate_dummy_data')
    ) INTO old_markers;
  END IF;

  IF force_reset
     OR old_markers
     OR ((any_api_tables OR any_api_hist) AND to_regclass('public.api_order') IS NULL)
  THEN
    FOR r IN
      SELECT tablename FROM pg_tables
      WHERE schemaname = 'public' AND tablename LIKE 'api\_%'
    LOOP
      EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', r.tablename);
      RAISE NOTICE 'Dropped stale table public.%', r.tablename;
    END LOOP;

    IF has_history THEN
      DELETE FROM public.django_migrations WHERE app = 'api';
      RAISE NOTICE 'Cleared stale migration history for app "api".';
    END IF;

    RAISE NOTICE 'Reset complete. Now run: python manage.py migrate';
  ELSE
    RAISE NOTICE 'Schema looks healthy (or brand new) — nothing to reset.';
  END IF;
END $$;


-- 2b. Result of PART 2: how many api_* tables exist right now.
--     After a reset this is 0 until `python manage.py migrate` recreates them.
SELECT count(*) AS api_tables_now
FROM pg_tables
WHERE schemaname = 'public' AND tablename LIKE 'api\_%';


-- ---------------------------------------------------------------------
-- PART 3 — LOCK DOWN THE SUPABASE PUBLIC API (safe to re-run any time)
-- Django connects as the table owner (postgres) which bypasses RLS, so
-- the app keeps working. anon/authenticated (the browser-facing API keys)
-- lose all access to your tables.
-- If you later want to read a table from supabase-js, add an explicit
-- RLS policy + GRANT for just that table instead of undoing this.
-- ---------------------------------------------------------------------
DO $$
DECLARE
  r        record;
  rolename text;
BEGIN
  -- Row-level security on every table we own in public
  FOR r IN
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public' AND tableowner = current_user
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
  END LOOP;

  FOREACH rolename IN ARRAY ARRAY['anon', 'authenticated'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = rolename) THEN
      EXECUTE format('REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM %I', rolename);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', rolename);
      EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', rolename);
      -- Stop Supabase's default grants from re-exposing FUTURE tables
      -- created by later migrations
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM %I', rolename);
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', rolename);
      EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I', rolename);
    END IF;
  END LOOP;
END $$;


-- ---------------------------------------------------------------------
-- PART 4 — VERIFY (read-only). Both result sets should be EMPTY / zero
-- after you have run migrate and then Part 3 once more.
-- ---------------------------------------------------------------------

-- 4a. Tables in public WITHOUT row-level security
SELECT c.relname AS table_without_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
ORDER BY c.relname;

-- 4b. Privileges still granted to the public API roles (expect 0 rows)
SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
ORDER BY table_name, grantee;
