"""Path completion for the settings form."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from textual.binding import Binding
from textual.suggester import Suggester
from textual.widgets import Input

SEPARATORS = {"/", os.sep} | ({os.altsep} if os.altsep else set())


def common_prefix(words: list[str]) -> str:
    """The characters every word starts with.

    Not os.path.commonprefix, deprecated in 3.15, nor its suggested
    replacement commonpath, which compares whole path components: completion
    needs the shared characters of names like "Music/" and "Movies/".
    """
    if not words:
        return ""
    first, last = min(words), max(words)  # the two that differ soonest
    i = 0
    while i < min(len(first), len(last)) and first[i] == last[i]:
        i += 1
    return first[:i]


def complete_path(value: str, *, dirs_only: bool = False) -> str | None:
    """Complete a path the way a shell's tab completion would.

    A unique match completes in full, with a trailing slash on folders so the
    next segment can follow straight on. Several matches complete only as far
    as they agree; when that adds nothing there is no suggestion, rather than
    an arbitrary pick the user would have to delete.
    """
    if value == "~":
        return "~/"
    cut = max(value.rfind(sep) for sep in SEPARATORS) + 1
    head, stem = value[:cut], value[cut:]
    show_hidden = stem.startswith(".")
    matches: list[str] = []
    try:
        with os.scandir(Path(head or ".").expanduser()) as entries:
            for entry in entries:
                if not entry.name.startswith(stem):
                    continue
                if entry.name.startswith(".") and not show_hidden:
                    continue
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                if is_dir:
                    matches.append(entry.name + "/")
                elif not dirs_only:
                    matches.append(entry.name)
    except OSError:
        return None
    if len(matches) == 1:
        return head + matches[0]
    common = common_prefix(matches)
    return head + common if len(common) > len(stem) else None


class PathSuggester(Suggester):
    def __init__(self, *, dirs_only: bool = False) -> None:
        # Paths are case-sensitive, and the filesystem changes under a cache.
        super().__init__(use_cache=False, case_sensitive=True)
        self.dirs_only = dirs_only

    async def get_suggestion(self, value: str) -> str | None:
        # Off the event loop: a big folder or a network mount would stall typing.
        return await asyncio.to_thread(complete_path, value, dirs_only=self.dirs_only)


class PathInput(Input):
    """An Input that completes paths, accepting with tab as well as right.

    Tab completes only once the field has been edited. Otherwise tabbing
    through the form would complete every path it passed, rewriting settings
    the user only meant to skip over.
    """

    BINDINGS = [Binding("tab", "complete", "Complete", show=False)]

    def __init__(self, *args, dirs_only: bool = False, **kwargs) -> None:
        super().__init__(*args, suggester=PathSuggester(dirs_only=dirs_only), **kwargs)
        self._value_on_focus = self.value

    def _on_focus(self, event) -> None:
        self._value_on_focus = self.value
        super()._on_focus(event)

    def action_complete(self) -> None:
        edited = self.value != self._value_on_focus
        if edited and self.cursor_at_end and len(self._suggestion) > len(self.value):
            self.action_cursor_right()  # Input's own accept-the-suggestion path
        else:
            self.screen.focus_next()
