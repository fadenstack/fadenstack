CREATE EXTENSION IF NOT EXISTS vector;

-- DB for Langfuse
CREATE DATABASE langfuse;
-- DB for your RAG service
CREATE DATABASE rag;
-- DB for Fadenstack backend (used by local uv-run dev server)
CREATE DATABASE faden_backend;
-- DB for llm gateway metadata/audit
CREATE DATABASE faden_gateway;
-- DB for MCP tool registry
CREATE DATABASE faden_mcp;
-- DB for Skills Registry
CREATE DATABASE faden_skills;

-- Dedicated least-privileged role for faden_gateway
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'llm_user') THEN
        CREATE ROLE llm_user LOGIN PASSWORD 'llm_user';
    END IF;
END
$$;

-- Role for faden_backend (local dev server)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'faden_backend') THEN
        CREATE ROLE faden_backend LOGIN PASSWORD 'faden_backend';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE faden_gateway TO llm_user;
GRANT CONNECT ON DATABASE faden_mcp TO llm_user;
GRANT CONNECT ON DATABASE faden_skills TO llm_user;
GRANT CONNECT ON DATABASE faden_backend TO faden_backend;
GRANT ALL PRIVILEGES ON DATABASE faden_backend TO faden_backend;

-- Optional: enable pgvector in rag DB
\connect rag
CREATE EXTENSION IF NOT EXISTS vector;

-- Grant schema privileges for faden_backend (needed for Alembic migrations)
\connect faden_backend
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO faden_backend;

-- Allow llm_user (used by faden-gateway) to read the JWT secret from the backend DB at startup.
-- The API reads faden_gateway.jwt_secret from system_setting_secret on container start so it
-- stays in sync with System Settings without needing image rebuilds.
GRANT CONNECT ON DATABASE faden_backend TO llm_user;
GRANT USAGE ON SCHEMA public TO llm_user;
-- Grant SELECT on any table that faden_backend role creates (including post-migration tables).
ALTER DEFAULT PRIVILEGES FOR ROLE faden_backend IN SCHEMA public GRANT SELECT ON TABLES TO llm_user;
-- Also cover tables created by postgres (Alembic often runs as superuser).
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO llm_user;

-- Grant least privileges for gateway tables in faden_gateway
\connect faden_gateway
GRANT USAGE, CREATE ON SCHEMA public TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO llm_user;

-- Grant least privileges for MCP registry tables in faden_mcp
\connect faden_mcp
GRANT USAGE, CREATE ON SCHEMA public TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO llm_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO llm_user;
