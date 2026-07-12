-- 001_init.sql — single-database design: tenants, documents, chunks (vectors + FTS), job queue.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE orgs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    daily_token_budget BIGINT NOT NULL DEFAULT 500000,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id),
    email       TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id       BIGINT NOT NULL REFERENCES orgs(id),
    source_uri   TEXT NOT NULL,
    title        TEXT,
    content_hash TEXT NOT NULL,               -- skip re-ingest when unchanged
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','ingesting','ready','quarantined','deleted')),
    error        TEXT,                        -- set when quarantined (malformed PDF etc.)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, source_uri)
);

CREATE TABLE chunks (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id     BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    org_id          BIGINT NOT NULL REFERENCES orgs(id),     -- denormalized: tenant filter at SQL level
    chunk_index     INT NOT NULL,
    page            INT,
    text            TEXT NOT NULL,
    context_summary TEXT NOT NULL DEFAULT '',                -- contextual retrieval prefix
    embedding       vector(1024),                            -- BGE-M3
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX chunks_org ON chunks (org_id);

-- Ingestion job queue, consumed with FOR UPDATE SKIP LOCKED (no Redis needed).
CREATE TABLE ingestion_jobs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('ingest','reingest','delete')),
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','done','failed')),
    attempts     INT NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);

CREATE INDEX ingestion_jobs_queued ON ingestion_jobs (created_at) WHERE status = 'queued';

-- Per-org daily token spend, enforced as a hard cutoff.
CREATE TABLE token_usage (
    org_id      BIGINT NOT NULL REFERENCES orgs(id),
    day         DATE NOT NULL,
    tokens_used BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (org_id, day)
);
