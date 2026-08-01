"""Command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table

from . import transcripts
from .argv import build_command
from .config import CONFIG_PATH, Config
from .presets import PRESETS, apply_preset
from .runner import run_downloads
from .storage import (
    delete_profile,
    list_profiles,
    load_config,
    load_profile,
    save_config,
    save_profile,
)

console = Console()
err = Console(stderr=True)

app = typer.Typer(
    name="ytkit",
    help="Download YouTube video, audio, captions, transcripts and metadata.",
    add_completion=True,
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
profile_app = typer.Typer(help="Manage saved setting profiles.")
app.add_typer(profile_app, name="profile")


# --- shared option plumbing --------------------------------------------------


def _base_config(config_file: Path | None, profile: str | None, preset: str | None) -> Config:
    cfg = load_profile(profile) if profile else load_config(config_file)
    if preset:
        try:
            cfg = apply_preset(cfg, preset)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
    return cfg


def _apply_overrides(cfg: Config, overrides: list[str]) -> Config:
    for item in overrides:
        if "=" not in item:
            raise typer.BadParameter(f"--set expects key=value, got '{item}'")
        key, value = item.split("=", 1)
        key = key.strip().replace("-", "_")
        if not hasattr(cfg, key):
            raise typer.BadParameter(f"Unknown setting '{key}'. See `ytkit config --show`.")
        if not cfg.set_from_string(key, value.strip()):
            raise typer.BadParameter(f"Invalid value for '{key}': {value!r}")
    return cfg


def _flag(cfg: Config, name: str, value) -> None:
    if value is not None:
        setattr(cfg, name, value)


# --- commands ----------------------------------------------------------------


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V", help="Show the version and exit."),
) -> None:
    """Run with no arguments to launch the interactive TUI."""
    if version:
        from . import __version__

        console.print(f"ytkit {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        # Not ctx.invoke(tui): Click would pass Typer's OptionInfo sentinels
        # through as the parameter values instead of resolving the defaults.
        _launch_tui()


def _launch_tui(
    urls: list[str] | None = None,
    config_file: Path | None = None,
    profile: str | None = None,
    preset: str | None = None,
) -> None:
    from .tui.app import run

    run(_base_config(config_file, profile, preset), list(urls or []))


@app.command()
def tui(
    urls: list[str] = typer.Argument(None, help="URLs to queue on startup."),
    config_file: Path | None = typer.Option(None, "--config", "-c", help="Config file to load."),
    profile: str | None = typer.Option(None, "--profile", "-P", help="Saved profile to load."),
    preset: str | None = typer.Option(None, "--preset", "-p", help="Preset to start from."),
) -> None:
    """Launch the full-screen terminal UI."""
    _launch_tui(urls, config_file, profile, preset)


@app.command()
def get(
    urls: list[str] = typer.Argument(..., help="Video, playlist or channel URLs."),
    config_file: Path | None = typer.Option(None, "--config", "-c", help="Config file to load."),
    profile: str | None = typer.Option(None, "--profile", "-P", help="Saved profile to load."),
    preset: str | None = typer.Option(None, "--preset", "-p", help="Preset: see `ytkit presets`."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output directory."),
    template: str | None = typer.Option(None, "--template", "-t", help="Output filename template."),
    mode: str | None = typer.Option(None, "--mode", "-m", help="video | audio | subtitles-only | metadata-only."),
    quality: str | None = typer.Option(None, "--quality", "-q", help="best, 1080, 720, … or worst."),
    container: str | None = typer.Option(None, "--container", help="mp4, mkv, webm, auto."),
    audio_format: str | None = typer.Option(None, "--audio-format", help="mp3, m4a, opus, flac, …"),
    fmt: str | None = typer.Option(None, "--format", "-f", help="Raw yt-dlp format selector."),
    langs: str | None = typer.Option(None, "--langs", "-l", help="Subtitle languages, comma separated, or 'all'."),
    subs: bool | None = typer.Option(None, "--subs/--no-subs", help="Download subtitles."),
    transcript_: bool | None = typer.Option(None, "--transcript/--no-transcript", help="Write plain-text transcripts."),
    timestamps: bool | None = typer.Option(None, "--timestamps/--no-timestamps", help="Timestamps in transcripts."),
    thumbnail: bool | None = typer.Option(None, "--thumbnail/--no-thumbnail", help="Write the thumbnail."),
    info_json: bool | None = typer.Option(None, "--info-json/--no-info-json", help="Write the .info.json."),
    comments: bool | None = typer.Option(None, "--comments/--no-comments", help="Download comments."),
    romanise: bool | None = typer.Option(None, "--romanise/--no-romanise", help="Transliterate non-Latin titles and channel names into Latin filenames."),
    sponsorblock: bool = typer.Option(False, "--sponsorblock", help="Remove sponsor and self-promo segments."),
    archive: Path | None = typer.Option(None, "--archive", help="Download-archive file."),
    items: str | None = typer.Option(None, "--items", "-I", help="Playlist items, e.g. 1-5,8."),
    limit: int | None = typer.Option(None, "--limit", help="Stop after N downloads."),
    rate: str | None = typer.Option(None, "--rate", help="Rate limit, e.g. 2M."),
    jobs: int | None = typer.Option(None, "--jobs", "-j", help="Parallel downloads."),
    cookies_browser: str | None = typer.Option(None, "--cookies-from-browser", help="chrome, firefox, …"),
    cookies: Path | None = typer.Option(None, "--cookies", help="Cookies file."),
    set_: list[str] = typer.Option([], "--set", "-s", metavar="KEY=VALUE", help="Override any setting; repeatable."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the resolved yt-dlp command and exit."),
) -> None:
    """Download URLs non-interactively."""
    cfg = _base_config(config_file, profile, preset)

    _flag(cfg, "output_dir", str(output) if output else None)
    _flag(cfg, "output_template", template)
    _flag(cfg, "mode", mode)
    _flag(cfg, "video_quality", quality)
    _flag(cfg, "container", container)
    _flag(cfg, "audio_format", audio_format)
    _flag(cfg, "format_override", fmt)
    _flag(cfg, "subtitles", subs)
    _flag(cfg, "auto_subtitles", subs)
    _flag(cfg, "transcript_txt", transcript_)
    _flag(cfg, "transcript_timestamps", timestamps)
    _flag(cfg, "write_thumbnail", thumbnail)
    _flag(cfg, "write_info_json", info_json)
    _flag(cfg, "write_comments", comments)
    _flag(cfg, "archive_file", str(archive) if archive else None)
    _flag(cfg, "playlist_items", items)
    _flag(cfg, "max_downloads", limit)
    _flag(cfg, "rate_limit", rate)
    _flag(cfg, "parallel_jobs", jobs)
    _flag(cfg, "cookies_from_browser", cookies_browser)
    _flag(cfg, "cookies_file", str(cookies) if cookies else None)
    _flag(cfg, "romanise_filenames", romanise)
    if langs:
        cfg.sub_langs = [p.strip() for p in langs.split(",") if p.strip()]
    if sponsorblock:
        cfg.sponsorblock_remove = ["sponsor", "selfpromo"]

    cfg = _apply_overrides(cfg, set_)

    if mode and mode not in ("video", "audio", "subtitles-only", "metadata-only"):
        raise typer.BadParameter(f"Unknown mode '{mode}'.")

    if dry_run:
        console.print(Syntax(build_command(cfg, list(urls)), "bash", word_wrap=True, theme="ansi_dark"))
        raise typer.Exit()

    raise typer.Exit(run_downloads(list(urls), cfg, console))


@app.command()
def wizard(
    config_file: Path | None = typer.Option(None, "--config", "-c", help="Config file to load."),
    profile: str | None = typer.Option(None, "--profile", "-P", help="Saved profile to load."),
) -> None:
    """Answer a few questions instead of remembering flags."""
    from .wizard import Cancelled, run_wizard

    if not sys.stdin.isatty():
        err.print("[red]The wizard needs an interactive terminal.[/red]")
        raise typer.Exit(2)
    try:
        cfg, urls = run_wizard(_base_config(config_file, profile, None), console)
    except (Cancelled, KeyboardInterrupt):
        console.print("\n[yellow]Cancelled.[/yellow]")
        raise typer.Exit(130) from None
    raise typer.Exit(run_downloads(urls, cfg, console))


@app.command()
def info(
    url: str = typer.Argument(..., help="URL to inspect."),
    as_json: bool = typer.Option(False, "--json", help="Dump the raw info dict."),
    formats: bool = typer.Option(False, "--formats", help="List available media formats."),
    subtitles: bool = typer.Option(False, "--subtitles", help="List available caption tracks."),
    config_file: Path | None = typer.Option(None, "--config", "-c", help="Config file to load."),
) -> None:
    """Show metadata for a URL without downloading anything."""
    from rich.markdown import Markdown

    from .engine import probe
    from .sidecar import summarise

    cfg = load_config(config_file)
    with console.status("Fetching metadata…", spinner="dots"):
        try:
            data = probe(url, cfg, process=formats or subtitles)
        except Exception as exc:  # noqa: BLE001 - extractor errors are user-facing
            err.print(f"[red]Could not fetch metadata:[/red] {escape(str(exc))}")
            raise typer.Exit(1) from exc

    if as_json:
        console.print_json(json.dumps(data, default=str))
        return

    if data.get("_type") == "playlist":
        entries = [e for e in (data.get("entries") or []) if e]
        table = Table(title=data.get("title") or "Playlist", title_style="bold")
        table.add_column("#", style="dim", justify="right")
        table.add_column("Title")
        table.add_column("ID", style="dim")
        for i, entry in enumerate(entries, 1):
            table.add_row(str(i), escape(str(entry.get("title", "?"))[:70]), escape(str(entry.get("id", ""))))
        console.print(table)
        console.print(f"\n[bold]{len(entries)}[/bold] entries")
        return

    console.print(Markdown(summarise(data)))

    if formats:
        table = Table(title="Formats", title_style="bold")
        for column in ("ID", "Ext", "Resolution", "FPS", "Video", "Audio", "Size", "Note"):
            table.add_column(column, overflow="ellipsis")
        for f in data.get("formats") or []:
            size = f.get("filesize") or f.get("filesize_approx")
            table.add_row(
                str(f.get("format_id", "")),
                str(f.get("ext", "")),
                str(f.get("resolution") or f.get("format_note") or ""),
                str(f.get("fps") or ""),
                str(f.get("vcodec") or ""),
                str(f.get("acodec") or ""),
                f"{size / 1_048_576:.1f}M" if isinstance(size, (int, float)) else "",
                str(f.get("format_note") or "")[:24],
            )
        console.print(table)

    if subtitles:
        table = Table(title="Caption tracks", title_style="bold")
        table.add_column("Language")
        table.add_column("Kind")
        table.add_column("Formats")
        for lang, tracks in sorted((data.get("subtitles") or {}).items()):
            table.add_row(lang, "manual", ", ".join(sorted({t.get("ext", "?") for t in tracks})))
        for lang, tracks in sorted((data.get("automatic_captions") or {}).items()):
            table.add_row(lang, "[dim]auto[/dim]", ", ".join(sorted({t.get("ext", "?") for t in tracks})))
        console.print(table)


@app.command()
def transcript(
    urls: list[str] = typer.Argument(..., help="URLs to transcribe."),
    langs: str = typer.Option("en", "--langs", "-l", help="Languages, comma separated, or 'all'."),
    timestamps: bool = typer.Option(False, "--timestamps", help="Prefix lines with [hh:mm:ss]."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output directory."),
    keep_captions: bool = typer.Option(True, "--keep-captions/--no-keep-captions", help="Keep the .srt files."),
    config_file: Path | None = typer.Option(None, "--config", "-c", help="Config file to load."),
) -> None:
    """Fetch captions and write plain-text transcripts — no media downloaded."""
    cfg = apply_preset(load_config(config_file), "transcript")
    cfg.sub_langs = [p.strip() for p in langs.split(",") if p.strip()] or ["en"]
    cfg.transcript_timestamps = timestamps
    if output:
        cfg.output_dir = str(output)
    if not keep_captions:
        cfg.exec_cmd = ""  # captions are removed after conversion below
    raise typer.Exit(run_downloads(list(urls), cfg, console))


@app.command()
def convert(
    files: list[Path] = typer.Argument(..., help="Caption files (.vtt/.srt/.ass) to convert."),
    timestamps: bool = typer.Option(False, "--timestamps", help="Prefix lines with [hh:mm:ss]."),
    stdout: bool = typer.Option(False, "--stdout", help="Print instead of writing .txt files."),
) -> None:
    """Convert caption files you already have into plain-text transcripts."""
    failures = 0
    for path in files:
        if not path.is_file():
            err.print(f"[red]Not found:[/red] {escape(str(path))}")
            failures += 1
            continue
        if stdout:
            cues = transcripts.parse_cues(
                path.read_text(encoding="utf-8", errors="replace"), path.suffix.lower()
            )
            console.print(transcripts.render(cues, timestamps=timestamps), markup=False, highlight=False)
            continue
        written = transcripts.convert_file(path, timestamps=timestamps)
        if written:
            console.print(f"[green]✓[/green] {escape(str(written))}")
        else:
            err.print(f"[yellow]![/yellow] no usable cues in {escape(str(path))}")
            failures += 1
    raise typer.Exit(1 if failures else 0)


@app.command()
def presets() -> None:
    """List the built-in presets."""
    table = Table(title="Presets", title_style="bold")
    table.add_column("Key", style="cyan bold")
    table.add_column("Name")
    table.add_column("Description", overflow="fold")
    for preset in PRESETS:
        table.add_row(preset.key, preset.label, preset.description)
    console.print(table)


@app.command("config")
def config_cmd(
    show: bool = typer.Option(False, "--show", help="Print the effective configuration."),
    path: bool = typer.Option(False, "--path", help="Print the config file location."),
    init: bool = typer.Option(False, "--init", help="Write a fully-commented config file."),
    edit: bool = typer.Option(False, "--edit", help="Open the config in $EDITOR."),
    config_file: Path | None = typer.Option(None, "--config", "-c", help="Config file to act on."),
) -> None:
    """Inspect or create the configuration file."""
    target = config_file or CONFIG_PATH

    if path or not (show or init or edit):
        console.print(escape(str(target)))
        if not target.exists():
            console.print("[dim](does not exist yet — run `ytkit config --init`)[/dim]")
        if not (show or init or edit):
            return

    if init:
        written = save_config(load_config(config_file), target, full=True)
        console.print(f"[green]✓[/green] wrote {escape(str(written))}")

    if edit:
        import os
        import subprocess

        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            save_config(Config(), target, full=True)
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        subprocess.call([editor, str(target)])

    if show:
        cfg = load_config(config_file)
        table = Table(title="Effective configuration", title_style="bold")
        table.add_column("Section", style="dim")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", overflow="fold")
        for section, fields_ in Config.sections().items():
            for f in fields_:
                value = escape(cfg.display(f.name))
                changed = getattr(cfg, f.name) != Config.default_for(f.name)
                table.add_row(section, f.name, f"[bold]{value}[/bold]" if changed else value)
        console.print(table)


@profile_app.command("list")
def profile_list() -> None:
    """List saved profiles."""
    names = list_profiles()
    if not names:
        console.print("[dim]No profiles saved yet.[/dim]")
        return
    for name in names:
        console.print(f"  [cyan]{escape(name)}[/cyan]")


@profile_app.command("save")
def profile_save(
    name: str = typer.Argument(..., help="Profile name."),
    from_config: Path | None = typer.Option(None, "--from", help="Config file to snapshot."),
    preset: str | None = typer.Option(None, "--preset", "-p", help="Base the profile on a preset."),
    set_: list[str] = typer.Option([], "--set", "-s", metavar="KEY=VALUE", help="Override a setting."),
) -> None:
    """Save the current (or preset-derived) settings as a named profile."""
    cfg = _apply_overrides(_base_config(from_config, None, preset), set_)
    console.print(f"[green]✓[/green] saved {escape(str(save_profile(name, cfg)))}")


@profile_app.command("show")
def profile_show(name: str = typer.Argument(..., help="Profile name.")) -> None:
    """Show the yt-dlp command a profile produces."""
    try:
        cfg = load_profile(name)
    except FileNotFoundError as exc:
        err.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc
    console.print(Syntax(build_command(cfg, ["<URL>"]), "bash", word_wrap=True, theme="ansi_dark"))


@profile_app.command("delete")
def profile_delete(name: str = typer.Argument(..., help="Profile name.")) -> None:
    """Delete a saved profile."""
    if delete_profile(name):
        console.print(f"[green]✓[/green] deleted profile '{escape(name)}'")
    else:
        err.print(f"[red]No such profile:[/red] {escape(name)}")
        raise typer.Exit(1)


def entrypoint() -> None:
    app()
