"""Personal memory: hybrid BM25 + embedding search over the user's history.

BM25-first and hybrid-ready: search always runs a pg_search (ParadeDB) BM25 pass
over both `memory_pages` (agent-curated) and `memory_documents` (passive corpus).
When ``MEMORY_EMBEDDER_URL`` is set and reachable, it ALSO runs a pgvector cosine
pass and fuses the two ranked lists with Reciprocal Rank Fusion (K=60), boosting
curated pages 2x. ANY embedder failure degrades silently to BM25-only -- search
must never fail because of embeddings.

Ranking is a port of the local Verso memory provider
(papeete/desktop/orchestrator/src/http/lexical-provider.ts:262-335).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg

from centaur_sdk.tool_sdk import secret

DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50

# pg_search boost idioms -- copied from company_context/client.py so the `|||`
# match operator and `pdb.boost` behave identically.
TITLE_MATCH_BOOST = 4
EXACT_QUERY_TITLE_BOOST = 8
EXACT_QUERY_BODY_BOOST = 2

# Hybrid ranking (port of lexical-provider.ts constants).
RRF_K = 60
PAGE_RANK_BOOST = 2.0  # curated pages beat raw documents for the same terms

DEFAULT_PREVIEW_CHARS = 280
MAX_PAGE_CHARS = 20_000

# Postgres credential resolution -- same DSN plumbing as company_context so the
# memory tool reaches the same database (client.py:82-101 pattern).
MEMORY_DSN_ENV = "CENTAUR_POSTGRES_DSN"
MEMORY_DATABASE_ENV = "MEMORY_POSTGRES_DATABASE"
COMPANY_CONTEXT_DATABASE_ENV = "COMPANY_CONTEXT_POSTGRES_DATABASE"
DEFAULT_POSTGRES_DATABASE = "ai_v2"

# Embedder (HuggingFace text-embeddings-inference). Optional; unset => BM25-only.
EMBEDDER_URL_ENV = "MEMORY_EMBEDDER_URL"
EMBED_TIMEOUT_SECONDS = 3.0
EMBED_QUERY_PREFIX = "query: "

_SEARCH_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "how", "i", "if", "in", "into", "is", "it", "of", "on", "or", "our",
    "that", "the", "their", "there", "these", "they", "this", "to", "was",
    "we", "were", "what", "when", "where", "which", "who", "why", "will",
    "with",
}


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    """Clamp integer tool inputs to predictable output bounds."""
    return max(minimum, min(int(value), maximum))


def _scoped_database_url() -> str:
    value = os.getenv(MEMORY_DSN_ENV)  # noqa: TID251
    if value is None:
        value = secret(MEMORY_DSN_ENV, default="")
    value = value.strip()
    if value == MEMORY_DSN_ENV:
        return ""
    return value


def _database_url_with_name(value: str, database: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc and parsed.path in ("", "/"):
        return urlunparse(parsed._replace(path=f"/{database}"))
    return value


def _postgres_database_name() -> str:
    # Default to the SAME database company_context uses so the memory tables are
    # co-located; MEMORY_POSTGRES_DATABASE overrides if the operator wants split.
    value = (
        os.getenv(MEMORY_DATABASE_ENV)  # noqa: TID251
        or os.getenv(COMPANY_CONTEXT_DATABASE_ENV)  # noqa: TID251
        or DEFAULT_POSTGRES_DATABASE
    )
    return value.strip() or DEFAULT_POSTGRES_DATABASE


def _embedder_url() -> str:
    value = os.getenv(EMBEDDER_URL_ENV)  # noqa: TID251
    if value is None:
        try:
            value = secret(EMBEDDER_URL_ENV, default="")
        except Exception:
            value = ""
    value = (value or "").strip()
    if value == EMBEDDER_URL_ENV:
        return ""
    return value


def _embed_texts(url: str, inputs: list[str]) -> list[list[float]]:
    """POST to a HuggingFace text-embeddings-inference `/embed` endpoint."""
    endpoint = url.rstrip("/") + "/embed"
    payload = json.dumps({"inputs": inputs}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=EMBED_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read())
    if not isinstance(data, list) or not data:
        raise ValueError("embedder returned an empty or malformed response")
    return [[float(x) for x in vector] for vector in data]


def _embed_query(text: str) -> list[float] | None:
    """Embed a search query with the e5 `query: ` prefix.

    Returns None on ANY failure (env unset, connection refused, timeout, bad
    response) so search silently degrades to BM25-only.
    """
    url = _embedder_url()
    if not url:
        return None
    try:
        vectors = _embed_texts(url, [f"{EMBED_QUERY_PREFIX}{text}"])
        return vectors[0]
    except Exception:
        return None


def _vector_literal(vector: list[float]) -> str:
    """Render a float list as a pgvector text literal for `$n::vector`."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _isoformat(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _search_terms(query: str) -> list[str]:
    """Extract unique content terms, falling back when filtering removes everything."""
    seen: set[str] = set()
    all_terms: list[str] = []
    filtered_terms: list[str] = []
    for match in _SEARCH_TERM_RE.finditer(query):
        term = match.group(0).strip()
        if len(term) < 2:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        all_terms.append(term)
        if key not in _STOP_WORDS:
            filtered_terms.append(term)
    return filtered_terms or all_terms or [query]


