-- Reference schema for the `memory` tool. These tables are created and owned by
-- the operator (hand-applied via psql for the spike; a proper sqlx migration is
-- post-spike hardening). The tool NEVER creates them -- it only reads/writes
-- rows. They live in the SAME Postgres database `company_context` uses
-- (ParadeDB image: pg_search 0.23.0 + pgvector 0.8.1 both installed).
--
-- The BM25 index DDL below copies the exact `USING bm25 (...) WITH (...)` idiom
-- from services/api-rs/crates/centaur-session-sqlx/migrations/0012_company_context_documents.sql
-- so pg_search's `|||` / `pdb.boost` / `paradedb.score(<key_field>)` operators
-- behave identically to `company_context`.

CREATE EXTENSION IF NOT EXISTS pg_search;  -- ParadeDB BM25
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector; already installed, idempotent

-- Agent-curated memory pages (written via `memory write`). Boosted 2x over raw
-- documents in ranking.
CREATE TABLE IF NOT EXISTS memory_pages (
    slug        text PRIMARY KEY,
    title       text,
    content     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Passive corpus: raw ingested history (email, chat, meeting notes, Drive, ...).
-- Dedup key is (source, source_ref).
CREATE TABLE IF NOT EXISTS memory_documents (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source      text NOT NULL,
    stream      text NOT NULL DEFAULT '',
    source_ref  text NOT NULL,
    title       text,
    content     text NOT NULL,
    occurred_at timestamptz,
    metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz,
    UNIQUE (source, source_ref)
);

-- Vector index over chunks of pages+documents (filled by the Step-2 backfill
-- workflow, never by this tool). `kind` is 'page' | 'doc'; `ref` is the page
-- slug or the document id as text. e5-small => 384 dims.
CREATE TABLE IF NOT EXISTS memory_embeddings (
    kind          text NOT NULL,
    ref           text NOT NULL,
    chunk         int  NOT NULL,
    model         text NOT NULL,
    source_stamp  text NOT NULL,
    embedding     vector(384) NOT NULL,
    PRIMARY KEY (kind, ref, chunk)
);

-- Watermarks for cloud ingestion (Step 2). Not touched by this tool.
CREATE TABLE IF NOT EXISTS memory_ingest_cursors (
    source     text NOT NULL,
    stream     text NOT NULL DEFAULT '',
    cursor     text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, stream)
);

-- BM25 index on pages: key_field = slug so `paradedb.score(slug)` works.
DROP INDEX IF EXISTS idx_memory_pages_bm25;
CREATE INDEX idx_memory_pages_bm25
    ON memory_pages
    USING bm25 (slug, title, content)
    WITH (
        key_field = 'slug',
        text_fields = '{
            "slug": {
                "tokenizer": {"type": "keyword"}
            }
        }'
    );

-- BM25 index on documents: key_field = id so `paradedb.score(id)` works.
DROP INDEX IF EXISTS idx_memory_documents_bm25;
CREATE INDEX idx_memory_documents_bm25
    ON memory_documents
    USING bm25 (id, title, content, source, stream, occurred_at, metadata)
    WITH (
        key_field = 'id'
    );

-- Optional pgvector ANN index (personal scale is fine brute-force; add later if
-- search slows):
-- CREATE INDEX idx_memory_embeddings_hnsw
--     ON memory_embeddings USING hnsw (embedding vector_cosine_ops);
