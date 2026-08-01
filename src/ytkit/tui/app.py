"""The ytkit terminal UI."""

from __future__ import annotations

import queue
import subprocess
import sys
from pathlib import Path
from typing import TypeVar

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)
from textual.widgets._data_table import ColumnKey

from ..argv import build_command
from ..config import Config
from ..engine import Engine, Event, Job, State
from ..presets import BY_KEY, PRESETS
from ..storage import delete_profile, load_profile, save_config, save_profile
from .modals import ConfirmScreen, InfoScreen, ProfileScreen
from .settings import SettingsPanel

WidgetT = TypeVar("WidgetT", bound=Widget)

STATE_STYLE = {
    State.QUEUED: ("queued", "dim"),
    State.PROBING: ("probing", "cyan"),
    State.RUNNING: ("downloading", "bold cyan"),
    State.POST: ("processing", "yellow"),
    State.DONE: ("done", "bold green"),
    State.FAILED: ("failed", "bold red"),
    State.CANCELLED: ("cancelled", "bright_black"),
}

# (key, label, width). Explicit keys are required: add_columns() would generate
# its own, and update_cell() cannot address a column by its label.
COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("id", "#", 3),
    ("status", "Status", 11),
    ("title", "Title / URL", 34),  # resized to fill; see _fit_title_column
    ("progress", "Progress", 19),
    ("speed", "Speed", 9),
    ("eta", "ETA", 5),
    ("size", "Size", 9),
)
FIXED_WIDTH = sum(w for key, _, w in COLUMNS if key != "title")
CELL_PADDING = 2 * len(COLUMNS)  # DataTable pads one space either side
MIN_TITLE, MAX_TITLE = 18, 70


def bar(percent: float, width: int = 12) -> Text:
    filled = int(round(percent / 100 * width))
    text = Text()
    text.append("█" * filled, style="cyan")
    text.append("░" * (width - filled), style="bright_black")
    text.append(f" {percent:5.1f}%")
    return text


