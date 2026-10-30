"""Settings panel, generated from the Config field metadata."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import Field

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Collapsible, Input, Label, Select, Static, Switch

from ..config import Config
from .paths import PathInput

NONE_LABEL = "(none)"


def widget_id(name: str) -> str:
    return f"f-{name.replace('_', '-')}"


class FieldRow(Horizontal):
    """One config field: label on the left, editor on the right, help beneath."""

    def __init__(self, cfg: Config, field: Field) -> None:
        super().__init__(classes="field-row")
        self.cfg = cfg
        self.field = field

    def compose(self) -> ComposeResult:
        f = self.field
        meta = f.metadata
        yield Label(meta["label"], classes="field-label")
        kind = meta["kind"]
        wid = widget_id(f.name)
        value = getattr(self.cfg, f.name)

        if kind == "bool":
            yield Switch(value=bool(value), id=wid, classes="field-input")
        elif kind == "choice":
            options = [(NONE_LABEL if c == "" else c, c) for c in meta["choices"]]
            current = value if value in meta["choices"] else meta["choices"][0]
            yield Select(options, value=current, id=wid, allow_blank=False, classes="field-input")
        elif kind in ("path", "dir"):
            yield PathInput(
                value=self.cfg.display(f.name),
                placeholder=meta["placeholder"] or "type a path, tab to complete",
                id=wid,
                dirs_only=kind == "dir",
                classes="field-input",
            )
        else:
            yield Input(
                value=self.cfg.display(f.name),
                placeholder=meta["placeholder"],
                id=wid,
                type="integer" if kind == "int" else "text",
                classes="field-input",
            )

        if meta["help"]:
            yield Static(meta["help"], classes="field-help")


class SettingsPanel(VerticalScroll):
    """All config sections, one collapsible each."""

    def __init__(self, cfg: Config, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        yield Static(
            "Changes apply to new downloads immediately. "
            "Press [b]ctrl+s[/b] to save them as your config; [b]esc[/b] then [b]p[/b] for profiles.",
            classes="panel-hint",
        )
        for index, (section, fields_) in enumerate(Config.sections().items()):
            with Collapsible(title=section, collapsed=index > 1, classes="section"):
                for f in fields_:
                    yield FieldRow(self.cfg, f)

    # -- change handling ------------------------------------------------------

    def _name_for(self, widget_ident: str | None) -> str | None:
        if not widget_ident or not widget_ident.startswith("f-"):
            return None
        candidate = widget_ident[2:].replace("-", "_")
        return candidate if hasattr(self.cfg, candidate) else None

    def apply_change(self, widget_ident: str | None, raw) -> str | None:
        """Push an edited widget value into the config. Returns the field name."""
        name = self._name_for(widget_ident)
        if name is None:
            return None
        return name if self.cfg.set_from_string(name, raw) else None

    def refresh_values(self) -> None:
        """Re-sync every widget from the config (after loading a profile)."""
        for f in iter_fields():
            try:
                widget = self.query_one(f"#{widget_id(f.name)}")
            except Exception:  # noqa: BLE001 - panel may not be mounted yet
                continue
            value = getattr(self.cfg, f.name)
            if isinstance(widget, Switch):
                widget.value = bool(value)
            elif isinstance(widget, Select):
                if value in f.metadata["choices"]:
                    widget.value = value
            elif isinstance(widget, Input):
                widget.value = self.cfg.display(f.name)


def iter_fields() -> Iterator[Field]:
    for _, f in Config.spec():
        yield f
