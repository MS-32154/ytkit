"""The ytkit terminal UI."""

from __future__ import annotations

import queue
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)
from textual.widgets._data_table import ColumnKey
from textual.widgets.select import NoSelection

from .. import storage
from ..argv import build_command
from ..config import Config
from ..engine import TERMINAL, Engine, Event, Job, State
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

Columns = tuple[tuple[str, str, int], ...]

# (key, label, width). Explicit keys are required: add_columns() would generate
# its own, and update_cell() cannot address a column by its label.
COLUMNS: Columns = (
    ("id", "#", 3),
    ("status", "Status", 11),
    ("title", "Title / URL", 34),  # resized to fill; see _fit_title_column
    ("progress", "Progress", 19),
    ("speed", "Speed", 9),
    ("eta", "ETA", 5),
    ("size", "Size", 9),
)
# For narrow terminals: a bare percentage instead of the bar, and no size, so
# the title keeps a readable width and nothing runs off the edge.
COMPACT_COLUMNS: Columns = (
    ("id", "#", 3),
    ("status", "Status", 11),
    ("title", "Title / URL", 30),
    ("progress", "Done", 5),
    ("speed", "Speed", 9),
    ("eta", "ETA", 5),
)
MIN_TITLE, MAX_TITLE = 18, 70
# Below this terminal width every screen goes narrow: the compact table, the
# stacked settings rows, the full-screen inspector.
NARROW_BELOW = 110


def fixed_width(columns: Columns) -> int:
    """Everything but the title, including DataTable's one-space cell padding."""
    return sum(w for key, _, w in columns if key != "title") + 2 * len(columns)


def width_of(columns: Columns, key: str) -> int:
    return next(w for k, _, w in columns if k == key)


def is_blank(value: object) -> bool:
    """True when a Select is showing its empty state.

    Not `value is Select.BLANK`: in Textual 8 that attribute is the bool
    False, while the actual sentinel is a NoSelection instance (Select.NULL),
    so the identity check silently never matched.
    """
    return isinstance(value, NoSelection)


PROGRESS_WIDTH = width_of(COLUMNS, "progress")


def short_reason(error: str) -> str:
    """The gist of a yt-dlp error, for a table cell.

    'ERROR: [youtube] abc123: Private video. Sign in if...' -> 'Private video'
    """
    reason = re.sub(r"^ERROR:\s*", "", error.strip())
    reason = re.sub(r"^\[[^\]]+\]\s*(?:[\w-]+:\s+)?", "", reason)
    return reason.split(". ")[0].rstrip(".") or "failed"


def progress_cell(job: Job, width: int = PROGRESS_WIDTH) -> Text:
    """A bar while there is progress to show; for a failure, why it failed.

    Too narrow for a bar, it is the percentage alone, and a failure's reason
    moves to the title cell (see title_cell), where there is room to read it.
    """
    if job.state is State.FAILED and width < PROGRESS_WIDTH:
        return Text("")
    if job.state is State.FAILED:
        text = Text(short_reason(job.error), style="red", no_wrap=True)
        text.truncate(width, overflow="ellipsis")
        return text
    if job.state in (State.QUEUED, State.PROBING) or (job.state is State.CANCELLED and not job.percent):
        return Text("")
    if width < PROGRESS_WIDTH:
        # Floored: 99.6% rounded would read as finished while it is not.
        return Text(f"{int(job.percent)}%".rjust(width), style="cyan" if job.percent < 100 else "")
    return bar(job.percent)


def title_cell(job: Job, width: int, *, with_reason: bool) -> Text:
    detail = f" [{job.detail}]" if job.detail and job.state in (State.RUNNING, State.POST) else ""
    text = Text(no_wrap=True)
    if with_reason and job.state is State.FAILED:
        # Reason first: a long URL would otherwise push it out of the cell.
        text.append(f"{short_reason(job.error)} · ", style="red")
    text.append(job.label + detail)
    text.truncate(width, overflow="ellipsis")
    return text


def is_setting(widget: Widget | None) -> bool:
    return widget is not None and (widget.id or "").startswith("f-")


def bar(percent: float, width: int = 12) -> Text:
    filled = int(round(percent / 100 * width))
    text = Text()
    text.append("█" * filled, style="cyan")
    text.append("░" * (width - filled), style="bright_black")
    text.append(f" {percent:5.1f}%")
    return text


