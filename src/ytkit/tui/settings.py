"""Settings panel, generated from the Config field metadata."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import Field

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Input, Label, Select, Static, Switch

from ..config import Config
from .paths import PathInput

NONE_LABEL = "(none)"


def widget_id(name: str) -> str:
    return f"f-{name.replace('_', '-')}"


CHANGED_MARK = "● "
DEFAULTS = Config().to_dict()


class FieldRow(Horizontal):
    """One config field: label on the left, editor on the right, help beneath."""

    def __init__(self, cfg: Config, field: Field) -> None:
        super().__init__(classes="field-row")
        self.cfg = cfg
        self.field = field
        meta = field.metadata
        self.search_text = f"{meta['label']} {meta['help']} {field.name}".casefold()

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

    def show_changed(self, changed: bool) -> None:
        """Mark a setting that differs from its default; not by colour alone."""
        if self.has_class("-changed") == changed:
            return
        self.set_class(changed, "-changed")
        label = self.field.metadata["label"]
        self.query_one(".field-label", Label).update(CHANGED_MARK + label if changed else label)


class SettingsPanel(Vertical):
    """All config sections, one collapsible each, under a filter that stays put."""

    def __init__(self, cfg: Config, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cfg = cfg
        self._collapsed_before_filter: dict[Collapsible, bool] = {}

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter settings…  ( / )", id="settings-filter")
        with VerticalScroll(id="settings-scroll"):
            yield Static(
                f"Text fields apply on enter or when you leave them; [b]{CHANGED_MARK.strip()}[/b] "
                "marks a changed default. [b]ctrl+s[/b] saves them as your config; [b]esc[/b] then "
                "[b]p[/b] for profiles.",
                classes="panel-hint",
            )
            for index, (section, fields_) in enumerate(Config.sections().items()):
                with Collapsible(title=section, collapsed=index > 1, classes="section"):
                    for f in fields_:
                        yield FieldRow(self.cfg, f)

    def on_mount(self) -> None:
        self.refresh_changed()

    # -- filtering ------------------------------------------------------------

    @on(Input.Changed, "#settings-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.filter(event.value)

    def filter(self, text: str) -> None:
        """Show only the settings whose label, help or name contain `text`.

        Matching sections open while a filter is active, and every section
        goes back to how the user left it once the filter is cleared.
        """
        term = text.strip().casefold()
        sections = list(self.query(Collapsible))
        if term and not self._collapsed_before_filter:
            self._collapsed_before_filter = {s: s.collapsed for s in sections}
        for section in sections:
            rows = list(section.query(FieldRow))
            for row in rows:
                row.display = not term or term in row.search_text
            section.display = any(row.display for row in rows)
            if term:
                section.collapsed = False
        if not term:
            for section, collapsed in self._collapsed_before_filter.items():
                section.collapsed = collapsed
            self._collapsed_before_filter = {}
        self.query_one("#settings-scroll", VerticalScroll).scroll_home(animate=False)

    # -- change handling ------------------------------------------------------

    def _name_for(self, widget_ident: str | None) -> str | None:
        if not widget_ident or not widget_ident.startswith("f-"):
            return None
        candidate = widget_ident[2:].replace("-", "_")
        return candidate if hasattr(self.cfg, candidate) else None

    def apply_change(self, widget_ident: str | None, raw) -> str | None:
        """Push an edited widget value into the config. Returns the field name."""
        name = self._name_for(widget_ident)
        if name is None or not self.cfg.set_from_string(name, raw):
            return None
        self.refresh_changed(name)
        return name

    def label_for(self, widget_ident: str | None) -> str:
        name = self._name_for(widget_ident)
        return Config.__dataclass_fields__[name].metadata["label"] if name else ""

    def accepts(self, widget_ident: str | None, raw) -> bool:
        """Whether `raw` would be accepted, without touching the config."""
        name = self._name_for(widget_ident)
        return name is not None and Config.parse_value(name, raw)[0]

    def refresh_changed(self, name: str | None = None) -> None:
        """Re-mark settings that differ from their defaults (one, or all)."""
        for row in self.query(FieldRow):
            if name is None or row.field.name == name:
                row.show_changed(getattr(self.cfg, row.field.name) != DEFAULTS[row.field.name])

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
                widget.remove_class("-invalid")
        self.refresh_changed()


def iter_fields() -> Iterator[Field]:
    for _, f in Config.spec():
        yield f
