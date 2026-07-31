"""Personal memory CLI for AI agents."""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from .client import MemoryClient

load_dotenv()

app = typer.Typer(name="memory", help="Search and write the user's personal memory.")
console = Console()


def _print_json(data: dict[str, Any]) -> None:
    console.print(JSON(json.dumps(data, default=str)))


def _require_ok(result: dict[str, Any]) -> None:
    if result.get("status") == "error":
        console.print(f"[red]{result.get('error', 'unknown error')}[/red]")
        raise typer.Exit(1)


@app.command("health")
def health() -> None:
    """Assert memory connectivity with a safe read-only check."""
    from .client import _client

    client = _client()
    try:
        details = client.status()
        if isinstance(details, dict) and details.get("status") == "error":
            raise RuntimeError(str(details.get("error") or "memory health check failed"))
        payload = {"ok": True, "tool": "memory", "error": None, "details": details}
    except Exception as exc:
        payload = {"ok": False, "tool": "memory", "error": str(exc), "details": {}}
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        raise typer.Exit(1) from exc
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


@app.command("search")
def search(
    query: str = typer.Argument(..., help="Search query."),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results (max 50)."),
    source: str | None = typer.Option(None, "--source", help="Filter documents by source."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Search personal memory (BM25, plus embeddings when the embedder is up)."""
    result = MemoryClient().search(query=query, limit=limit, source=source)
    _require_ok(result)
    if json_output:
        _print_json(result)
        return

    results = result.get("results") or []
    if not results:
        console.print(f"[yellow]No memory found for: {query}[/yellow]")
        return

    table = Table(title=f"Memory Search ({len(results)}, {result.get('mode', 'bm25')})")
    table.add_column("Kind", style="magenta", max_width=6)
    table.add_column("Ref", style="dim", max_width=36)
    table.add_column("Title", style="bold", max_width=36)
    table.add_column("Source", style="cyan", max_width=14)
    table.add_column("Score", style="green", max_width=8)
    table.add_column("Snippet", max_width=72)
    for item in results:
        table.add_row(
            str(item.get("kind") or ""),
            str(item.get("ref") or ""),
            str(item.get("title") or ""),
            str(item.get("source") or ""),
            f"{float(item.get('score') or 0.0):.4f}",
            str(item.get("snippet") or ""),
        )
    console.print(table)


@app.command("page")
def page(
    ref: str = typer.Argument(..., help="Page slug (exact or fuzzy) or doc:<id>."),
    max_chars: int = typer.Option(0, "--max-chars", help="Maximum content chars; 0 means full."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Print the full content of a memory page or raw document."""
    result = MemoryClient().page(ref=ref, max_chars=max_chars)
    _require_ok(result)
    if json_output:
        _print_json(result)
        return

    title = result.get("title") or result.get("ref") or "Memory"
    console.print(f"[bold]{title}[/bold]")
    meta = result.get("ref") or ""
    if result.get("source"):
        meta = f"{meta}  ({result['source']})"
    console.print(f"[dim]{meta}[/dim]")
    console.print(result.get("content") or "")
    if result.get("truncated"):
        console.print(
            f"[yellow]Truncated at {result.get('chars')} of "
            f"{result.get('total_chars')} chars.[/yellow]"
        )


@app.command("write")
def write(
    slug: str = typer.Argument(..., help="Page slug (stable id; update instead of duplicating)."),
    title: str | None = typer.Option(None, "--title", "-t", help="Page title."),
    content: str | None = typer.Option(None, "--content", "-c", help="Page content."),
    file: str | None = typer.Option(None, "--file", "-f", help="Read content from a file."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Create or update a curated memory page (content from --content, --file, or stdin)."""
    if content is not None:
        body = content
    elif file is not None:
        try:
            with open(file, encoding="utf-8") as handle:
                body = handle.read()
        except OSError as exc:
            console.print(f"[red]could not read {file}: {exc}[/red]")
            raise typer.Exit(1) from exc
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        console.print("[red]provide content via --content, --file, or stdin[/red]")
        raise typer.Exit(1)

    result = MemoryClient().write(slug=slug, content=body, title=title)
    _require_ok(result)
    if json_output:
        _print_json(result)
        return
    action = result.get("action", "saved")
    console.print(
        f"[green]{action.capitalize()}[/green] page "
        f"[bold]{result.get('slug')}[/bold] "
        f"([dim]{result.get('title')}[/dim])"
    )


identity_app = typer.Typer(
    name="identity",
    help=(
        "The identity pages behind the 'Who you are working for' block of your "
        "system prompt. `update` is THE way to record durable facts about the "
        "user (role, employer, preferences, corrections) — one call, fixed slugs."
    ),
)
app.add_typer(identity_app, name="identity")

IDENTITY_SLUGS = {"user": "identity/user", "agent": "identity/agent"}
IDENTITY_TITLES = {"user": "User identity", "agent": "Agent identity"}


def _identity_slug(which: str) -> str:
    slug = IDENTITY_SLUGS.get(which.strip().lower())
    if slug is None:
        console.print(f"[red]unknown identity page {which!r}; use 'user' or 'agent'[/red]")
        raise typer.Exit(1)
    return slug


@identity_app.command("show")
def identity_show(
    which: str = typer.Argument("all", help="Which page: user, agent, or all."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Print the identity page(s) injected into the system prompt at session start."""
    names = list(IDENTITY_SLUGS) if which.strip().lower() == "all" else [which]
    client = MemoryClient()
    results = {name: client.page(ref=_identity_slug(name)) for name in names}
    if json_output:
        _print_json(results)
        return
    for name, result in results.items():
        slug = IDENTITY_SLUGS[name]
        console.print(f"[bold]{slug}[/bold]")
        if result.get("status") != "ok":
            console.print("[yellow](empty — not written yet)[/yellow]")
        else:
            console.print(result.get("content") or "")
        console.print()


@identity_app.command("update")
def identity_update(
    which: str = typer.Argument(..., help="Which page: user (who they are) or agent (how to operate)."),
    content: str | None = typer.Option(None, "--content", "-c", help="Full replacement content."),
    file: str | None = typer.Option(None, "--file", "-f", help="Read content from a file."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Replace an identity page (content from --content, --file, or stdin).

    The page is a dense card, not an archive: read it first with `identity show`,
    edit the relevant line, and write the WHOLE page back. Keep it under ~2500
    chars; details belong in ordinary pages (profile/<person>, project/<x>).
    Takes effect at the next session start.
    """
    slug = _identity_slug(which)
    if content is not None:
        body = content
    elif file is not None:
        try:
            with open(file, encoding="utf-8") as handle:
                body = handle.read()
        except OSError as exc:
            console.print(f"[red]could not read {file}: {exc}[/red]")
            raise typer.Exit(1) from exc
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        console.print("[red]provide content via --content, --file, or stdin[/red]")
        raise typer.Exit(1)

    result = MemoryClient().write(slug=slug, content=body, title=IDENTITY_TITLES[which.strip().lower()])
    _require_ok(result)
    if json_output:
        _print_json(result)
        return
    console.print(
        f"[green]{result.get('action', 'saved').capitalize()}[/green] "
        f"[bold]{slug}[/bold] — takes effect next session"
    )


@app.command("list")
def list_memory(
    limit: int = typer.Option(10, "--limit", "-n", help="Max recent pages."),
    source: str | None = typer.Option(None, "--source", help="Filter document counts by source."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """List recent curated pages and per-source document counts."""
    result = MemoryClient().list(limit=limit, source=source)
    _require_ok(result)
    if json_output:
        _print_json(result)
        return

    pages = result.get("pages") or []
    sources = result.get("sources") or []

    pages_table = Table(title=f"Recent Pages ({len(pages)})")
    pages_table.add_column("Slug", style="bold", max_width=40)
    pages_table.add_column("Title", max_width=40)
    pages_table.add_column("Updated", style="green", max_width=24)
    for item in pages:
        pages_table.add_row(
            str(item.get("slug") or ""),
            str(item.get("title") or ""),
            str(item.get("updated_at") or ""),
        )
    console.print(pages_table)

    sources_table = Table(title="Documents by Source")
    sources_table.add_column("Source", style="magenta", max_width=24)
    sources_table.add_column("Count", style="cyan", max_width=10)
    sources_table.add_column("Latest", style="green", max_width=24)
    for item in sources:
        sources_table.add_row(
            str(item.get("source") or ""),
            str(item.get("count") or 0),
            str(item.get("latest") or ""),
        )
    console.print(sources_table)


@app.command("status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show connectivity, row counts, and embedder state."""
    result = MemoryClient().status()
    _require_ok(result)
    _print_json(result)


if __name__ == "__main__":
    app()