SETTINGS_LABEL = "[dim]2[/] Settings"


class QueuePanel(Vertical):
    """URL entry, preset picker and the live job table."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="entry-row"):
            yield Input(
                placeholder="Paste a video, playlist or channel URL and press Enter · esc for shortcuts",
                id="url-input",
            )
            yield Select(
                [(p.label, p.key) for p in PRESETS],
                prompt="Preset",
                id="preset-select",
                allow_blank=True,
            )
            yield Button("Add", variant="primary", id="add-btn")
        hint = Static("", id="preset-hint", classes="panel-hint")
        hint.display = False  # until a preset has something to say
        yield hint
        yield DataTable(id="job-table", cursor_type="row", zebra_stripes=True)
        log = RichLog(id="log", markup=True, wrap=True, max_lines=2000, highlight=False)
        log.border_title = "Log"
        yield log


class YtkitApp(App[None]):
    """Queue-driven downloader with a full settings surface."""

    CSS_PATH = "styles.tcss"
    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (NARROW_BELOW, "-wide")]
    TITLE = "ytkit"
    SUB_TITLE = "YouTube media & metadata"

    # ctrl+p belongs to Textual's command palette, which takes it first.
    BINDINGS = [
        ("a", "focus_url", "Add"),
        ("i", "inspect", "Inspect"),
        # One key, two jobs: check_action enables whichever fits the selected row.
        ("x", "cancel_job", "Cancel"),
        ("x", "remove_job", "Remove"),
        Binding("X", "clear_finished", "Clear finished", show=False),
        ("r", "retry_job", "Retry"),
        ("o", "open_output", "Folder"),
        ("c", "copy_command", "Copy"),
        ("p", "profiles", "Profiles"),
        # The tab labels carry the numbers, so the footer doesn't have to.
        Binding("1", "show_tab('tab-queue')", "Queue", show=False),
        Binding("2", "show_tab('tab-settings')", "Settings", show=False),
        Binding("3", "show_tab('tab-command')", "Command", show=False),
        Binding("slash", "focus_filter", "Filter settings", show=False),
        ("escape", "leave_input", "Shortcuts"),
        ("ctrl+s", "save_config", "Save"),
        Binding("ctrl+l", "clear_log", "Clear log", show=False),
        ("ctrl+q", "quit", "Quit"),
    ]

    active_jobs: reactive[int] = reactive(0)

    def __init__(
        self, cfg: Config, urls: list[str] | None = None, *, saved: Config | None = None
    ) -> None:
        super().__init__()
        self.cfg = cfg
        # What ctrl+s would overwrite, for the unsaved mark. Not `cfg` itself:
        # a --preset or --profile at launch is a change the file doesn't have.
        self.saved_cfg = (saved or cfg).copy()
        self._warned: dict[str, object] = {}  # last bad value warned about, per field
        self._remember_theme = False
        self.pending_urls = list(urls or [])
        self.events: queue.Queue[Event] = queue.Queue()
        self.engine = Engine(self.events.put, workers=max(1, cfg.parallel_jobs))
        self.rows: set[int] = set()
        self.title_width = MIN_TITLE
        self.columns: Columns = ()

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("[dim]1[/] Queue", id="tab-queue"):
                yield QueuePanel()
            with TabPane(SETTINGS_LABEL, id="tab-settings"):
                yield SettingsPanel(self.cfg, id="settings-panel")
            with TabPane("[dim]3[/] Command", id="tab-command"), VerticalScroll(id="command-scroll"):
                yield Static("", id="command-view", classes="command-view")
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self) -> None:
        theme = storage.load_ui_state().get("theme")
        if isinstance(theme, str) and theme in self.available_themes:
            self.theme = theme
        self._remember_theme = True  # from here on a change is the user's
        self.set_interval(0.1, self._drain_events)
        self.set_interval(0.5, self._refresh_status)
        self.log_line(f"Output directory: [b]{escape(self.cfg.output_dir)}[/b]")
        self.refresh_command()
        self._refresh_unsaved()
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
        # The same breakpoint as the settings and modals, so the screens agree.
        compact = 0 < self.size.width < NARROW_BELOW
        columns = COMPACT_COLUMNS if compact else COLUMNS
        width = max(MIN_TITLE, min(MAX_TITLE, table.size.width - fixed_width(columns)))
        if columns != self.columns:
            self.title_width = width  # before the rebuild, which renders every row
            self._set_columns(table, columns)
            return
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

    def watch_theme(self, theme: str) -> None:
        if self._remember_theme:
            try:
                storage.save_ui_state({**storage.load_ui_state(), "theme": theme})
            except OSError as exc:
                self.notify(f"Could not remember the theme: {exc}", severity="warning")

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """ytkit's own actions in the command palette (ctrl+p), after Textual's."""
        yield from super().get_system_commands(screen)
        for title, help_text, action in (
            ("Add a URL", "Focus the URL box", self.action_focus_url),
            ("Copy the yt-dlp command", "For the current settings", self.action_copy_command),
            ("Open the output folder", "In the system file manager", self.action_open_output),
            ("Profiles", "Save, load or delete a named profile", self.action_profiles),
            ("Save settings", "Write them to the config file", self.action_save_config),
            ("Filter settings", "Search every setting by name or help", self.action_focus_filter),
            ("Clear finished jobs", "Remove done, failed and cancelled rows", self.action_clear_finished),
            ("Clear the log", "Empty the log panel", self.action_clear_log),
        ):
            yield SystemCommand(title, help_text, action)

    def _set_columns(self, table: DataTable, columns: Columns) -> None:
        """Rebuild the table for another layout, keeping the selected job."""
        selected = self.selected_job()
        table.clear(columns=True)
        self.rows.clear()
        self.columns = columns
        for key, label, width in columns:
            table.add_column(label, key=key, width=width)
        for job in self.engine.jobs.values():
            self._render_row(job)
        if selected is not None and selected.id in self.rows:
            table.move_cursor(row=table.get_row_index(str(selected.id)))

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
        preview = build_command(self.cfg, ["<URL>"], multiline=True)
        changed = self.cfg.diff_from_defaults()
        lines = [
            "[b]Equivalent yt-dlp command[/b]  [dim]c copies it[/dim]",
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
        # The same URL under other settings (video now, audio after) is a new job.
        twin = next(
            (
                j
                for j in self.engine.jobs.values()
                if j.url == url and j.config == self.cfg and j.state not in TERMINAL
            ),
            None,
        )
        if twin is not None:
            self.notify(f"Already in the queue as job {twin.id}", severity="warning")
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
        self.refresh_bindings()  # x and r follow the selected job's state
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
        # A removed job can still have events in flight; they must not revive its row.
        if table is None or job.id not in self.engine.jobs or not self.columns:
            return
        label, style = STATE_STYLE[job.state]
        progress_width = width_of(self.columns, "progress")
        cells = {
            "id": str(job.id),
            "status": Text(label, style=style),
            "title": title_cell(job, self.title_width, with_reason=progress_width < PROGRESS_WIDTH),
            "progress": progress_cell(job, progress_width),
            "speed": job.speed,
            "eta": job.eta,
            "size": job.size,
        }
        if job.id in self.rows:
            for key, _, _ in self.columns:
                table.update_cell(str(job.id), key, cells[key], update_width=False)
        else:
            table.add_row(*(cells[key] for key, _, _ in self.columns), key=str(job.id))
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
        # Clearing the picker back to "--" is a no-op: it must not revert
        # settings the user has since tuned by hand.
        preset = BY_KEY.get(str(event.value)) if not is_blank(event.value) else None
        if preset is None:
            return
        self.cfg = preset.apply(self.cfg)
        panel = self.query_one("#settings-panel", SettingsPanel)
        panel.cfg = self.cfg
        panel.refresh_values()
        hint = self.query_one("#preset-hint", Static)
        hint.update(f"[b]{preset.label}:[/b] {preset.description}")
        hint.display = True
        self.log_line(f"applied preset [b]{preset.label}[/b]")
        self.refresh_command()
        self._refresh_unsaved()

    # -- settings interactions ------------------------------------------------

    @on(Switch.Changed)
    @on(Select.Changed)
    def _choice_changed(self, event: Switch.Changed | Select.Changed) -> None:
        if is_setting(event.control) and not is_blank(event.value):
            self._apply_setting(event.control, event.value)

    @on(Input.Changed)
    def _setting_edited(self, event: Input.Changed) -> None:
        # Checked as you type, applied only on commit: a half-typed path or
        # number must never become the setting a download picks up.
        panel = self._widget("#settings-panel", SettingsPanel)
        if is_setting(event.input) and panel is not None:
            event.input.set_class(not panel.accepts(event.input.id, event.value), "-invalid")

    @on(Input.Submitted)
    @on(Input.Blurred)
    def _setting_committed(self, event: Input.Submitted | Input.Blurred) -> None:
        if is_setting(event.input):
            self._apply_setting(event.input, event.value)

    @on(DataTable.RowHighlighted, "#job-table")
    @on(TabbedContent.TabActivated)
    def _context_moved(self) -> None:
        self.refresh_bindings()  # x and r depend on the tab and the selected job

    def _commit_focused(self) -> None:
        """Apply a text field still being edited, before its value is used."""
        if isinstance(self.focused, Input) and is_setting(self.focused):
            self._apply_setting(self.focused, self.focused.value)

    def _apply_setting(self, widget: Widget, value: object) -> None:
        panel = self._widget("#settings-panel", SettingsPanel)
        if panel is None:
            return
        name = panel.apply_change(widget.id, value)
        if name is None:
            if isinstance(widget, Input):
                widget.add_class("-invalid")
                # Enter, then leaving the field, then ctrl+s all commit: say it once.
                if self._warned.get(widget.id or "") != value:
                    self._warned[widget.id or ""] = value
                    self.notify(
                        f"{panel.label_for(widget.id)}: {value!r} isn't valid; the setting is unchanged.",
                        severity="warning",
                    )
            return
        self._warned.pop(widget.id or "", None)
        widget.remove_class("-invalid")
        self.refresh_command()
        self._refresh_unsaved()

    def _refresh_unsaved(self) -> None:
        tabs = self._widget("#tabs", TabbedContent)
        if tabs is not None:
            unsaved = self.cfg != self.saved_cfg
            tabs.get_tab("tab-settings").label = SETTINGS_LABEL + ("[b $warning]*[/]" if unsaved else "")

    # -- actions --------------------------------------------------------------

    def action_show_tab(self, tab: str) -> None:
        """Switch tab and take focus with it.

        Focus has to move first. Left on a widget in the old pane, it is hidden
        by the switch, Textual moves it to the next focusable widget in that
        same pane, and focusing inside a pane activates it again, so the tab
        snaps straight back.
        """
        self.set_focus(None)
        self.query_one("#tabs", TabbedContent).active = tab
        target = {
            "tab-queue": "#job-table",
            "tab-settings": "#settings-scroll",
            "tab-command": "#command-scroll",
        }[tab]
        self.query_one(target).focus()

    def action_focus_url(self) -> None:
        self.action_show_tab("tab-queue")
        self.query_one("#url-input", Input).focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Offered only while typing, so esc still reaches anything else that wants it.
        if action == "leave_input":
            return isinstance(self.focused, Input)
        if action in ("cancel_job", "remove_job", "retry_job"):
            # Only where the job is in view: these act on a row the user must see.
            tabs = self._widget("#tabs", TabbedContent)
            job = self.selected_job()
            if job is None or tabs is None or tabs.active != "tab-queue":
                return False
            if action == "cancel_job":
                return job.state not in TERMINAL
            if action == "remove_job":
                return job.state in TERMINAL
            return job.state in (State.FAILED, State.CANCELLED)
        return True

    def action_leave_input(self) -> None:
        """Hand the keyboard back to the shortcuts, which a text box swallows."""
        if self.query_one("#tabs", TabbedContent).active == "tab-queue":
            self.query_one("#job-table").focus()
        else:
            self.set_focus(None)

    def action_cancel_job(self) -> None:
        job = self.selected_job()
        if job is None:
            self.notify("Select a job first.", severity="warning")
            return
        self.engine.cancel(job.id)
        self.log_line(f"cancelling job {job.id}…", "warning")

    def action_remove_job(self) -> None:
        job = self.selected_job()
        if job is not None and self._forget(job.id):
            self.log_line(f"removed job {job.id} from the list")

    def action_clear_finished(self) -> None:
        finished = [j.id for j in list(self.engine.jobs.values()) if j.state in TERMINAL]
        removed = sum(self._forget(job_id) for job_id in finished)
        self.notify(f"Cleared {removed} finished job(s)" if removed else "Nothing finished to clear")

    def _forget(self, job_id: int) -> bool:
        if not self.engine.forget(job_id):
            return False
        table = self.query_one("#job-table", DataTable)
        if job_id in self.rows:
            table.remove_row(str(job_id))
            self.rows.discard(job_id)
        self.refresh_bindings()
        return True

    def action_focus_filter(self) -> None:
        self.action_show_tab("tab-settings")
        self.query_one("#settings-filter", Input).focus()

    def action_retry_job(self) -> None:
        job = self.selected_job()
        if job is None or job.state not in (State.FAILED, State.CANCELLED):
            self.notify("Select a failed or cancelled job to retry.", severity="warning")
            return
        self.add_url(job.url)

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_save_config(self) -> None:
        self._commit_focused()
        path = save_config(self.cfg)
        self.saved_cfg = self.cfg.copy()
        self._refresh_unsaved()
        self.notify(f"Saved {path}")
        self.log_line(f"config saved to [b]{escape(str(path))}[/b]")

    def action_open_output(self) -> None:
        """Open the selected job's folder from the job table, else the output root."""
        table = self._widget("#job-table", DataTable)
        job = self.selected_job() if table is not None and table.has_focus else None
        if job is not None and job.files:
            self._open(job.files[0].parent)
            return
        path = Path((job.config if job else self.cfg).output_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        self._open(path)

    def _open(self, path: Path) -> None:
        opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
        try:
            subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.notify(f"Opened {path}")
        except OSError as exc:
            self.notify(f"Could not open folder: {exc}", severity="error")

    def action_copy_command(self) -> None:
        """Copy the yt-dlp command; from the job table, the selected job's own.

        A job's command stays in the queue, so several can be copied in turn,
        and because the Command tab previews the current settings rather than
        that job's. Anywhere else the tab opens on exactly what was copied.
        """
        table = self._widget("#job-table", DataTable)
        job = self.selected_job() if table is not None and table.has_focus else None
        if job is not None:
            self.copy_to_clipboard(build_command(job.config, [job.url]))
            self.notify(f"Copied the yt-dlp command for job {job.id}")
            return
        self.copy_to_clipboard(build_command(self.cfg, ["<URL>"]))
        self.refresh_command()
        self.action_show_tab("tab-command")
        self.notify("Copied the yt-dlp command")

    def action_inspect(self) -> None:
        """Inspect the pasted URL if there is one, else the selected job.

        The pasted URL wins: the table always has a selected row once it has
        any, so preferring the job would make a pasted URL uninspectable.
        """
        pasted = self.query_one("#url-input", Input).value.strip().split()
        job = None if pasted else self.selected_job()
        url = pasted[0] if pasted else (job.url if job else "")
        if not url:
            self.notify("Paste a URL or select a job to inspect.", severity="warning")
            return
        self.log_line(f"probing [b]{escape(url)}[/b]…")
        self._probe(url, queueable=job is None)

    @work(thread=True, exclusive=True, group="probe")
    def _probe(self, url: str, queueable: bool = False) -> None:
        from ..engine import probe

        try:
            info = probe(url, self.cfg)
        except Exception as exc:  # noqa: BLE001 - report any extractor failure
            self.call_from_thread(self.notify, f"Probe failed: {exc}", severity="error")
            return
        self.call_from_thread(self._show_info, info, url, queueable)

    def _show_info(self, info: dict, url: str, queueable: bool) -> None:
        """The inspector; queueing from it is the same as pressing Add."""

        def queued(yes: bool | None) -> None:
            if yes:
                self.add_url(url)
                field = self.query_one("#url-input", Input)
                if field.value.strip() == url:
                    field.value = ""

        self.push_screen(InfoScreen(info, queueable=queueable), queued)

    def action_profiles(self) -> None:
        self._commit_focused()
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
                self._refresh_unsaved()
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


def run(cfg: Config, urls: list[str] | None = None, *, saved: Config | None = None) -> None:
    YtkitApp(cfg, urls, saved=saved).run()
