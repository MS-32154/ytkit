"""Turn caption files into readable transcripts.

YouTube's auto-generated captions are *rolling*: each cue repeats the tail of
the previous one so the on-screen text scrolls. Dumping them naively triples
the word count, so cues are de-duplicated line-wise before reflowing.
"""

from __future__ import annotations

import re
import textwrap
from collections import deque
from dataclasses import dataclass
from pathlib import Path

CAPTION_SUFFIXES = (".vtt", ".srt", ".ass", ".ssa")

_TIMING = re.compile(
    r"(?P<start>\d{1,3}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,3}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)
_TAG = re.compile(r"<[^>]+>")
_ASS_BRACE = re.compile(r"\{[^}]*\}")


@dataclass(slots=True)
class Cue:
    start: float
    end: float
    lines: list[str]


def _seconds(stamp: str) -> float:
    stamp = stamp.replace(",", ".")
    parts = stamp.split(":")
    if len(parts) == 2:
        parts = ["0", *parts]
    h, m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)


def hhmmss(total: float) -> str:
    total = max(0, int(total))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _clean(line: str) -> str:
    line = _TAG.sub("", line)
    line = _ASS_BRACE.sub("", line)
    line = line.replace("\\N", " ").replace("&nbsp;", " ")
    line = (
        line.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    return " ".join(line.split())


def parse_ass(text: str) -> list[Cue]:
    cues: list[Cue] = []
    fmt: list[str] = []
    for raw in text.splitlines():
        if raw.startswith("Format:") and not fmt:
            fmt = [p.strip() for p in raw.split(":", 1)[1].split(",")]
        elif raw.startswith("Dialogue:") and fmt:
            body = raw.split(":", 1)[1]
            parts = body.split(",", len(fmt) - 1)
            row = dict(zip(fmt, parts, strict=False))
            content = _clean(row.get("Text", ""))
            if not content:
                continue
            try:
                start, end = _seconds(row["Start"].strip()), _seconds(row["End"].strip())
            except (KeyError, ValueError):
                start = end = 0.0
            cues.append(Cue(start, end, [content]))
    return cues


def parse_cues(text: str, suffix: str = ".vtt") -> list[Cue]:
    """Parse WebVTT / SRT / ASS into cues, dropping styling and metadata blocks."""
    if suffix in (".ass", ".ssa"):
        return parse_ass(text)

    cues: list[Cue] = []
    block: list[str] = []
    timing: tuple[float, float] | None = None
    in_note = False

    def flush() -> None:
        nonlocal block, timing
        if timing is not None:
            lines = [c for c in (_clean(b) for b in block) if c]
            if lines:
                cues.append(Cue(timing[0], timing[1], lines))
        block, timing = [], None

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            flush()
            in_note = True
            continue
        if not stripped:
            flush()
            in_note = False
            continue
        if in_note:
            continue
        match = _TIMING.search(stripped)
        if match:
            flush()
            timing = (_seconds(match["start"]), _seconds(match["end"]))
            continue
        if timing is None:
            continue  # cue identifier / sequence number
        block.append(raw)
    flush()
    return cues


LOOKBACK = 8
_MIN_SUBSET = 8  # below this, a "contained" line is probably a real short phrase


def _novel_part(line: str, recent: deque[str]) -> str:
    """Return only the part of `line` not already emitted.

    Auto-captions repeat in two shapes: verbatim line carry-over, and cues that
    *grow* word by word ("we're going" -> "we're going to talk"). Both collapse
    to a prefix relationship against something recently emitted.
    """
    for previous in reversed(recent):
        if line == previous:
            return ""
        if line.startswith(previous):
            line = line[len(previous):].lstrip(" -–—")
            if not line:
                return ""
        elif len(line) >= _MIN_SUBSET and previous.startswith(line):
            return ""
    return line


def dedupe(cues: list[Cue]) -> list[Cue]:
    """Drop text a cue inherited from its predecessors (rolling captions)."""
    out: list[Cue] = []
    recent: deque[str] = deque(maxlen=LOOKBACK)
    for cue in cues:
        fresh: list[str] = []
        for line in cue.lines:
            piece = _novel_part(line, recent)
            if piece:
                fresh.append(piece)
                recent.append(piece)
        if fresh:
            out.append(Cue(cue.start, cue.end, fresh))
    return out


def render(
    cues: list[Cue],
    *,
    timestamps: bool = False,
    width: int = 100,
    paragraph_gap: float = 3.0,
) -> str:
    """Reflow cues into prose, or into timestamped blocks."""
    cues = dedupe(cues)
    if not cues:
        return ""

    if timestamps:
        lines = []
        for cue in cues:
            body = " ".join(cue.lines)
            lines.append(f"[{hhmmss(cue.start)}] {body}")
        return "\n".join(lines) + "\n"

    paragraphs: list[list[str]] = [[]]
    previous_end = cues[0].start
    for cue in cues:
        if cue.start - previous_end > paragraph_gap and paragraphs[-1]:
            paragraphs.append([])
        paragraphs[-1].extend(cue.lines)
        previous_end = cue.end

    chunks = []
    for para in paragraphs:
        if not para:
            continue
        chunks.append(textwrap.fill(" ".join(para), width=width))
    return "\n\n".join(chunks) + "\n"


def transcript_path(caption: Path) -> Path:
    """`Title.en.vtt` -> `Title.en.txt`, keeping the language tag."""
    return caption.with_suffix(".txt")


def convert_file(
    caption: Path, *, timestamps: bool = False, header: str | None = None
) -> Path | None:
    """Write a plain-text transcript beside a caption file. Returns the new path."""
    try:
        raw = caption.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    body = render(parse_cues(raw, caption.suffix.lower()), timestamps=timestamps)
    if not body.strip():
        return None
    target = transcript_path(caption)
    text = f"{header.rstrip()}\n\n{body}" if header else body
    target.write_text(text, encoding="utf-8")
    return target


def find_captions(directory: Path, stem: str) -> list[Path]:
    """Caption files belonging to a media file stem, e.g. `Title.en-US.vtt`."""
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.glob(f"{glob_escape(stem)}*")
        if p.suffix.lower() in CAPTION_SUFFIXES
    )


def glob_escape(text: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", text)
