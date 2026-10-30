"""TOML persistence for the config and named profiles."""

from __future__ import annotations

import tomllib
from dataclasses import fields
from pathlib import Path
from typing import Any

import tomli_w

from .config import CONFIG_DIR, CONFIG_PATH, PROFILE_DIR, Config, parse_bool

# Interface preferences (the TUI theme), kept apart from the download config
# so they never show up as a setting or a yt-dlp option.
UI_PATH = CONFIG_DIR / "tui.toml"


def _coerce(cfg_cls: type[Config], data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known keys and repair loose types (ints given as strings, etc.)."""
    known = {f.name: f for f in fields(cfg_cls)}
    out: dict[str, Any] = {}
    for key, value in data.items():
        f = known.get(key)
        if f is None:
            continue
        kind = f.metadata["kind"]
        try:
            if kind == "bool":
                parsed = parse_bool(value)
                if parsed is None:
                    continue  # unrecognised word: keep the default
                out[key] = parsed
            elif kind == "int":
                out[key] = int(value)
            elif kind == "list":
                if isinstance(value, str):
                    value = [p.strip() for p in value.split(",") if p.strip()]
                out[key] = [str(v) for v in value]
            elif isinstance(cfg_cls.default_for(key), float):
                out[key] = float(value)
            else:
                out[key] = str(value)
        except (TypeError, ValueError):
            continue  # malformed entry: fall back to the default
    return out


def _flatten(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept both a flat table and one grouped into [sections]."""
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def load_config(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.is_file():
        return Config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return Config(**_coerce(Config, _flatten(raw)))


def save_config(cfg: Config, path: Path | None = None, *, full: bool = False) -> Path:
    """Write config grouped by section. By default only non-default values."""
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    values = cfg.to_dict() if full else cfg.diff_from_defaults()
    grouped: dict[str, dict[str, Any]] = {}
    by_name = {f.name: f for f in fields(Config)}
    for key, value in values.items():
        section = by_name[key].metadata["section"].lower().replace(" & ", "_").replace(" ", "_")
        grouped.setdefault(section, {})[key] = value

    header = (
        "# ytkit configuration\n"
        "# Values omitted here fall back to built-in defaults.\n"
        "# Run `ytkit config --path` to locate this file.\n\n"
    )
    path.write_text(header + tomli_w.dumps(grouped), encoding="utf-8")
    return path


# --- interface state ---------------------------------------------------------


def load_ui_state(path: Path | None = None) -> dict[str, Any]:
    path = path or UI_PATH
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save_ui_state(state: dict[str, Any], path: Path | None = None) -> Path:
    path = path or UI_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # Via a temporary file, so a crash mid-write can't leave half a file.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(tomli_w.dumps(state), encoding="utf-8")
    tmp.replace(path)
    return path


# --- profiles ----------------------------------------------------------------


def profile_path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip().replace(" ", "-")
    return PROFILE_DIR / f"{safe or 'unnamed'}.toml"


def list_profiles() -> list[str]:
    if not PROFILE_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROFILE_DIR.glob("*.toml"))


def save_profile(name: str, cfg: Config) -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return save_config(cfg, profile_path(name), full=True)


def load_profile(name: str) -> Config:
    path = profile_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"No such profile: {name}")
    return load_config(path)


def delete_profile(name: str) -> bool:
    path = profile_path(name)
    if path.is_file():
        path.unlink()
        return True
    return False
