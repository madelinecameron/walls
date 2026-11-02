-- Create the walls database and role on the shared Postgres. Run once
-- as the postgres superuser; running it again is harmless and resets the
-- password to the given value. The password comes in as a psql variable on
-- stdin, never on the command line:
--
--   { printf '\\set password %s\n' "$PASSWORD"; cat postgres.sql; } |
--     su postgres -c 'psql -v ON_ERROR_STOP=1 -d postgres'
--
-- The role owns its database and applies migrations when the service starts.
-- vector needs a superuser, so it is created here rather than in a migration.

SET log_statement = 'none';
SET log_min_duration_statement = -1;

SELECT 'CREATE ROLE walls LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'walls') \gexec

ALTER ROLE walls LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'password';

-- template0 and UTF8 because the cluster was initdb'd as SQL_ASCII, and psycopg
-- returns bytes for text in a SQL_ASCII database.
SELECT 'CREATE DATABASE walls OWNER walls TEMPLATE template0 ENCODING ''UTF8'' '
       'LC_COLLATE ''C.UTF-8'' LC_CTYPE ''C.UTF-8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'walls') \gexec

\connect walls
CREATE EXTENSION IF NOT EXISTS vector;
ALTER SCHEMA public OWNER TO walls;
