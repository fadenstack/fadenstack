-- Run by Postgres once, when its data volume is created empty.
--
-- The databases only, and no logins: every service connects as
-- POSTGRES_USER with POSTGRES_PASSWORD, which `faden deploy` writes into
-- .env at random for each install. This script used to make two more logins
-- with fixed passwords (llm_user, faden_backend), the same on every install,
-- one of them able to read every backend table. Nothing in an install uses
-- them: `faden deploy` and `faden upgrade` switch them off on an install that
-- has them, and the development stack makes the one it needs itself
-- (`faden dev up`).

CREATE EXTENSION IF NOT EXISTS vector;

-- DB for Langfuse
CREATE DATABASE langfuse;
-- DB for your RAG service
CREATE DATABASE rag;
-- DB for Fadenstack backend
CREATE DATABASE faden_backend;
-- DB for llm gateway metadata/audit
CREATE DATABASE faden_gateway;
-- DB for MCP tool registry
CREATE DATABASE faden_mcp;
-- DB for Skills Registry
CREATE DATABASE faden_skills;
-- DB for Provenance (its migrator also makes it on installs created before)
CREATE DATABASE faden_provenance;

-- Optional: enable pgvector in rag DB
\connect rag
CREATE EXTENSION IF NOT EXISTS vector;

\connect faden_backend
CREATE EXTENSION IF NOT EXISTS vector;
