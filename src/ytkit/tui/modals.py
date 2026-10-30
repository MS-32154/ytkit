"""Modal screens: metadata inspector, profile manager, confirmation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Markdown, Static

from ..sidecar import summarise
from ..storage import list_profiles


class InfoScreen(ModalScreen[bool]):
    """View of probed metadata. Dismisses True when the user queues it."""

    BINDINGS = [("escape,q", "close", "Close"), ("a", "queue", "Queue")]

    def __init__(self, info: dict[str, Any], *, queueable: bool = False) -> None:
        super().__init__()
        self.info = info
        self.queueable = queueable

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-info", classes="modal"):
            yield Label("Metadata", classes="modal-title")
            with VerticalScroll():
                yield Markdown(self._body())
            with Horizontal(classes="modal-buttons"):
                if self.queueable:
                    yield Static("a queues it · esc closes", classes="modal-hint")
                    yield Button("Queue", variant="primary", id="queue")
                else:
                    yield Static("esc closes", classes="modal-hint")
                yield Button("Close", id="close")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return self.queueable if action == "queue" else True

    def action_queue(self) -> None:
        self.dismiss(True)

    def action_close(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed)
    def _act(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "queue")

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


class ProfileList(ListView):
    """A ListView where Enter loads, but a click only picks.

    ListView posts Selected for both, and a click that loaded would discard
    unsaved settings when the user only meant to pick a profile to delete.
    """

    @dataclass
    class Chosen(Message):
        index: int

    def action_select_cursor(self) -> None:
        if self.index is not None:
            self.post_message(self.Chosen(self.index))


class ProfileScreen(ModalScreen[tuple[str, str] | None]):
    """Save or load a named profile. Dismisses with (action, name)."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        profiles = list_profiles()
        with Vertical(id="modal-profiles", classes="modal"):
            yield Label("Profiles", classes="modal-title")
            if profiles:
                yield ProfileList(
                    *[ListItem(Label(p), id=f"p-{i}") for i, p in enumerate(profiles)], id="profile-list"
                )
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

    @on(ProfileList.Chosen)
    def _load_chosen(self, event: ProfileList.Chosen) -> None:
        if 0 <= event.index < len(self._profiles):
            self.dismiss(("load", self._profiles[event.index]))

    @on(Input.Submitted, "#profile-name")
    def _save_typed(self) -> None:
        self._act_on("save")

    @on(Button.Pressed)
    def _act(self, event: Button.Pressed) -> None:
        self._act_on(event.button.id or "")

    def _act_on(self, action: str) -> None:
        if action == "cancel":
            self.dismiss(None)
            return
        name = self.query_one("#profile-name", Input).value.strip()
        if not name:
            self.notify("Enter a profile name first.", severity="warning")
            return
        # The name box starts out holding a listed profile, so a save there is
        # as destructive as a delete: both ask first.
        question = {
            "delete": f"Delete the profile '{name}'?",
            "save": f"Replace the saved profile '{name}' with the current settings?",
        }.get(action)
        if question and (action == "delete" or name in self._profiles):

            def confirmed(yes: bool | None) -> None:
                # Not a lambda: Textual awaits a callback's return value, and
                # awaiting dismiss() from inside this screen raises.
                if yes:
                    self.dismiss((action, name))

            self.app.push_screen(ConfirmScreen(question), confirmed)
            return
        self.dismiss((action, name))


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