class QueuePanel(Vertical):
    """URL entry, preset picker and the live job table."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="entry-row"):
            yield Input(
                placeholder="Paste a video, playlist or channel URL and press Enter…",
                id="url-input",
            )
            yield Select(
                [(p.label, p.key) for p in PRESETS],
                prompt="Preset",
                id="preset-select",
                allow_blank=True,
            )
            yield Button("Add", variant="primary", id="add-btn")
        yield Static("", id="preset-hint", classes="panel-hint")
        yield DataTable(id="job-table", cursor_type="row", zebra_stripes=True)
        yield Label("Log", classes="pane-title")
        yield RichLog(id="log", markup=True, wrap=True, max_lines=2000, highlight=False)


class YtkitApp(App[None]):
    """Queue-driven downloader with a full settings surface."""

    CSS_PATH = "styles.tcss"
    TITLE = "ytkit"
    SUB_TITLE = "YouTube media & metadata"

    BINDINGS = [
        ("a", "focus_url", "Add URL"),
        ("i", "inspect", "Inspect"),
        ("x", "cancel_job", "Cancel"),
        ("r", "retry_job", "Retry"),
        ("o", "open_output", "Open folder"),
        ("c", "copy_command", "Show command"),
        ("ctrl+s", "save_config", "Save config"),
        ("ctrl+p", "profiles", "Profiles"),
        ("ctrl+l", "clear_log", "Clear log"),
        ("ctrl+q", "quit", "Quit"),
    ]

    active_jobs: reactive[int] = reactive(0)

    def __init__(self, cfg: Config, urls: list[str] | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.pending_urls = list(urls or [])
        self.events: queue.Queue[Event] = queue.Queue()
        self.engine = Engine(self.events.put, workers=max(1, cfg.parallel_jobs))
        self.rows: set[int] = set()
        self.title_width = MIN_TITLE

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Queue", id="tab-queue"):
                yield QueuePanel()
            with TabPane("Settings", id="tab-settings"):
                yield SettingsPanel(self.cfg, id="settings-panel")
            with TabPane("Command", id="tab-command"), VerticalScroll():
                yield Static("", id="command-view", classes="command-view")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#job-table", DataTable)
        for key, label, width in COLUMNS:
            table.add_column(label, key=key, width=width)
        self.set_interval(0.1, self._drain_events)
        self.set_interval(0.5, self._refresh_status)
        self.log_line(f"Output directory: [b]{escape(self.cfg.output_dir)}[/b]")
        self.refresh_command()
        self._fit_title_column()
        for url in self.pending_urls:
            self.add_url(url)
        self.query_one("#url-input", Input).focus()

    def on_resize(self, event) -> None:
        self._fit_title_column()

    def _widget(self, selector: str, kind: type[WidgetT]) -> WidgetT | None:
        """Look up a widget that may not exist yet.

        Interval callbacks keep firing while the app is still mounting and
        while it is tearing down, when the tree is incomplete.
        """
        found = self.query(selector)
        widget = found.first() if found else None
        return widget if isinstance(widget, kind) else None

    def _fit_title_column(self) -> None:
        """Give the title column whatever horizontal space is left over.

        DataTable has no flexible columns, so the width is recomputed on every
        resize; without it the table either truncates titles brutally on wide
        terminals or scrolls sideways on narrow ones.
        """
        table = self._widget("#job-table", DataTable)
        if table is None:
            return
        spare = table.size.width - FIXED_WIDTH - CELL_PADDING
        width = max(MIN_TITLE, min(MAX_TITLE, spare))
        if width == self.title_width:
            return
        self.title_width = width
        column = table.columns.get(ColumnKey("title"))
        if column is not None:
            column.width = width
            column.auto_width = False
        for job in self.engine.jobs.values():
            if job.id in self.rows:
                self._render_row(job)
        table.refresh()

    # -- helpers --------------------------------------------------------------

    def log_line(self, message: str, level: str = "info") -> None:
        log = self._widget("#log", RichLog)
        if log is None:
            return
        prefix = {"info": "", "warning": "[yellow]! [/yellow]", "error": "[red]✗ [/red]"}[level]
        log.write(f"{prefix}{message}")

    def refresh_command(self) -> None:
        view = self._widget("#command-view", Static)
        if view is None:
            return
        preview = build_command(self.cfg, ["<URL>"])
        changed = self.cfg.diff_from_defaults()
        lines = [
            "[b]Equivalent yt-dlp command[/b]",
            "",
            f"[cyan]{escape(preview)}[/cyan]",
            "",
            f"[b]Non-default settings[/b] ({len(changed)})",
            "",
        ]
        lines += (
            [f"  [green]{k}[/green] = {escape(str(v))}" for k, v in sorted(changed.items())]
            if changed
            else ["  [dim]all settings are at their defaults[/dim]"]
        )
        view.update("\n".join(lines))

    def selected_job(self) -> Job | None:
        table = self._widget("#job-table", DataTable)
        if table is None or table.row_count == 0 or table.cursor_row is None:
            return None
        try:
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key
        except Exception:  # noqa: BLE001 - cursor may be stale mid-update
            return None
        return self.engine.jobs.get(int(str(key.value)))

    def add_url(self, url: str) -> None:
        url = url.strip()
        if not url:
            return
        job = self.engine.submit(Job(url=url, config=self.cfg.copy()))
        self.log_line(f"queued [b]{escape(url)}[/b] (job {job.id})")

    # -- event pump -----------------------------------------------------------

    def _drain_events(self) -> None:
        """Move engine events onto the UI thread in batches."""
        touched: dict[int, Job] = {}
        for _ in range(400):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            touched[event.job.id] = event.job
            if event.kind == "log" and event.message:
                self.log_line(f"[dim]job {event.job.id}[/dim] {escape(event.message)}", event.level)
            elif event.kind == "state":
                self._on_state(event)
            elif event.kind == "file":
                self.log_line(f"[dim]job {event.job.id}[/dim] → [green]{escape(event.message)}[/green]")
        for job in touched.values():
            self._render_row(job)

    def _on_state(self, event: Event) -> None:
        job = event.job
        if job.state is State.DONE:
            count = f" ({job.items_done} items)" if job.items_done > 1 else ""
            self.log_line(f"[green]✓[/green] job {job.id} finished{count} in {job.elapsed:.0f}s")
            self.notify(f"Finished: {job.label[:60]}", timeout=4)
        elif job.state is State.FAILED:
            self.log_line(f"job {job.id} failed: {escape(event.message)}", "error")
            self.notify(f"Failed: {job.label[:60]}", severity="error", timeout=6)
        elif job.state is State.CANCELLED:
            self.log_line(f"job {job.id} cancelled", "warning")

    def _refresh_status(self) -> None:
        # Cheap no-op unless the width actually changed; on_mount runs before
        # the table has been laid out, so this is what settles the first fit.
        self._fit_title_column()
        self.active_jobs = self.engine.active
        done = sum(1 for j in self.engine.jobs.values() if j.state is State.DONE)
        failed = sum(1 for j in self.engine.jobs.values() if j.state is State.FAILED)
        self.sub_title = f"{self.active_jobs} active · {done} done · {failed} failed"

    def _render_row(self, job: Job) -> None:
        table = self._widget("#job-table", DataTable)
        if table is None:
            return
        label, style = STATE_STYLE[job.state]
        detail = f" [{job.detail}]" if job.detail and job.state in (State.RUNNING, State.POST) else ""
        cells = (
            str(job.id),
            Text(label, style=style),
            Text((job.label + detail)[: self.title_width], no_wrap=True),
            bar(job.percent),
            job.speed,
            job.eta,
            job.size,
        )
        if job.id in self.rows:
            for (key, _, _), value in zip(COLUMNS, cells, strict=True):
                table.update_cell(str(job.id), key, value, update_width=False)
        else:
            table.add_row(*cells, key=str(job.id))
            self.rows.add(job.id)

    # -- queue interactions ---------------------------------------------------

    @on(Input.Submitted, "#url-input")
    @on(Button.Pressed, "#add-btn")
    def _submit_url(self, event) -> None:
        field = self.query_one("#url-input", Input)
        for url in field.value.split():
            self.add_url(url)
        field.value = ""

    @on(Select.Changed, "#preset-select")
    def _preset_changed(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        preset = BY_KEY[str(event.value)]
        self.cfg = preset.apply(self.cfg)
        panel = self.query_one("#settings-panel", SettingsPanel)
        panel.cfg = self.cfg
        panel.refresh_values()
        self.query_one("#preset-hint", Static).update(f"[b]{preset.label}:[/b] {preset.description}")
        self.log_line(f"applied preset [b]{preset.label}[/b]")
        self.refresh_command()

    # -- settings interactions ------------------------------------------------

    @on(Switch.Changed)
    @on(Select.Changed)
    @on(Input.Changed)
    def _setting_changed(self, event) -> None:
        widget_id = event.control.id if event.control else None
        if not widget_id or not widget_id.startswith("f-"):
            return
        value = event.value
        if value is Select.BLANK:
            return
        panel = self.query_one("#settings-panel", SettingsPanel)
        if panel.apply_change(widget_id, value):
            self.refresh_command()

    # -- actions --------------------------------------------------------------

    def action_focus_url(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-queue"
        self.query_one("#url-input", Input).focus()

    def action_cancel_job(self) -> None:
        job = self.selected_job()
        if job is None:
            self.notify("Select a job first.", severity="warning")
            return
        self.engine.cancel(job.id)
        self.log_line(f"cancelling job {job.id}…", "warning")

    def action_retry_job(self) -> None:
        job = self.selected_job()
        if job is None or job.state not in (State.FAILED, State.CANCELLED):
            self.notify("Select a failed or cancelled job to retry.", severity="warning")
            return
        self.add_url(job.url)

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_save_config(self) -> None:
        path = save_config(self.cfg)
        self.notify(f"Saved {path}")
        self.log_line(f"config saved to [b]{escape(str(path))}[/b]")

    def action_open_output(self) -> None:
        path = Path(self.cfg.output_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
        try:
            subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.notify(f"Opened {path}")
        except OSError as exc:
            self.notify(f"Could not open folder: {exc}", severity="error")

    def action_copy_command(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "tab-command"
        self.refresh_command()

    def action_inspect(self) -> None:
        job = self.selected_job()
        url = job.url if job else self.query_one("#url-input", Input).value.strip()
        if not url:
            self.notify("Paste a URL or select a job to inspect.", severity="warning")
            return
        self.log_line(f"probing [b]{escape(url)}[/b]…")
        self._probe(url)

    @work(thread=True, exclusive=True, group="probe")
    def _probe(self, url: str) -> None:
        from ..engine import probe

        try:
            info = probe(url, self.cfg)
        except Exception as exc:  # noqa: BLE001 - report any extractor failure
            self.call_from_thread(self.notify, f"Probe failed: {exc}", severity="error")
            return
        self.call_from_thread(self.push_screen, InfoScreen(info))

    def action_profiles(self) -> None:
        self.push_screen(ProfileScreen(), self._handle_profile)

    def _handle_profile(self, result: tuple[str, str] | None) -> None:
        if not result:
            return
        action, name = result
        try:
            if action == "save":
                save_profile(name, self.cfg)
                self.notify(f"Saved profile '{name}'")
            elif action == "load":
                self.cfg = load_profile(name)
                panel = self.query_one("#settings-panel", SettingsPanel)
                panel.cfg = self.cfg
                panel.refresh_values()
                self.refresh_command()
                self.notify(f"Loaded profile '{name}'")
            elif action == "delete":
                self.notify(
                    f"Deleted profile '{name}'" if delete_profile(name) else f"No profile '{name}'"
                )
        except (OSError, FileNotFoundError) as exc:
            self.notify(str(exc), severity="error")

    # -- shutdown -------------------------------------------------------------

    async def action_quit(self) -> None:
        if self.engine.active:
            confirm = await self.push_screen_wait(
                ConfirmScreen(f"{self.engine.active} download(s) still running. Quit anyway?")
            )
            if not confirm:
                return
        self.engine.shutdown()
        self.exit()


def run(cfg: Config, urls: list[str] | None = None) -> None:
    YtkitApp(cfg, urls).run()
