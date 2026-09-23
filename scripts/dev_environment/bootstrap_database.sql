\set ON_ERROR_STOP on
\set migration_password `cat /run/secrets/postgres-migration-password`
\set migration_read_error :SHELL_ERROR
\set runtime_password `cat /run/secrets/postgres-runtime-password`
\set runtime_read_error :SHELL_ERROR

SELECT NOT :'migration_read_error'::boolean AND NOT :'runtime_read_error'::boolean
  AND length(:'migration_password') > 0 AND length(:'runtime_password') > 0
  AS credentials_present \gset
\if :credentials_present
\else
  DO $$ BEGIN
    RAISE EXCEPTION 'Required role credential file is empty or unavailable';
  END $$;
\endif

SELECT pg_catalog.format(
  'CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  'ci_coordinator_migration', :'migration_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ci_coordinator_migration'
) \gexec

SELECT pg_catalog.format(
  'ALTER ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  'ci_coordinator_migration', :'migration_password'
) \gexec

SELECT pg_catalog.format(
  'CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  'ci_coordinator_runtime', :'runtime_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ci_coordinator_runtime'
) \gexec

SELECT pg_catalog.format(
  'ALTER ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
  'ci_coordinator_runtime', :'runtime_password'
) \gexec

SELECT pg_catalog.format(
  'CREATE DATABASE %I OWNER %I', 'ci_coordinator', 'ci_coordinator_migration'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'ci_coordinator'
) \gexec

ALTER DATABASE ci_coordinator OWNER TO ci_coordinator_migration;