def _search_where_clause(term_count: int, content_col: str) -> str:
    """ParadeDB query that boosts exact matches, then OR-matches terms.

    Mirrors company_context._search_where_clause but parameterises the body
    column (memory tables call it `content`, not `body`).
    """
    clauses = [
        "("
        f"title ||| $1::text::pdb.boost({EXACT_QUERY_TITLE_BOOST}) "
        f"OR {content_col} ||| $1::text::pdb.boost({EXACT_QUERY_BODY_BOOST})"
        ")"
    ]
    for index in range(2, term_count + 2):
        clauses.append(
            f"(title ||| ${index}::text::pdb.boost({TITLE_MATCH_BOOST}) "
            f"OR {content_col} ||| ${index}::text)"
        )
    return " OR ".join(clauses)


def _body_preview(body: str, *, query: str, max_chars: int = DEFAULT_PREVIEW_CHARS) -> str:
    """Compact preview centered on the first query-term hit when possible."""
    normalized = _normalize_text(body)
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized

    terms = _search_terms(query) if query else []
    start = 0
    lowered = normalized.lower()
    for term in terms:
        index = lowered.find(term.lower())
        if index >= 0:
            start = max(0, index - max_chars // 3)
            break

    end = min(len(normalized), start + max_chars)
    snippet = normalized[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(normalized):
        snippet = f"{snippet}..."
    return snippet


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _derive_title(slug: str, content: str) -> str:
    """Title from a markdown heading, else the slug leaf (port of deriveTitle)."""
    heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if heading:
        return heading.group(1).strip()
    leaf = slug.split("/")[-1] or slug
    return re.sub(r"[-_]+", " ", leaf).strip()


class MemoryClient:
    """Query and write the user's personal memory tables."""

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = (database_url or _scoped_database_url()).strip()

    def _require_database_url(self) -> str:
        if not self._database_url:
            raise RuntimeError(f"{MEMORY_DSN_ENV} is required for memory access")
        return self._database_url

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            _database_url_with_name(self._require_database_url(), _postgres_database_name()),
            command_timeout=30,
        )

    # -- search -----------------------------------------------------------

    def _bm25_hits(
        self,
        page_rows: list[Any],
        doc_rows: list[Any],
        *,
        query: str,
    ) -> list[dict[str, Any]]:
        """Merge page + document BM25 rows into one ranked list (raw score desc).

        Page relevance stays raw here; the 2x curated boost is applied once at
        the fusion/finalisation step (see _fuse).
        """
        hits: list[dict[str, Any]] = []
        for row in page_rows:
            slug = str(_row_value(row, "slug", ""))
            content = str(_row_value(row, "content", "") or "")
            hits.append(
                {
                    "kind": "page",
                    "ref": slug,
                    "title": str(_row_value(row, "title", "") or ""),
                    "source": None,
                    "score": float(_row_value(row, "score", 0.0) or 0.0),
                    "recency": _isoformat(_row_value(row, "updated_at")) or "",
                    "snippet": _body_preview(content, query=query),
                }
            )
        for row in doc_rows:
            doc_id = str(_row_value(row, "id", ""))
            content = str(_row_value(row, "content", "") or "")
            source = str(_row_value(row, "source", "") or "")
            recency = _isoformat(_row_value(row, "occurred_at")) or _isoformat(
                _row_value(row, "created_at")
            )
            hits.append(
                {
                    "kind": "doc",
                    "ref": f"doc:{doc_id}",
                    "title": str(_row_value(row, "title", "") or ""),
                    "source": source,
                    "score": float(_row_value(row, "score", 0.0) or 0.0),
                    "recency": recency or "",
                    "snippet": _body_preview(content, query=query),
                }
            )
        hits.sort(key=lambda hit: (hit["score"], hit["recency"]), reverse=True)
        return hits

    def _vector_hits(self, rows: list[Any], *, query: str) -> list[dict[str, Any]]:
        """Vector rows (already ordered by cosine distance asc) into ranked hits."""
        hits: list[dict[str, Any]] = []
        for row in rows:
            kind = str(_row_value(row, "kind", ""))
            raw_ref = str(_row_value(row, "ref", ""))
            content = str(_row_value(row, "content", "") or "")
            source = _row_value(row, "source")
            distance = float(_row_value(row, "distance", 1.0) or 1.0)
            ref = raw_ref if kind == "page" else f"doc:{raw_ref}"
            hits.append(
                {
                    "kind": kind,
                    "ref": ref,
                    "title": str(_row_value(row, "title", "") or ""),
                    "source": (str(source) if source else None) if kind == "doc" else None,
                    "score": 1.0 - distance,  # cosine similarity, display only
                    "recency": _isoformat(_row_value(row, "recency")) or "",
                    "snippet": _body_preview(content, query=query),
                }
            )
        return hits

    def _fuse(
        self,
        bm25_hits: list[dict[str, Any]],
        vector_hits: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Reciprocal Rank Fusion of the two ranked lists, with a 2x page boost.

        Port of lexical-provider.ts:262-291. In BM25-only mode (no vector list)
        the raw BM25 ranking is returned with the page boost applied to the raw
        score. In hybrid mode the two lists are fused by rank (RRF, K=60) and the
        fused score of curated pages is multiplied by 2.0 before the final sort.
        """
        if not vector_hits:
            merged = [dict(hit) for hit in bm25_hits]
            for hit in merged:
                boost = PAGE_RANK_BOOST if hit["kind"] == "page" else 1.0
                hit["score"] = hit["score"] * boost
            merged.sort(key=lambda hit: (hit["score"], hit["recency"]), reverse=True)
            return merged[:limit]

        fused: dict[str, dict[str, Any]] = {}
        for ranked in (bm25_hits, vector_hits):
            for rank, hit in enumerate(ranked):
                key = hit["ref"]
                contribution = 1.0 / (RRF_K + rank + 1)
                existing = fused.get(key)
                if existing is not None:
                    existing["rrf"] += contribution
                else:
                    fused[key] = {**hit, "rrf": contribution}

        results = list(fused.values())
        for hit in results:
            boost = PAGE_RANK_BOOST if hit["kind"] == "page" else 1.0
            hit["score"] = hit["rrf"] * boost
        results.sort(key=lambda hit: (hit["score"], hit["recency"]), reverse=True)
        for hit in results:
            hit.pop("rrf", None)
        return results[:limit]

    async def _search_async(
        self,
        *,
        query: str,
        limit: int,
        source: str | None,
    ) -> dict[str, Any]:
        conn = await self._connect()
        try:
            terms = _search_terms(query)
            search_terms = [query, *terms]
            where = _search_where_clause(len(terms), "content")

            page_rows: list[Any] = []
            # A source filter targets ingested documents; pages have no source,
            # so we skip the pages lane entirely when a source is requested.
            if source is None:
                page_limit_param = len(search_terms) + 1
                page_rows = await conn.fetch(
                    f"""
                    SELECT slug, title, content, updated_at,
                           paradedb.score(slug) AS score
                    FROM memory_pages
                    WHERE {where}
                    ORDER BY paradedb.score(slug) DESC, updated_at DESC NULLS LAST
                    LIMIT ${page_limit_param}
                    """,
                    *search_terms,
                    limit,
                )

            doc_source_param = len(search_terms) + 1
            doc_limit_param = len(search_terms) + 2
            doc_rows = await conn.fetch(
                f"""
                SELECT id, source, stream, title, content, occurred_at, created_at,
                       metadata, paradedb.score(id) AS score
                FROM memory_documents
                WHERE {where}
                  AND (${doc_source_param}::text IS NULL OR source = ${doc_source_param})
                ORDER BY paradedb.score(id) DESC,
                         COALESCE(occurred_at, created_at) DESC NULLS LAST
                LIMIT ${doc_limit_param}
                """,
                *search_terms,
                source,
                limit,
            )

            bm25_hits = self._bm25_hits(page_rows, doc_rows, query=query)

            vector_hits: list[dict[str, Any]] = []
            query_vector = _embed_query(query)
            mode = "bm25"
            if query_vector is not None:
                mode = "hybrid"
                vector_rows = await conn.fetch(
                    self._vector_sql(include_pages=source is None),
                    _vector_literal(query_vector),
                    source,
                    limit,
                )
                vector_hits = self._vector_hits(vector_rows, query=query)

            results = self._fuse(bm25_hits, vector_hits, limit=limit)
            return {
                "status": "ok",
                "query": query,
                "source": source,
                "mode": mode,
                "count": len(results),
                "results": results,
            }
        finally:
            await conn.close()

    @staticmethod
    def _vector_sql(*, include_pages: bool) -> str:
        """Cosine top-K over embeddings, best chunk per row, joined to rows.

        `$1` = query vector literal, `$2` = optional source filter, `$3` = limit.
        """
        doc_vec = """
            SELECT 'doc'::text AS kind, d.id::text AS ref, d.title AS title,
                   d.content AS content,
                   COALESCE(d.occurred_at, d.created_at) AS recency,
                   d.source AS source,
                   MIN(e.embedding <=> $1::vector) AS distance
            FROM memory_embeddings e
            JOIN memory_documents d ON d.id::text = e.ref
            WHERE e.kind = 'doc' AND ($2::text IS NULL OR d.source = $2)
            GROUP BY d.id, d.title, d.content, d.occurred_at, d.created_at, d.source
        """
        if not include_pages:
            union = doc_vec
        else:
            page_vec = """
                SELECT 'page'::text AS kind, p.slug AS ref, p.title AS title,
                       p.content AS content, p.updated_at AS recency,
                       NULL::text AS source,
                       MIN(e.embedding <=> $1::vector) AS distance
                FROM memory_embeddings e
                JOIN memory_pages p ON p.slug = e.ref
                WHERE e.kind = 'page'
                GROUP BY p.slug, p.title, p.content, p.updated_at
            """
            union = f"{page_vec}\n            UNION ALL\n{doc_vec}"
        return f"""
            SELECT kind, ref, title, content, recency, source, distance
            FROM (
{union}
            ) fused
            ORDER BY distance ASC
            LIMIT $3
        """

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        source: str | None = None,
    ) -> dict:
        """Hybrid-ready BM25-first search over pages and documents."""
        normalized_query = query.strip()
        if not normalized_query:
            return {"status": "error", "error": "query cannot be empty"}
        try:
            return asyncio.run(
                self._search_async(
                    query=normalized_query,
                    limit=_clamp(limit, minimum=1, maximum=MAX_SEARCH_LIMIT),
                    source=source.strip() if source else None,
                )
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # -- page read --------------------------------------------------------

    async def _page_async(self, ref: str, max_chars: int | None) -> dict[str, Any]:
        conn = await self._connect()
        try:
            doc_match = re.fullmatch(r"doc:(\d+)", ref)
            if doc_match:
                row = await conn.fetchrow(
                    """
                    SELECT id, source, stream, title, content, occurred_at,
                           created_at, updated_at, metadata
                    FROM memory_documents
                    WHERE id = $1
                    """,
                    int(doc_match.group(1)),
                )
                if not row:
                    return {"status": "error", "error": f"document not found: {ref}"}
                return self._page_payload(
                    kind="doc",
                    ref=f"doc:{_row_value(row, 'id', '')}",
                    title=str(_row_value(row, "title", "") or ""),
                    source=str(_row_value(row, "source", "") or ""),
                    occurred_at=_isoformat(_row_value(row, "occurred_at")),
                    body=str(_row_value(row, "content", "") or ""),
                    max_chars=max_chars,
                )

            row = await conn.fetchrow(
                "SELECT slug, title, content, created_at, updated_at "
                "FROM memory_pages WHERE slug = $1",
                ref,
            )
            if not row:
                row = await conn.fetchrow(
                    """
                    SELECT slug, title, content, created_at, updated_at
                    FROM memory_pages
                    WHERE lower(slug) LIKE '%' || lower($1) || '%'
                       OR lower(coalesce(title, '')) LIKE '%' || lower($1) || '%'
                    ORDER BY length(slug) ASC, updated_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    ref,
                )
            if not row:
                return {"status": "error", "error": f"page not found: {ref}"}
            return self._page_payload(
                kind="page",
                ref=str(_row_value(row, "slug", "")),
                title=str(_row_value(row, "title", "") or ""),
                source=None,
                occurred_at=_isoformat(_row_value(row, "updated_at")),
                body=str(_row_value(row, "content", "") or ""),
                max_chars=max_chars,
            )
        finally:
            await conn.close()

    @staticmethod
    def _page_payload(
        *,
        kind: str,
        ref: str,
        title: str,
        source: str | None,
        occurred_at: str | None,
        body: str,
        max_chars: int | None,
    ) -> dict[str, Any]:
        content = body if max_chars is None else body[:max_chars]
        return {
            "status": "ok",
            "kind": kind,
            "ref": ref,
            "title": title,
            "source": source,
            "occurred_at": occurred_at,
            "chars": len(content),
            "total_chars": len(body),
            "truncated": max_chars is not None and len(body) > max_chars,
            "content": content,
        }

    def page(self, ref: str, max_chars: int = 0) -> dict:
        """Read a full page (by slug, exact then fuzzy) or a `doc:<id>` document."""
        normalized = ref.strip()
        if not normalized:
            return {"status": "error", "error": "ref cannot be empty"}
        try:
            return asyncio.run(
                self._page_async(normalized, max_chars if max_chars > 0 else None)
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # -- write ------------------------------------------------------------

    async def _write_async(self, slug: str, title: str | None, content: str) -> dict[str, Any]:
        conn = await self._connect()
        try:
            resolved_title = title if title is not None else _derive_title(slug, content)
            created = await conn.fetchval(
                """
                INSERT INTO memory_pages (slug, title, content, created_at, updated_at)
                VALUES ($1, $2, $3, now(), now())
                ON CONFLICT (slug) DO UPDATE SET
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    updated_at = now()
                RETURNING (xmax = 0) AS created
                """,
                slug,
                resolved_title,
                content,
            )
            return {
                "status": "ok",
                "slug": slug,
                "title": resolved_title,
                "created": bool(created),
                "action": "created" if created else "updated",
            }
        finally:
            await conn.close()

    def write(self, slug: str, content: str, title: str | None = None) -> dict:
        """Upsert a curated memory page, bumping updated_at."""
        normalized_slug = slug.strip()
        if not normalized_slug:
            return {"status": "error", "error": "slug cannot be empty"}
        if not content.strip():
            return {"status": "error", "error": "content cannot be empty"}
        try:
            return asyncio.run(
                self._write_async(
                    normalized_slug,
                    title.strip() if title else None,
                    content,
                )
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # -- list -------------------------------------------------------------

    async def _list_async(self, *, limit: int, source: str | None) -> dict[str, Any]:
        conn = await self._connect()
        try:
            page_rows = await conn.fetch(
                """
                SELECT slug, title, updated_at
                FROM memory_pages
                ORDER BY updated_at DESC NULLS LAST
                LIMIT $1
                """,
                limit,
            )
            source_rows = await conn.fetch(
                """
                SELECT source, COUNT(*)::bigint AS count, MAX(occurred_at) AS latest
                FROM memory_documents
                WHERE ($1::text IS NULL OR source = $1)
                GROUP BY source
                ORDER BY count DESC, source ASC
                """,
                source,
            )
            pages = [
                {
                    "slug": str(_row_value(row, "slug", "")),
                    "title": str(_row_value(row, "title", "") or ""),
                    "updated_at": _isoformat(_row_value(row, "updated_at")),
                }
                for row in page_rows
            ]
            sources = [
                {
                    "source": str(_row_value(row, "source", "")),
                    "count": int(_row_value(row, "count", 0) or 0),
                    "latest": _isoformat(_row_value(row, "latest")),
                }
                for row in source_rows
            ]
            return {
                "status": "ok",
                "source": source,
                "pages": pages,
                "sources": sources,
            }
        finally:
            await conn.close()

    def list(self, limit: int = DEFAULT_SEARCH_LIMIT, source: str | None = None) -> dict:
        """Recent curated pages and per-source document counts."""
        try:
            return asyncio.run(
                self._list_async(
                    limit=_clamp(limit, minimum=1, maximum=MAX_SEARCH_LIMIT),
                    source=source.strip() if source else None,
                )
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # -- status -----------------------------------------------------------

    async def _status_async(self) -> dict[str, Any]:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*)::bigint FROM memory_pages) AS pages,
                    (SELECT COUNT(*)::bigint FROM memory_documents) AS documents,
                    (SELECT COUNT(DISTINCT kind || ':' || ref)::bigint
                       FROM memory_embeddings) AS embedded_rows,
                    (SELECT COUNT(*)::bigint FROM (
                        SELECT slug FROM memory_pages
                        WHERE slug NOT IN (
                            SELECT ref FROM memory_embeddings WHERE kind = 'page')
                        UNION ALL
                        SELECT id::text FROM memory_documents
                        WHERE id::text NOT IN (
                            SELECT ref FROM memory_embeddings WHERE kind = 'doc')
                    ) missing) AS rows_missing_embeddings
                """
            )
            counts = {
                "pages": int(_row_value(row, "pages", 0) or 0),
                "documents": int(_row_value(row, "documents", 0) or 0),
                "embedded_rows": int(_row_value(row, "embedded_rows", 0) or 0),
                "rows_missing_embeddings": int(
                    _row_value(row, "rows_missing_embeddings", 0) or 0
                ),
            }
            embedder_url = _embedder_url()
            embedder = {
                "configured": bool(embedder_url),
                "url": embedder_url or None,
                "reachable": bool(embedder_url) and _embed_query("ping") is not None,
            }
            return {
                "status": "ok",
                "connected": True,
                "database": _postgres_database_name(),
                **counts,
                "embedder": embedder,
            }
        finally:
            await conn.close()

    def status(self) -> dict:
        """Connectivity, row counts, embedder state, and backfill lag."""
        try:
            return asyncio.run(self._status_async())
        except Exception as exc:
            return {"status": "error", "error": str(exc), "connected": False}


def _client() -> MemoryClient:
    return MemoryClient()
