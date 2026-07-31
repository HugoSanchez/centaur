# memory — the user's personal memory

Hybrid BM25 + embedding search over the user's history (Slack, Google Docs,
meeting notes, past chats) plus agent-written pages. Search this BEFORE web
search or other tools for questions about the user's history, work, people,
projects, decisions, or preferences.

`memory` is a first-class tool CLI: it is installed on PATH by the standard
tool-shim mechanism, like `slack` or `websearch`. Just run it.

Connectivity comes from injected env: `CENTAUR_POSTGRES_DSN` (never hand-roll a
DSN; the CLI picks the right database) and `MEMORY_EMBEDDER_URL` (optional —
without it search silently degrades to BM25-only).

## Commands

- `search QUERY [--limit N] [--source gmail|slack|gdrive|granola|chat] [--json]` —
  if a search misses, reword once (synonyms, or the other language) and retry.
- `page <slug or doc:ID>` — read full content.
- `write SLUG --title T --content "..."` — save a durable fact as a page.
  Search first; UPDATE an existing page rather than creating a near-duplicate.
- `identity show [user|agent]` — the identity pages rendered into the "Who you
  are working for" block of the system prompt.
- `identity update user --content "..."` — replace an identity page (also
  `--file` or stdin). THE way to record durable facts about the user
  themselves: read with `identity show`, edit, write the whole card back.
  Takes effect at the next session start.
- `list`, `status` — inventory and diagnostics.

When memory informs an answer, attribute it in HUMAN terms — the entry's title,
source, and date ("your All-Hands notes from July 8", "a Slack thread in
#eng-cloud on July 9"), including its link when the entry carries one. NEVER
show raw internal refs like `doc:1028` to the user — those ids are only for
your own follow-up `memory page doc:ID` reads.
