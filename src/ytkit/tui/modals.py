"""Modal screens: metadata inspector, profile manager, confirmation."""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Markdown, Static

from ..sidecar import summarise
from ..storage import list_profiles


class InfoScreen(ModalScreen[None]):
    """Read-only view of probed metadata."""

    BINDINGS = [("escape,q", "dismiss", "Close")]

    def __init__(self, info: dict[str, Any]) -> None:
        super().__init__()
        self.info = info

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-info", classes="modal"):
            yield Label("Metadata", classes="modal-title")
            with VerticalScroll():
                yield Markdown(self._body())
            yield Static("esc to close", classes="modal-hint")

    def _body(self) -> str:
        info = self.info
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            lines = [
                f"# {info.get('title', 'Playlist')}",
                "",
                f"**{len(entries)}** entries · {info.get('uploader') or info.get('channel') or '—'}",
                "",
            ]
            for i, entry in enumerate(entries[:100], 1):
                lines.append(f"{i}. {entry.get('title', entry.get('id', '?'))}")
            if len(entries) > 100:
                lines.append(f"\n_…and {len(entries) - 100} more_")
            return "\n".join(lines)
        return summarise(info)


class ProfileScreen(ModalScreen[tuple[str, str] | None]):
    """Save or load a named profile. Dismisses with (action, name)."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        profiles = list_profiles()
        with Vertical(id="modal-profiles", classes="modal"):
            yield Label("Profiles", classes="modal-title")
            if profiles:
                yield ListView(*[ListItem(Label(p), id=f"p-{i}") for i, p in enumerate(profiles)], id="profile-list")
            else:
                yield Static("No saved profiles yet.", classes="modal-hint")
            yield Input(placeholder="Profile name…", id="profile-name")
            with Horizontal(classes="modal-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Load", id="load")
                yield Button("Delete", variant="error", id="delete")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#profile-name", Input).focus()
        self._profiles = list_profiles()

    @on(ListView.Highlighted)
    def _pick(self, event: ListView.Highlighted) -> None:
        if event.list_view.index is not None and 0 <= event.list_view.index < len(self._profiles):
            self.query_one("#profile-name", Input).value = self._profiles[event.list_view.index]

    @on(Button.Pressed)
    def _act(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        name = self.query_one("#profile-name", Input).value.strip()
        if not name:
            self.notify("Enter a profile name first.", severity="warning")
            return
        self.dismiss((event.button.id or "", name))


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-confirm", classes="modal"):
            yield Label(self.question, classes="modal-title")
            with Horizontal(classes="modal-buttons"):
                yield Button("Yes", variant="error", id="yes")
                yield Button("No", variant="primary", id="no")

    @on(Button.Pressed)
    def _act(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")
