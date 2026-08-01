"""Romanise non-Latin metadata so filenames stay navigable in a terminal.

Right-to-left titles are the motivating case: the Unicode bidirectional
algorithm reorders them for display, so what you see in `ls` does not match the
byte order you have to type, and tab-completion becomes guesswork. Latin
filenames sidestep the problem entirely.

The original strings are never lost — they are stashed on the info dict and
written into the .summary.md and .info.json sidecars.
"""

from __future__ import annotations

import re
from typing import Any

from anyascii import anyascii

# Fields that end up in a path. `uploader` and `channel` matter as much as the
# title: the default output template makes the uploader a directory.
ROMANISED_FIELDS = ("title", "fulltitle", "uploader", "channel", "playlist", "playlist_title")

ORIGINAL_PREFIX = "ytkit_original_"

# yt-dlp rewrites these to fullwidth look-alikes, which would put non-ASCII
# straight back into the filename. Map them to plain equivalents first.
_FULLWIDTH_TRIGGERS = {
    ":": " -",
    "/": "-",
    "\\": "-",
    "|": "-",
    "?": "",
    "*": "",
    '"': "",
    "<": "(",
    ">": ")",
}

# anyascii renders some letters as shell metacharacters — Arabic ain becomes a
# backtick, which is genuinely dangerous in an unquoted path.
_SHELL_HOSTILE = "`'$!&;(){}[]~^"

_TRANSLATION = str.maketrans(
    {**_FULLWIDTH_TRIGGERS, **dict.fromkeys(_SHELL_HOSTILE, "")}
)
_SPACE = re.compile(r"\s+")


def is_ascii(text: str) -> bool:
    return text.isascii()


def romanise(text: str) -> str:
    """Best-effort Latin rendering, safe to embed in a filename."""
    converted = anyascii(text).translate(_TRANSLATION)
    converted = _SPACE.sub(" ", converted)
    return converted.strip(" .-")


def romanise_info(info: dict[str, Any]) -> dict[str, Any]:
    """Romanise path-forming fields in place, stashing the originals.

    Already-ASCII values are left untouched, so English titles keep their
    punctuation and mixed-script titles keep whatever Latin they contain.
    """
    for field in ROMANISED_FIELDS:
        value = info.get(field)
        if not isinstance(value, str) or not value.strip() or is_ascii(value):
            continue
        converted = romanise(value)
        if not converted:
            # Nothing survived (e.g. an emoji-only title): keep the ID rather
            # than emit an empty filename component.
            converted = str(info.get("id") or "untitled")
        info.setdefault(f"{ORIGINAL_PREFIX}{field}", value)
        info[field] = converted
    return info


def original(info: dict[str, Any], field: str) -> str | None:
    """The pre-romanisation value of a field, if it was changed."""
    value = info.get(f"{ORIGINAL_PREFIX}{field}")
    return value if isinstance(value, str) else None
