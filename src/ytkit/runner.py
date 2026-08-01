"""Headless download runner with a Rich progress display."""

from __future__ import annotations

import queue
import time

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from .config import Config
from .engine import TERMINAL, Engine, Event, Job, State

STATE_MARKUP = {
    State.QUEUED: "[dim]queued[/dim]",
    State.PROBING: "[cyan]probing[/cyan]",
    State.RUNNING: "[cyan]downloading[/cyan]",
    State.POST: "[yellow]processing[/yellow]",
    State.DONE: "[green]done[/green]",
    State.FAILED: "[red]failed[/red]",
    State.CANCELLED: "[bright_black]cancelled[/bright_black]",
}


def run_downloads(
    urls: list[str],
    cfg: Config,
    console: Console,
    *,
    show_progress: bool = True,
) -> int:
    """Download every URL. Returns a process exit code."""
    events: queue.Queue[Event] = queue.Queue()
    engine = Engine(events.put, workers=max(1, cfg.parallel_jobs))

    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold]{task.fields[label]}", table_column=None),
        BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
        TextColumn("{task.percentage:>5.1f}%"),
        TextColumn("[dim]{task.fields[speed]}"),
        TextColumn("[dim]{task.fields[status]}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    tasks: dict[int, TaskID] = {}
    jobs = engine.submit_urls(urls, cfg)

    def sync(job: Job) -> None:
        label = (job.title or job.url)[:48]
        status = job.detail if job.state in (State.RUNNING, State.POST) else ""
        if job.id not in tasks:
            tasks[job.id] = progress.add_task(
                "", total=100, label=label, speed="", status=status
            )
        progress.update(
            tasks[job.id],
            completed=job.percent,
            label=label,
            speed=job.speed,
            status=status or STATE_MARKUP[job.state],
        )

    def pump() -> None:
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                return
            job = event.job
            if event.kind == "log" and event.level in ("warning", "error"):
                progress.console.print(f"[dim]job {job.id}[/dim] {escape(event.message)}")
            elif event.kind == "state" and job.state is State.FAILED:
                progress.console.print(f"[red]✗[/red] {escape(job.url)}: {escape(job.error)}")
            sync(job)

    try:
        if show_progress:
            with Live(progress, console=console, refresh_per_second=12):
                while any(j.state not in TERMINAL for j in jobs):
                    pump()
                    time.sleep(0.08)
                pump()
        else:
            while any(j.state not in TERMINAL for j in jobs):
                pump()
                time.sleep(0.2)
            pump()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted — cancelling…[/yellow]")
        engine.shutdown(wait=True)
        return 130
    finally:
        engine.shutdown()

    _report(jobs, console)
    return 0 if all(j.state is State.DONE for j in jobs) else 1


def _report(jobs: list[Job], console: Console) -> None:
    table = Table(title="Summary", title_style="bold", show_lines=False, expand=False)
    table.add_column("Job", style="dim", width=4)
    table.add_column("Result")
    table.add_column("Title / URL", overflow="fold")
    table.add_column("Files", justify="right")
    table.add_column("Time", justify="right")

    for job in jobs:
        table.add_row(
            str(job.id),
            STATE_MARKUP[job.state],
            escape(job.label[:70]),
            str(len(job.files)),
            f"{job.elapsed:.0f}s",
        )
    console.print()
    console.print(table)

    written = [f for job in jobs for f in job.files]
    if written:
        console.print(f"\n[bold]{len(written)}[/bold] file(s) written:")
        for path in written[:40]:
            console.print(f"  [green]·[/green] {escape(str(path))}")
        if len(written) > 40:
            console.print(f"  [dim]…and {len(written) - 40} more[/dim]")

    failures = [j for j in jobs if j.state is State.FAILED]
    if failures:
        console.print()
        for job in failures:
            console.print(f"[red]✗[/red] {escape(job.url)}\n  {escape(job.error)}")
