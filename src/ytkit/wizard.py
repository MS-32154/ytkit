"""Guided prompt flow for people who would rather answer questions than
memorise flags. Falls back cleanly when the terminal is not interactive."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import questionary
from questionary import Choice
from rich.console import Console

from .argv import build_command
from .config import Config
from .presets import BY_KEY, PRESETS

STYLE = questionary.Style(
    [
        ("qmark", "fg:#00afaf bold"),
        ("question", "bold"),
        ("answer", "fg:#5fd7ff bold"),
        ("pointer", "fg:#00afaf bold"),
        ("highlighted", "fg:#00afaf bold"),
        ("selected", "fg:#5fd7ff"),
        ("separator", "fg:#6c6c6c"),
        ("instruction", "fg:#8a8a8a"),
    ]
)


class Cancelled(Exception):
    """The user aborted the wizard."""


def _ask(question):
    answer = question.ask()
    if answer is None:
        raise Cancelled
    return answer


def run_wizard(cfg: Config, console: Console) -> tuple[Config, list[str]]:
    """Walk the user through a download. Returns the config and the URLs."""
    console.print()
    console.rule("[bold cyan]ytkit[/bold cyan] · guided download")
    console.print()

    raw = _ask(
        questionary.text(
            "URL(s) to download",
            instruction="(space-separate several)",
            validate=lambda t: bool(t.strip()) or "Enter at least one URL",
            style=STYLE,
        )
    )
    urls = raw.split()

    preset_key = _ask(
        questionary.select(
            "What do you want out of it?",
            choices=[Choice(f"{p.label} — {p.description}", value=p.key) for p in PRESETS]
            + [Choice("Custom — choose every option myself", value="custom")],
            style=STYLE,
        )
    )

    cfg = cfg.copy() if preset_key == "custom" else BY_KEY[preset_key].apply(cfg)

    cfg = _custom_flow(cfg) if preset_key == "custom" else _refine_flow(cfg)

    output = _ask(
        questionary.path(
            "Save to",
            default=str(Path(cfg.output_dir).expanduser()),
            only_directories=True,
            style=STYLE,
        )
    )
    cfg.output_dir = output.strip() or cfg.output_dir

    if _ask(questionary.confirm("Organise into a folder per channel?", default="/" in cfg.output_template, style=STYLE)):
        if "%(uploader)s/" not in cfg.output_template:
            cfg.output_template = "%(uploader)s/" + cfg.output_template.split("/")[-1]
    else:
        cfg.output_template = cfg.output_template.split("/")[-1]

    console.print()
    console.print("[bold]Equivalent command[/bold]")
    console.print(f"[cyan]{build_command(cfg, urls)}[/cyan]", soft_wrap=True)
    console.print()

    if not _ask(questionary.confirm("Start download?", default=True, style=STYLE)):
        raise Cancelled
    return cfg, urls


def _refine_flow(cfg: Config) -> Config:
    """Two quick follow-ups that matter regardless of preset."""
    if cfg.mode == "video":
        cfg.video_quality = _ask(
            questionary.select(
                "Maximum quality",
                choices=["best", "2160", "1440", "1080", "720", "480", "360"],
                default=cfg.video_quality,
                style=STYLE,
            )
        )
    if cfg.subtitles or cfg.auto_subtitles:
        langs = _ask(
            questionary.text(
                "Subtitle languages",
                default=",".join(cfg.sub_langs),
                instruction="(comma separated, or 'all')",
                style=STYLE,
            )
        )
        cfg.sub_langs = [p.strip() for p in langs.split(",") if p.strip()] or ["en"]
    return cfg


def _custom_flow(cfg: Config) -> Config:
    cfg.mode = _ask(
        questionary.select(
            "Download mode",
            choices=[
                Choice("Video (with audio)", value="video"),
                Choice("Audio only", value="audio"),
                Choice("Subtitles / transcript only", value="subtitles-only"),
                Choice("Metadata only", value="metadata-only"),
            ],
            default=cfg.mode,
            style=STYLE,
        )
    )

    if cfg.mode == "video":
        cfg.video_quality = _ask(
            questionary.select(
                "Maximum quality",
                choices=["best", "2160", "1440", "1080", "720", "480", "360", "worst"],
                default=cfg.video_quality,
                style=STYLE,
            )
        )
        cfg.video_codec = _ask(
            questionary.select(
                "Preferred codec",
                choices=["any", "h264", "h265", "vp9", "av1"],
                default=cfg.video_codec,
                style=STYLE,
            )
        )
        cfg.container = _ask(
            questionary.select(
                "Container",
                choices=["auto", "mp4", "mkv", "webm"],
                default=cfg.container,
                style=STYLE,
            )
        )
    elif cfg.mode == "audio":
        cfg.audio_format = _ask(
            questionary.select(
                "Audio format",
                choices=["mp3", "m4a", "opus", "flac", "wav", "best"],
                default=cfg.audio_format,
                style=STYLE,
            )
        )
        cfg.audio_quality = _ask(
            questionary.text(
                "Audio quality", default=cfg.audio_quality, instruction="(0-10 VBR, or 192K)", style=STYLE
            )
        )

    wanted = _ask(
        questionary.checkbox(
            "Also fetch",
            choices=[
                Choice("Subtitles / captions", value="subs", checked=cfg.subtitles),
                Choice("Auto-generated captions", value="auto", checked=cfg.auto_subtitles),
                Choice("Plain-text transcript", value="transcript", checked=cfg.transcript_txt),
                Choice("Thumbnail", value="thumb", checked=cfg.write_thumbnail),
                Choice("Full metadata (.info.json)", value="json", checked=cfg.write_info_json),
                Choice("Description", value="desc", checked=cfg.write_description),
                Choice("Comments", value="comments", checked=cfg.write_comments),
                Choice("Markdown summary", value="summary", checked=cfg.write_summary),
            ],
            style=STYLE,
        )
    )
    cfg = replace(
        cfg,
        subtitles="subs" in wanted,
        auto_subtitles="auto" in wanted,
        transcript_txt="transcript" in wanted,
        write_thumbnail="thumb" in wanted,
        write_info_json="json" in wanted,
        write_description="desc" in wanted,
        write_comments="comments" in wanted,
        write_summary="summary" in wanted,
    )

    if cfg.subtitles or cfg.auto_subtitles:
        langs = _ask(
            questionary.text(
                "Subtitle languages",
                default=",".join(cfg.sub_langs),
                instruction="(comma separated, or 'all')",
                style=STYLE,
            )
        )
        cfg.sub_langs = [p.strip() for p in langs.split(",") if p.strip()] or ["en"]
        cfg.transcript_timestamps = _ask(
            questionary.confirm(
                "Include timestamps in the transcript?",
                default=cfg.transcript_timestamps,
                style=STYLE,
            )
        )

    extras = _ask(
        questionary.checkbox(
            "Extras",
            choices=[
                Choice("Embed metadata and chapters", value="embed_meta", checked=cfg.embed_metadata),
                Choice("Embed thumbnail as cover art", value="embed_thumb", checked=cfg.embed_thumbnail),
                Choice("Embed subtitles into the video", value="embed_subs", checked=cfg.embed_subs),
                Choice("Remove sponsor segments (SponsorBlock)", value="sponsor", checked=bool(cfg.sponsorblock_remove)),
                Choice("Split file by chapters", value="split", checked=cfg.split_chapters),
                Choice("Keep a download archive (skip repeats)", value="archive", checked=bool(cfg.archive_file)),
            ],
            style=STYLE,
        )
    )
    cfg = replace(
        cfg,
        embed_metadata="embed_meta" in extras,
        embed_chapters="embed_meta" in extras,
        embed_thumbnail="embed_thumb" in extras,
        embed_subs="embed_subs" in extras,
        sponsorblock_remove=["sponsor", "selfpromo"] if "sponsor" in extras else [],
        split_chapters="split" in extras,
    )
    if "archive" in extras and not cfg.archive_file:
        cfg.archive_file = str(Path(cfg.output_dir).expanduser() / ".ytkit-archive.txt")
    elif "archive" not in extras:
        cfg.archive_file = ""

    return cfg
