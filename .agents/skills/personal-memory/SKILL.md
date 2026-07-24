---
name: personal-memory
description: "Use the `memory` tool as your persistent private memory of THIS user — the people, companies, projects, meetings, decisions, and preferences they care about, plus raw history from their email, chat, and meeting notes. Search it FIRST — before web search, before general knowledge — for ANY question about what you know or remember about the user, their history, their work, their contacts, or their preferences. Write durable new facts back as pages."
---

# Personal Memory

You have a persistent, private memory about this user, stored in their own instance. It holds memories you wrote yourself (curated `pages`) AND raw history passively captured from their connected apps — email, Slack, meeting notes, documents (`documents`). It routinely knows things that never came up in the current conversation.

Retrieval is hybrid: BM25 keyword search always runs; semantic embedding search is added automatically when the embedder is available. You do not manage that — just search.

## Setup (once per session)

The `memory` CLI is invoked through a small wrapper. Install it once — the block is idempotent, safe to re-run, and takes ~1s:

```bash
mkdir -p ~/bin && cat > ~/bin/memory <<'WRAP' && chmod +x ~/bin/memory
#!/usr/bin/env bash
set -euo pipefail
export MEMORY_EMBEDDER_URL="${MEMORY_EMBEDDER_URL:-http://memoryd:8787}"
export no_proxy="${no_proxy:-},memoryd" NO_PROXY="${NO_PROXY:-},memoryd"
TOOL_SRC=/home/agent/github/HugoSanchez/centaur/tools/productivity/memory
WORK="${TMPDIR:-/tmp}/memtool"
mkdir -p "$WORK"; ln -sfn "$TOOL_SRC" "$WORK/memory"
cd "$WORK"
exec env PYTHONPATH="$WORK:/opt/centaur" uv run --no-project \
  --with 'asyncpg>=0.30.0' --with 'python-dotenv>=1.0.0' \
  --with 'rich>=13.0.0' --with 'typer>=0.12.0' \
  python -m memory.cli "$@"
WRAP
~/bin/memory status --json
```

If `status` reports `connected`, memory is ready. Every command below is `~/bin/memory …` (shell functions and PATH edits do not persist between your tool calls; the absolute path always works).

## Search memory FIRST

For ANY question about what you know or remember about a person, company, project, topic, decision, preference, or commitment, call `memory search` BEFORE session search, web search, or answering from general knowledge.

```bash
~/bin/memory search "QUERY" --json
```

- If the wording might differ from how the fact was stored, and the first query comes back thin, reword ONCE and retry — try synonyms, a related name, or the user's other language (e.g. Spanish ⇄ English). The embedding lane is cross-lingual, but a second phrasing still helps.
- NEVER say you have nothing in memory about something unless `memory search` actually came back empty for it.
- Narrow to a raw source when useful: `memory search "QUERY" --source gmail --json` (sources: gmail, slack, gdrive, granola, chat).

Read a full entry before relying on it. `REF` is a page slug (exact or fuzzy) for curated pages, or `doc:<id>` for a raw document returned by search:

```bash
~/bin/memory page "profile/jane-doe" --json
~/bin/memory page "doc:1843" --json
```

## Write durable facts back

When the user asks you to remember something, or you learn a durable fact, preference, decision, or commitment worth keeping, save it as a page.

Search first, then UPDATE the existing page rather than creating a near-duplicate. Reuse a stable, descriptive slug (e.g. `profile/<person>`, `project/<name>`, `prefs/<topic>`).

```bash
# 1. Check whether a page already exists.
~/bin/memory search "Jane Doe" --json
~/bin/memory page "profile/jane-doe" --json   # if a plausible slug turned up

# 2. Write or overwrite. Content via --content, --file, or stdin.
~/bin/memory write "profile/jane-doe" --title "Jane Doe" --content "VP Eng at Acme. Prefers async updates. Met 2026-06 re: pilot."

# Longer content reads cleanly from stdin:
cat notes.md | ~/bin/memory write "project/pilot" --title "Acme pilot"
```

A write reports whether the page was `created` or `updated`. Confirm briefly to the user only when they explicitly asked you to remember something ("Saved.").

## Answering rules

- When memory informs an answer, weave it in naturally and attribute it in HUMAN terms — the entry's title, source, and date (e.g. "your All-Hands meeting notes from July 8", "a Slack thread in #eng-cloud on July 9", "the *Prover Network Services Agreement* Google Doc" — include its link when the entry carries one). NEVER show raw internal refs like `doc:1843` to the user; those ids exist only for your own follow-up `memory page` reads.
- If a search returns nothing relevant after a reworded retry, proceed normally without dwelling on it.
- Do not dump long private documents or message history into the reply. Summarize narrowly; quote only short snippets when genuinely useful.
- Curated pages are authoritative over raw documents when they conflict (the tool already ranks pages above documents).

## Diagnostics

```bash
~/bin/memory status --json          # connectivity, row counts, embedder state, backfill lag
~/bin/memory list --json            # recent pages + document counts per source
```
