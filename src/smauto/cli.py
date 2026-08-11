"""Command line entry point.

Deliberately deployment-agnostic: GitHub Actions, a systemd timer on a VPS and
a laptop all run exactly these commands.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from smauto import __version__
from smauto.config import get_settings
from smauto.logging import configure_logging, get_logger

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Turn posts you liked into Instagram-ready Reels and feed images.",
)
console = Console()
log = get_logger(__name__)


@app.callback()
def _main(
    log_level: Annotated[str, typer.Option(help="DEBUG, INFO, WARNING, ERROR")] = "",
) -> None:
    settings = get_settings()
    configure_logging(log_level or settings.log_level)


# ----------------------------------------------------------------------
@app.command("init-db")
def init_db() -> None:
    """Create the database schema."""
    from smauto.db.session import create_all

    create_all()
    console.print(f"[green]✓[/] schema ready at {get_settings().database_url}")


@app.command()
def ingest(
    limit: Annotated[int, typer.Option(help="Max updates to drain in one run")] = 50,
) -> None:
    """Pull new links and files from Telegram into the queue."""
    from smauto.db.session import session_scope
    from smauto.telegram.intake import ingest as run_ingest

    with session_scope() as session:
        summary = run_ingest(session, limit=limit)

    console.print(
        f"updates={summary.updates} new={summary.new_items} files={summary.files_attached} "
        f"dupes={summary.duplicates} commands={summary.commands} buttons={summary.callbacks}"
    )
    for error in summary.errors:
        console.print(f"[yellow]![/] {error}")


@app.command()
def process(
    limit: Annotated[int, typer.Option(help="Max items to advance in one run")] = 10,
    loops: Annotated[int, typer.Option(help="Advance each item this many stages")] = 5,
) -> None:
    """Advance queued items through resolve → screen → render → deliver."""
    from smauto.db.session import session_scope
    from smauto.pipeline import process_once

    totals = {"processed": 0, "advanced": 0, "rejected": 0, "failed": 0, "delivered": 0}
    for _ in range(max(loops, 1)):
        with session_scope() as session:
            summary = process_once(session, limit=limit)
        totals["processed"] += summary.processed
        totals["advanced"] += summary.advanced
        totals["rejected"] += summary.rejected
        totals["failed"] += summary.failed
        totals["delivered"] += summary.delivered
        for error in summary.errors:
            console.print(f"[yellow]![/] {error}")
        if summary.processed == 0:
            break

    console.print(
        f"advanced={totals['advanced']} delivered={totals['delivered']} "
        f"rejected={totals['rejected']} failed={totals['failed']}"
    )


@app.command()
def publish(
    limit: Annotated[int, typer.Option(help="Max posts to publish in one run")] = 5,
) -> None:
    """Schedule approved items and publish whatever is due."""
    from smauto.db.session import session_scope
    from smauto.publish.runner import run

    with session_scope() as session:
        summary = run(session, limit=limit)

    console.print(
        f"scheduled={summary.scheduled} published={summary.published} "
        f"failed={summary.failed} skipped={summary.skipped}"
    )
    for error in summary.errors:
        console.print(f"[red]✗[/] {error}")


@app.command("refresh-tokens")
def refresh_tokens() -> None:
    """Renew the Instagram long-lived access token (valid 60 days)."""
    from smauto.db.session import session_scope
    from smauto.publish import tokens
    from smauto.telegram.delivery import notify

    settings = get_settings()
    if settings.delivery_mode != "instagram":
        console.print("[dim]DELIVERY_MODE is not 'instagram'; nothing to refresh.[/]")
        return

    with session_scope() as session:
        try:
            status = tokens.refresh(session)
        except tokens.TokenError as exc:
            console.print(f"[red]✗[/] {exc}")
            notify(None, f"🔑 Instagram token yenilenemedi:\n{exc}")
            raise typer.Exit(1) from exc

        console.print(f"[green]✓[/] token yenilendi, {status.days_left} gün kaldı")
        if status.needs_attention:
            notify(None, f"🔑 Instagram token'ında {status.days_left} gün kaldı.")


@app.command("exchange-token")
def exchange_token(
    short_lived: Annotated[str, typer.Argument(help="Token from the OAuth redirect")],
) -> None:
    """One-off: turn a short-lived Instagram token into a 60-day one."""
    from smauto.db.session import session_scope
    from smauto.publish import tokens

    with session_scope() as session:
        status = tokens.exchange_short_lived(session, short_lived)
    console.print(f"[green]✓[/] long-lived token saved, {status.days_left} gün geçerli")


@app.command("fetch-fonts")
def fetch_fonts(
    force: Annotated[bool, typer.Option(help="Re-download even if present")] = False,
) -> None:
    """Download Inter and Noto Color Emoji into the render font directory."""
    from smauto.render import fonts

    downloaded = fonts.fetch_fonts(force=force)
    for path in downloaded:
        console.print(f"[green]✓[/] {path.name}")
    if not downloaded:
        console.print("[dim]fonts already present[/]")


@app.command()
def show(item_id: Annotated[int, typer.Argument()]) -> None:
    """Print everything known about one item."""
    from smauto.db.models import Item
    from smauto.db.session import session_scope

    with session_scope() as session:
        item = session.get(Item, item_id)
        if item is None:
            console.print(f"[red]✗[/] #{item_id} bulunamadı")
            raise typer.Exit(1)

        table = Table(show_header=False, box=None)
        for label, value in (
            ("status", item.status),
            ("platform", item.source_platform),
            ("url", item.source_url),
            ("author", f"@{item.author_handle}" if item.author_handle else "-"),
            ("text", (item.text or "")[:160]),
            ("score", str(item.ai_score)),
            ("flags", ", ".join(item.flags) or "-"),
            ("phash", item.phash or "-"),
            ("scheduled", str(item.scheduled_for or "-")),
            ("published", item.ig_permalink or str(item.published_at or "-")),
            ("attempts", str(item.attempts)),
            ("last error", (item.last_error or "-")[:200]),
        ):
            table.add_row(f"[bold]{label}[/]", value)
        console.print(table)

        for asset in sorted(item.assets, key=lambda a: (a.kind, a.variant)):
            console.print(
                f"  [dim]{asset.kind}/{asset.variant}[/] {asset.width}x{asset.height} "
                f"{asset.local_path}"
            )


@app.command()
def unpublish(
    item_id: Annotated[int, typer.Argument()],
    keep_author: Annotated[bool, typer.Option(help="Do not blocklist the author")] = False,
) -> None:
    """Remove a published post and blocklist its source (takedown requests)."""
    from smauto.db.models import Item
    from smauto.db.session import session_scope
    from smauto.publish.runner import unpublish as do_unpublish

    with session_scope() as session:
        item = session.get(Item, item_id)
        if item is None:
            console.print(f"[red]✗[/] #{item_id} bulunamadı")
            raise typer.Exit(1)
        do_unpublish(session, item, block_author=not keep_author)
    console.print(f"[green]✓[/] #{item_id} kaldırıldı")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


# ----------------------------------------------------------------------
@app.command()
def doctor() -> None:
    """Check every dependency and credential this project needs."""
    from smauto.doctor import run_checks

    results = run_checks()

    table = Table(title="smauto doctor")
    table.add_column("", width=2)
    table.add_column("Kontrol", style="bold")
    table.add_column("Sonuç")

    for check in results:
        icon = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "fail": "[red]✗[/]"}[check.level]
        table.add_row(icon, check.name, check.detail)

    console.print(table)

    failures = [c for c in results if c.level == "fail"]
    if failures:
        console.print(f"\n[red]{len(failures)} kontrol başarısız.[/]")
        raise typer.Exit(1)
    console.print("\n[green]Her şey yolunda.[/]")


def main() -> None:  # pragma: no cover - console script shim
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["app", "main"]
