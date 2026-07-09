from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import client as memory_client
from client import MemoryClient

from centaur_sdk.tool_sdk import ToolContext, reset_tool_context, set_tool_context


class _FakeConnection:
    """Async DB double. `fetch` results are dequeued in call order."""

    def __init__(self, *, fetch_results=None, fetchrow_result=None, fetchval_result=None) -> None:
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_result = fetchrow_result
        self.fetchval_result = fetchval_result
        self.fetch_calls = []
        self.fetchrow_calls = []
        self.fetchval_calls = []
        self.closed = False

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self.fetchrow_result

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return self.fetchval_result

    async def close(self):
        self.closed = True


def _patch_connect(monkeypatch, fake):
    async def fake_connect(*args, **kwargs):
        return fake

    monkeypatch.setattr(memory_client.asyncpg, "connect", fake_connect)


# -- input guards -----------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   "])
def test_search_rejects_empty_query(query):
    result = MemoryClient("postgresql://example").search(query)
    assert result == {"status": "error", "error": "query cannot be empty"}


@pytest.mark.parametrize("ref", ["", "   "])
def test_page_rejects_empty_ref(ref):
    result = MemoryClient("postgresql://example").page(ref)
    assert result == {"status": "error", "error": "ref cannot be empty"}


def test_write_rejects_empty_slug():
    result = MemoryClient("postgresql://example").write("  ", content="x")
    assert result == {"status": "error", "error": "slug cannot be empty"}


def test_write_rejects_empty_content():
    result = MemoryClient("postgresql://example").write("slug", content="   ")
    assert result == {"status": "error", "error": "content cannot be empty"}


# -- credential resolution (mirrors company_context) ------------------------


def test_default_database_url_uses_dsn_env(monkeypatch):
    monkeypatch.setenv("CENTAUR_POSTGRES_DSN", "postgresql://scoped")
    monkeypatch.setenv("DATABASE_URL", "postgresql://raw-app-db")
    assert MemoryClient()._require_database_url() == "postgresql://scoped"


def test_default_database_url_uses_tool_context_secret(monkeypatch):
    monkeypatch.delenv("CENTAUR_POSTGRES_DSN", raising=False)
    token = set_tool_context(
        ToolContext(name="memory", secrets={"CENTAUR_POSTGRES_DSN": "postgresql://ctx"})
    )
    try:
        assert MemoryClient()._require_database_url() == "postgresql://ctx"
    finally:
        reset_tool_context(token)


def test_default_database_url_does_not_fall_back_to_raw_database_url(monkeypatch):
    monkeypatch.delenv("CENTAUR_POSTGRES_DSN", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://raw-app-db")
    token = set_tool_context(ToolContext(name="memory", secrets={}))
    try:
        with pytest.raises(RuntimeError, match="CENTAUR_POSTGRES_DSN is required"):
            MemoryClient()._require_database_url()
    finally:
        reset_tool_context(token)


def test_database_name_defaults_to_ai_v2(monkeypatch):
    monkeypatch.delenv("MEMORY_POSTGRES_DATABASE", raising=False)
    monkeypatch.delenv("COMPANY_CONTEXT_POSTGRES_DATABASE", raising=False)
    assert memory_client._postgres_database_name() == "ai_v2"


def test_database_name_prefers_memory_override(monkeypatch):
    monkeypatch.setenv("MEMORY_POSTGRES_DATABASE", "verso")
    monkeypatch.setenv("COMPANY_CONTEXT_POSTGRES_DATABASE", "centaur")
    assert memory_client._postgres_database_name() == "verso"


def test_database_name_falls_back_to_company_context_env(monkeypatch):
    monkeypatch.delenv("MEMORY_POSTGRES_DATABASE", raising=False)
    monkeypatch.setenv("COMPANY_CONTEXT_POSTGRES_DATABASE", "centaur")
    assert memory_client._postgres_database_name() == "centaur"


# -- BM25-only search -------------------------------------------------------


def _page_row(slug="onboarding", score=1.0):
    return {
        "slug": slug,
        "title": "Onboarding notes",
        "content": "How we onboard new engineers at the company.",
        "updated_at": dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC),
        "score": score,
    }


