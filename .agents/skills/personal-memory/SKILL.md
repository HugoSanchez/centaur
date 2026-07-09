---
name: personal-memory
description: "Use the `memory` tool as your persistent private memory of THIS user — the people, companies, projects, meetings, decisions, and preferences they care about, plus raw history from their email, chat, and meeting notes. Search it FIRST — before web search, before general knowledge — for ANY question about what you know or remember about the user, their history, their work, their contacts, or their preferences. Write durable new facts back as pages."
---

# Personal Memory

You have a persistent, private memory about this user, stored in their own instance. It holds memories you wrote yourself (curated `pages`) AND raw history passively captured from their connected apps — email, Slack, meeting notes, documents (`documents`). It routinely knows things that never came up in the current conversation.

Retrieval is hybrid: BM25 keyword search always runs; semantic embedding search is added automatically when the embedder is available. You do not manage that — just search.

## Search memory FIRST

For ANY question about what you know or remember about a person, company, project, topic, decision, preference, or commitment, call `memory search` BEFORE session search, web search, or answering from general knowledge.

```bash
memory search "QUERY" --json
```

- If the wording might differ from how the fact was stored, and the first query comes back thin, reword ONCE and retry — try synonyms, a related name, or the user's other language (e.g. Spanish ⇄ English). The embedding lane is cross-lingual, but a second phrasing still helps.
- NEVER say you have nothing in memory about something unless `memory search` actually came back empty for it.
- Narrow to a raw source when useful: `memory search "QUERY" --source gmail --json` (sources include gmail, slack, gdrive, granola, calendar).

Read a full entry before relying on it. `REF` is a page slug (exact or fuzzy) for curated pages, or `doc:<id>` for a raw document returned by search:

```bash
memory page "profile/jane-doe" --json
memory page "doc:1843" --json
```

## Write durable facts back

When the user asks you to remember something, or you learn a durable fact, preference, decision, or commitment worth keeping, save it as a page.

Search first, then UPDATE the existing page rather than creating a near-duplicate. Reuse a stable, descriptive slug (e.g. `profile/<person>`, `project/<name>`, `prefs/<topic>`).

```bash
# 1. Check whether a page already exists.
memory search "Jane Doe" --json
memory page "profile/jane-doe" --json   # if a plausible slug turned up

# 2. Write or overwrite. Content via --content, --file, or stdin.
memory write "profile/jane-doe" --title "Jane Doe" --content "VP Eng at Acme. Prefers async updates. Met 2026-06 re: pilot."

# Longer content reads cleanly from stdin:
cat notes.md | memory write "project/pilot" --title "Acme pilot"
```

A write reports whether the page was `created` or `updated`. Confirm briefly to the user only when they explicitly asked you to remember something ("Saved.").

## Answering rules

- When memory informs an answer, weave it in naturally and cite which page or `doc:<id>` it came from where it matters.
- If a search returns nothing relevant after a reworded retry, proceed normally without dwelling on it.
- Do not dump long private documents or message history into the reply. Summarize narrowly; quote only short snippets when genuinely useful.
- Curated pages are authoritative over raw documents when they conflict (the tool already ranks pages above documents).

## Diagnostics

```bash
memory status --json          # connectivity, row counts, embedder state, backfill lag
memory list --json            # recent pages + document counts per source
```
