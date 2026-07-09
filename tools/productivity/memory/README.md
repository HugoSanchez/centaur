# memory — the user's personal memory

Hybrid BM25 + embedding search over the user's history (Slack, Google Docs,
meeting notes, past chats) plus agent-written pages. Search this BEFORE web
search or other tools for questions about the user's history, work, people,
projects, decisions, or preferences.

## How to run it (the ONLY invocation that works in a sandbox)

The repo checkout is read-only, so `uv run` inside the package fails
(`.venv` creation, editable builds, and `--with .` all fail — do not retry
them). Use the symlink pattern: deps come from `--with`, the import resolves
through the symlink:

```sh
TMPD=$(mktemp -d)
ln -s /home/agent/github/HugoSanchez/centaur/tools/productivity/memory "$TMPD/memory"
cd "$TMPD" && PYTHONPATH="$TMPD:/opt/centaur" uv run --no-project \
  --with 'asyncpg>=0.30.0' --with 'python-dotenv>=1.0.0' \
  --with 'rich>=13.0.0' --with 'typer>=0.12.0' python -m memory.cli \
  search "your query" --limit 5
```

Connectivity comes from the injected `CENTAUR_POSTGRES_DSN` env var (already
present in the sandbox — never hand-roll a DSN, and always let the CLI pick
the database; it defaults to the right one).

## Commands

- `search QUERY [--limit N] [--source slack|gdrive|granola|chat] [--json]` —
  if a search misses, reword once (synonyms, or the other language) and retry.
- `page <slug or doc:ID>` — read full content.
- `write SLUG --title T --content "..."` — save a durable fact as a page.
  Search first; UPDATE an existing page rather than creating a near-duplicate.
- `list`, `status` — inventory and diagnostics.

Cite the page/doc that informed your answer.