def _doc_row(doc_id=7, source="gmail", score=1.0):
    return {
        "id": doc_id,
        "source": source,
        "stream": "",
        "title": "Re: onboarding",
        "content": "Email thread about onboarding logistics.",
        "occurred_at": dt.datetime(2026, 5, 7, 9, 0, tzinfo=dt.UTC),
        "created_at": dt.datetime(2026, 5, 7, 9, 5, tzinfo=dt.UTC),
        "metadata": {},
        "score": score,
    }


def test_search_bm25_only_merges_pages_and_docs_and_boosts_pages(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDER_URL", raising=False)
    # Page has a LOWER raw BM25 score than the doc, but the 2x curated boost
    # should lift it above the doc.
    fake = _FakeConnection(fetch_results=[[_page_row(score=1.0)], [_doc_row(score=1.5)]])
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").search("onboarding", limit=5)

    assert result["status"] == "ok"
    assert result["mode"] == "bm25"
    assert result["count"] == 2
    # page boosted 1.0 * 2.0 = 2.0 > doc 1.5
    assert result["results"][0]["kind"] == "page"
    assert result["results"][0]["ref"] == "onboarding"
    assert result["results"][0]["score"] == pytest.approx(2.0)
    assert result["results"][1]["kind"] == "doc"
    assert result["results"][1]["ref"] == "doc:7"
    assert result["results"][1]["source"] == "gmail"
    assert result["results"][1]["score"] == pytest.approx(1.5)
    # Only two fetches (pages + docs), no vector lane.
    assert len(fake.fetch_calls) == 2
    assert fake.closed is True


def test_search_uses_pdb_boost_idioms(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDER_URL", raising=False)
    fake = _FakeConnection(fetch_results=[[], []])
    _patch_connect(monkeypatch, fake)

    MemoryClient("postgresql://example").search("state root mismatch", limit=5)

    page_query, page_args = fake.fetch_calls[0]
    assert "FROM memory_pages" in page_query
    assert "paradedb.score(slug)" in page_query
    assert "title ||| $1::text::pdb.boost(8) OR content ||| $1::text::pdb.boost(2)" in page_query
    assert "title ||| $2::text::pdb.boost(4) OR content ||| $2::text" in page_query
    # query + non-stopword terms (state, root, mismatch) + limit
    assert page_args == ("state root mismatch", "state", "root", "mismatch", 5)

    doc_query, doc_args = fake.fetch_calls[1]
    assert "FROM memory_documents" in doc_query
    assert "paradedb.score(id)" in doc_query
    assert doc_args == ("state root mismatch", "state", "root", "mismatch", None, 5)


def test_search_with_source_skips_pages_lane(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDER_URL", raising=False)
    fake = _FakeConnection(fetch_results=[[_doc_row(source="slack")]])
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").search("onboarding", source="slack")

    assert result["status"] == "ok"
    # Exactly one fetch -- documents only.
    assert len(fake.fetch_calls) == 1
    doc_query, doc_args = fake.fetch_calls[0]
    assert "FROM memory_documents" in doc_query
    assert doc_args[-2] == "slack"
    assert result["results"][0]["ref"] == "doc:7"


def test_search_clamps_limit(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDER_URL", raising=False)
    fake = _FakeConnection(fetch_results=[[], []])
    _patch_connect(monkeypatch, fake)

    MemoryClient("postgresql://example").search("hello", limit=999)

    _, page_args = fake.fetch_calls[0]
    assert page_args[-1] == 50  # MAX_SEARCH_LIMIT


# -- hybrid search (embedder up) --------------------------------------------


def test_search_hybrid_fuses_with_rrf_and_page_boost(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDER_URL", "http://embedder")
    monkeypatch.setattr(memory_client, "_embed_query", lambda text: [0.1, 0.2, 0.3])

    vector_rows = [
        {
            "kind": "page",
            "ref": "onboarding",
            "title": "Onboarding notes",
            "content": "How we onboard new engineers.",
            "recency": dt.datetime(2026, 5, 8, 12, 0, tzinfo=dt.UTC),
            "source": None,
            "distance": 0.10,
        },
        {
            "kind": "doc",
            "ref": "7",
            "title": "Re: onboarding",
            "content": "Email thread about onboarding.",
            "recency": dt.datetime(2026, 5, 7, 9, 0, tzinfo=dt.UTC),
            "source": "gmail",
            "distance": 0.20,
        },
    ]
    fake = _FakeConnection(
        fetch_results=[[_page_row()], [_doc_row()], vector_rows]
    )
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").search("onboarding", limit=5)

    assert result["mode"] == "hybrid"
    assert len(fake.fetch_calls) == 3  # pages, docs, vector
    vector_query, vector_args = fake.fetch_calls[2]
    assert "embedding <=> $1::vector" in vector_query
    assert vector_args[0] == "[0.1,0.2,0.3]"
    # Both hits appear once each (fused across lanes by ref).
    refs = {row["ref"] for row in result["results"]}
    assert refs == {"onboarding", "doc:7"}
    # Curated page wins after the 2x fused-score boost.
    assert result["results"][0]["ref"] == "onboarding"
    assert "rrf" not in result["results"][0]


def test_search_falls_back_to_bm25_when_embedder_fails(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDER_URL", "http://embedder")
    monkeypatch.setattr(memory_client, "_embed_query", lambda text: None)
    fake = _FakeConnection(fetch_results=[[_page_row()], [_doc_row()]])
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").search("onboarding")

    assert result["mode"] == "bm25"
    assert len(fake.fetch_calls) == 2  # no vector lane
    assert result["count"] == 2


def test_embed_query_returns_none_without_url(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDER_URL", raising=False)
    assert memory_client._embed_query("hello") is None


def test_embed_query_swallows_transport_errors(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDER_URL", "http://embedder")

    def boom(*args, **kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr(memory_client, "_embed_texts", boom)
    assert memory_client._embed_query("hello") is None


# -- page read --------------------------------------------------------------


def test_page_reads_document_by_ref(monkeypatch):
    fake = _FakeConnection(
        fetchrow_result={
            "id": 42,
            "source": "granola",
            "stream": "",
            "title": "Standup notes",
            "content": "x" * 100,
            "occurred_at": dt.datetime(2026, 5, 6, 15, 0, tzinfo=dt.UTC),
            "created_at": None,
            "updated_at": None,
            "metadata": {},
        }
    )
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").page("doc:42")

    assert result["status"] == "ok"
    assert result["kind"] == "doc"
    assert result["ref"] == "doc:42"
    assert result["source"] == "granola"
    assert result["total_chars"] == 100
    assert result["truncated"] is False
    query, args = fake.fetchrow_calls[0]
    assert "FROM memory_documents" in query
    assert args == (42,)


def test_page_reads_document_bounded(monkeypatch):
    fake = _FakeConnection(
        fetchrow_result={
            "id": 42,
            "source": "granola",
            "stream": "",
            "title": "Standup",
            "content": "y" * 500,
            "occurred_at": None,
            "created_at": None,
            "updated_at": None,
            "metadata": {},
        }
    )
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").page("doc:42", max_chars=100)

    assert result["chars"] == 100
    assert result["total_chars"] == 500
    assert result["truncated"] is True
    assert result["content"] == "y" * 100


def test_page_exact_slug(monkeypatch):
    fake = _FakeConnection(
        fetchrow_result={
            "slug": "team/roster",
            "title": "Team roster",
            "content": "The team roster.",
            "created_at": None,
            "updated_at": dt.datetime(2026, 5, 8, tzinfo=dt.UTC),
        }
    )
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").page("team/roster")

    assert result["kind"] == "page"
    assert result["ref"] == "team/roster"
    assert result["content"] == "The team roster."
    # Exact lookup found it, so no second (fuzzy) query.
    assert len(fake.fetchrow_calls) == 1
    query, args = fake.fetchrow_calls[0]
    assert "WHERE slug = $1" in query
    assert args == ("team/roster",)


def test_page_not_found(monkeypatch):
    fake = _FakeConnection(fetchrow_result=None)
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").page("missing")

    assert result == {"status": "error", "error": "page not found: missing"}
    # exact then fuzzy => two lookups
    assert len(fake.fetchrow_calls) == 2
    assert "LIKE" in fake.fetchrow_calls[1][0]


# -- write ------------------------------------------------------------------


def test_write_creates_page(monkeypatch):
    fake = _FakeConnection(fetchval_result=True)
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").write("profile/hugo", content="# Hugo\nCEO.")

    assert result == {
        "status": "ok",
        "slug": "profile/hugo",
        "title": "Hugo",  # derived from the markdown heading
        "created": True,
        "action": "created",
    }
    query, args = fake.fetchval_calls[0]
    assert "INSERT INTO memory_pages" in query
    assert "ON CONFLICT (slug) DO UPDATE" in query
    assert "updated_at = now()" in query
    assert args == ("profile/hugo", "Hugo", "# Hugo\nCEO.")


def test_write_updates_page_with_explicit_title(monkeypatch):
    fake = _FakeConnection(fetchval_result=False)
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").write(
        "profile/hugo", content="Updated.", title="Hugo S."
    )

    assert result["action"] == "updated"
    assert result["created"] is False
    assert result["title"] == "Hugo S."


def test_write_derives_title_from_slug_leaf(monkeypatch):
    fake = _FakeConnection(fetchval_result=True)
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").write("people/alice_smith", content="Notes.")

    assert result["title"] == "alice smith"


# -- list / status ----------------------------------------------------------


def test_list_returns_pages_and_source_counts(monkeypatch):
    fake = _FakeConnection(
        fetch_results=[
            [{"slug": "p1", "title": "Page one", "updated_at": dt.datetime(2026, 5, 8, tzinfo=dt.UTC)}],
            [{"source": "gmail", "count": 120, "latest": dt.datetime(2026, 5, 9, tzinfo=dt.UTC)}],
        ]
    )
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").list(limit=5)

    assert result["status"] == "ok"
    assert result["pages"] == [
        {"slug": "p1", "title": "Page one", "updated_at": "2026-05-08T00:00:00+00:00"}
    ]
    assert result["sources"] == [
        {"source": "gmail", "count": 120, "latest": "2026-05-09T00:00:00+00:00"}
    ]


def test_status_reports_counts_and_embedder(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDER_URL", "http://embedder")
    monkeypatch.setattr(memory_client, "_embed_query", lambda text: [0.0])
    fake = _FakeConnection(
        fetchrow_result={
            "pages": 3,
            "documents": 500,
            "embedded_rows": 480,
            "rows_missing_embeddings": 23,
        }
    )
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").status()

    assert result["status"] == "ok"
    assert result["connected"] is True
    assert result["pages"] == 3
    assert result["documents"] == 500
    assert result["rows_missing_embeddings"] == 23
    assert result["embedder"] == {
        "configured": True,
        "url": "http://embedder",
        "reachable": True,
    }


def test_status_reports_embedder_unconfigured(monkeypatch):
    monkeypatch.delenv("MEMORY_EMBEDDER_URL", raising=False)
    fake = _FakeConnection(
        fetchrow_result={
            "pages": 0,
            "documents": 0,
            "embedded_rows": 0,
            "rows_missing_embeddings": 0,
        }
    )
    _patch_connect(monkeypatch, fake)

    result = MemoryClient("postgresql://example").status()

    assert result["embedder"] == {"configured": False, "url": None, "reachable": False}
