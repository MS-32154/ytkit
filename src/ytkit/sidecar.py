"""Human-readable metadata sidecar generated from yt-dlp's info dict."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .romanise import original
from .transcripts import hhmmss


def _date(raw: Any) -> str:
    if not raw:
        return "unknown"
    try:
        return datetime.strptime(str(raw), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return str(raw)


def _count(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{int(value):,}"


def summarise(info: dict[str, Any]) -> str:
    """Render a compact Markdown record of a single video."""
    # Prefer the pre-romanisation title: the sidecar is where the real,
    # searchable name has to survive when the filename has been transliterated.
    title = original(info, "title") or info.get("title") or info.get("id") or "Untitled"
    lines: list[str] = [f"# {title}", ""]
    if original(info, "title"):
        lines += [f"_Filename uses:_ `{info.get('title', '')}`", ""]

    facts: list[tuple[str, str]] = [
        ("URL", info.get("webpage_url") or info.get("original_url") or "—"),
        ("Video ID", info.get("id") or "—"),
        (
            "Channel",
            original(info, "uploader")
            or original(info, "channel")
            or info.get("uploader")
            or info.get("channel")
            or "—",
        ),
        ("Channel URL", info.get("uploader_url") or info.get("channel_url") or "—"),
        ("Uploaded", _date(info.get("upload_date"))),
        (
            "Duration",
            hhmmss(info["duration"]) if isinstance(info.get("duration"), (int, float)) else "—",
        ),
        ("Views", _count(info.get("view_count"))),
        ("Likes", _count(info.get("like_count"))),
        ("Comments", _count(info.get("comment_count"))),
    ]
    if info.get("is_live"):
        facts.append(("Live", "yes"))
    if info.get("availability"):
        facts.append(("Availability", str(info["availability"])))

    lines += ["| Field | Value |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in facts]
    lines.append("")

    if info.get("categories"):
        lines += [f"**Categories:** {', '.join(info['categories'])}", ""]
    tags = info.get("tags") or []
    if tags:
        shown = ", ".join(tags[:30])
        suffix = f" _(+{len(tags) - 30} more)_" if len(tags) > 30 else ""
        lines += [f"**Tags:** {shown}{suffix}", ""]

    fmt = info.get("format")
    if fmt:
        detail = [str(fmt)]
        if info.get("resolution"):
            detail.append(str(info["resolution"]))
        if info.get("fps"):
            detail.append(f"{info['fps']}fps")
        if info.get("vcodec") and info["vcodec"] != "none":
            detail.append(str(info["vcodec"]))
        if info.get("acodec") and info["acodec"] != "none":
            detail.append(str(info["acodec"]))
        lines += [f"**Downloaded format:** {' · '.join(detail)}", ""]

    subs = sorted(set(info.get("subtitles") or {}))
    autos = sorted(set(info.get("automatic_captions") or {}))
    if subs:
        lines += [f"**Subtitle tracks:** {', '.join(subs)}", ""]
    if autos:
        preview = ", ".join(autos[:15])
        suffix = f" _(+{len(autos) - 15} more)_" if len(autos) > 15 else ""
        lines += [f"**Auto-caption tracks:** {preview}{suffix}", ""]

    chapters = info.get("chapters") or []
    if chapters:
        lines += ["## Chapters", ""]
        for ch in chapters:
            start = ch.get("start_time") or 0
            lines.append(f"- `{hhmmss(start)}` {ch.get('title', 'Untitled')}")
        lines.append("")

    description = (info.get("description") or "").strip()
    if description:
        lines += ["## Description", "", description, ""]

    return "\n".join(lines).rstrip() + "\n"


def write_summary(info: dict[str, Any], target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(summarise(info), encoding="utf-8")
    return target
