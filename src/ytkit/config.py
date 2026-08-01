"""Configuration model.

Every tunable lives in :class:`Config` as a dataclass field carrying UI metadata,
so the settings screen, the guided wizard and the TOML round-trip are all
generated from one definition. Add a field here and it shows up everywhere.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import platformdirs

APP_NAME = "ytkit"

CONFIG_DIR = Path(platformdirs.user_config_dir(APP_NAME))
CONFIG_PATH = CONFIG_DIR / "config.toml"
PROFILE_DIR = CONFIG_DIR / "profiles"

# --- field kinds -------------------------------------------------------------
# bool   -> Switch          int    -> numeric Input
# text   -> Input           path   -> Input (expanded to Path)
# choice -> Select          list   -> comma-separated Input


def opt(
    default: Any,
    *,
    label: str,
    section: str,
    kind: str = "text",
    choices: tuple[str, ...] = (),
    help: str = "",
    placeholder: str = "",
) -> Any:
    """Declare a config field plus the metadata the UI needs to render it."""
    meta = {
        "label": label,
        "section": section,
        "kind": kind,
        "choices": choices,
        "help": help,
        "placeholder": placeholder,
    }
    if isinstance(default, list):
        return field(default_factory=lambda: list(default), metadata=meta)
    return field(default=default, metadata=meta)


TRUE_WORDS = {"1", "true", "yes", "on", "y", "t"}
FALSE_WORDS = {"0", "false", "no", "off", "n", "f", ""}


def parse_bool(raw: object) -> bool | None:
    """Parse a boolean from a switch, a TOML value or a `--set` string.

    Not bool(raw): every non-empty string is truthy, so `--set subs=false`
    would silently switch the option *on*. Returns None for anything
    unrecognised so the caller can reject it instead of guessing.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return None


OUTPUT = "Output"
FORMAT = "Video & Format"
AUDIO = "Audio"
SUBS = "Subtitles & Transcripts"
META = "Metadata"
SECTIONS = "Chapters & Sections"
PLAYLIST = "Playlist & Filtering"
NETWORK = "Network"
AUTH = "Authentication"
BEHAVIOR = "Behaviour"

MODES = ("video", "audio", "subtitles-only", "metadata-only")


@dataclass
class Config:
    # -- Output ---------------------------------------------------------------
    output_dir: str = opt(
        str(Path.home() / "Downloads" / "ytkit"),
        label="Output directory",
        section=OUTPUT,
        kind="path",
        help="Root folder for all downloads.",
    )
    output_template: str = opt(
        "%(uploader)s/%(title)s [%(id)s].%(ext)s",
        label="Output template",
        section=OUTPUT,
        help="yt-dlp output template, relative to the output directory.",
    )
    temp_dir: str = opt(
        "",
        label="Temporary directory",
        section=OUTPUT,
        kind="path",
        help="Where partial files are staged. Empty = alongside the output.",
    )
    archive_file: str = opt(
        "",
        label="Download archive",
        section=OUTPUT,
        kind="path",
        help="File recording completed IDs so they are never fetched twice.",
    )
    romanise_filenames: bool = opt(
        False,
        label="Romanise non-Latin names",
        section=OUTPUT,
        kind="bool",
        help="Transliterate Arabic/Cyrillic/CJK titles and channel names into "
             "Latin so they are navigable in a terminal. The originals are kept "
             "in the .summary.md and .info.json sidecars.",
    )
    restrict_filenames: bool = opt(
        False,
        label="Restrict filenames to ASCII",
        section=OUTPUT,
        kind="bool",
        help="yt-dlp's own stripping. On its own it erases non-Latin titles "
             "entirely — pair it with romanising, not instead of it.",
    )
    windows_filenames: bool = opt(
        False,
        label="Windows-safe filenames",
        section=OUTPUT,
        kind="bool",
    )
    trim_filenames: int = opt(
        0, label="Trim filenames to N chars", section=OUTPUT, kind="int",
        help="0 disables trimming.",
    )
    no_overwrites: bool = opt(
        True, label="Never overwrite existing files", section=OUTPUT, kind="bool"
    )
    continue_partial: bool = opt(
        True, label="Resume partial downloads", section=OUTPUT, kind="bool"
    )
    write_url_link: bool = opt(
        False, label="Write .url shortcut file", section=OUTPUT, kind="bool"
    )

    # -- Video & format -------------------------------------------------------
    mode: str = opt(
        "video",
        label="Download mode",
        section=FORMAT,
        kind="choice",
        choices=MODES,
        help="What to fetch. 'metadata-only' downloads no media at all.",
    )
    video_quality: str = opt(
        "best",
        label="Max video quality",
        section=FORMAT,
        kind="choice",
        choices=("best", "2160", "1440", "1080", "720", "480", "360", "240", "worst"),
    )
    video_codec: str = opt(
        "any",
        label="Preferred video codec",
        section=FORMAT,
        kind="choice",
        choices=("any", "h264", "h265", "vp9", "av1"),
        help="A preference, not a hard filter — falls back if unavailable.",
    )
    container: str = opt(
        "auto",
        label="Merge container",
        section=FORMAT,
        kind="choice",
        choices=("auto", "mp4", "mkv", "webm", "mov", "avi"),
    )
    max_fps: int = opt(0, label="Max FPS", section=FORMAT, kind="int", help="0 = unlimited.")
    prefer_free_formats: bool = opt(
        False, label="Prefer free (non-patented) formats", section=FORMAT, kind="bool"
    )
    format_override: str = opt(
        "",
        label="Raw format selector",
        section=FORMAT,
        placeholder="bv*[height<=1080]+ba/b",
        help="Overrides the quality/codec pickers entirely (yt-dlp -f syntax).",
    )
    format_sort: str = opt(
        "",
        label="Extra format sort",
        section=FORMAT,
        placeholder="res,fps,codec:av01",
        help="Appended to the computed sort order (yt-dlp -S syntax).",
    )
    remux_to: str = opt(
        "",
        label="Remux to container",
        section=FORMAT,
        kind="choice",
        choices=("", "mp4", "mkv", "webm", "mov", "avi", "flv"),
        help="Lossless container change after download.",
    )
    recode_to: str = opt(
        "",
        label="Re-encode to container",
        section=FORMAT,
        kind="choice",
        choices=("", "mp4", "mkv", "webm", "mov", "avi"),
        help="Lossy full re-encode. Slow — prefer remux.",
    )

    # -- Audio ----------------------------------------------------------------
    audio_format: str = opt(
        "mp3",
        label="Audio format",
        section=AUDIO,
        kind="choice",
        choices=("best", "mp3", "m4a", "opus", "flac", "wav", "vorbis", "aac", "alac"),
        help="Used when mode is 'audio'.",
    )
    audio_quality: str = opt(
        "0",
        label="Audio quality",
        section=AUDIO,
        placeholder="0-10 (VBR) or 192K",
        help="0 is best VBR; or give an explicit bitrate such as 192K.",
    )
    keep_video: bool = opt(
        False,
        label="Keep video after audio extraction",
        section=AUDIO,
        kind="bool",
    )

    # -- Subtitles ------------------------------------------------------------
    subtitles: bool = opt(True, label="Download subtitles", section=SUBS, kind="bool")
    auto_subtitles: bool = opt(
        True,
        label="Include auto-generated captions",
        section=SUBS,
        kind="bool",
        help="YouTube's ASR track — the fallback when no human captions exist.",
    )
    sub_langs: list[str] = opt(
        ["en"],
        label="Subtitle languages",
        section=SUBS,
        kind="list",
        placeholder="en,es,fr  or  all",
        help="Comma separated. 'all' grabs every available language.",
    )
    sub_format: str = opt(
        "srt",
        label="Subtitle format",
        section=SUBS,
        kind="choice",
        choices=("srt", "vtt", "ass", "lrc", "best"),
    )
    embed_subs: bool = opt(
        False, label="Embed subtitles into the video", section=SUBS, kind="bool"
    )
    transcript_txt: bool = opt(
        True,
        label="Write plain-text transcript",
        section=SUBS,
        kind="bool",
        help="Converts captions to readable prose with the timing noise stripped.",
    )
    transcript_timestamps: bool = opt(
        False,
        label="Timestamps in transcript",
        section=SUBS,
        kind="bool",
        help="Prefix each line with [hh:mm:ss].",
    )

    # -- Metadata -------------------------------------------------------------
    write_info_json: bool = opt(
        True, label="Write .info.json", section=META, kind="bool",
        help="The complete metadata record yt-dlp extracted.",
    )
    write_description: bool = opt(True, label="Write description file", section=META, kind="bool")
    write_comments: bool = opt(
        False, label="Download comments", section=META, kind="bool",
        help="Slow on popular videos. Stored inside the .info.json.",
    )
    max_comments: int = opt(
        0, label="Max comments", section=META, kind="int", help="0 = no limit."
    )
    write_thumbnail: bool = opt(True, label="Write thumbnail", section=META, kind="bool")
    convert_thumbnail: str = opt(
        "",
        label="Convert thumbnail to",
        section=META,
        kind="choice",
        choices=("", "jpg", "png", "webp"),
    )
    embed_thumbnail: bool = opt(False, label="Embed thumbnail as cover art", section=META, kind="bool")
    embed_metadata: bool = opt(True, label="Embed metadata tags", section=META, kind="bool")
    embed_chapters: bool = opt(True, label="Embed chapter markers", section=META, kind="bool")
    write_summary: bool = opt(
        True,
        label="Write summary sidecar",
        section=META,
        kind="bool",
        help="A compact human-readable .summary.md next to each download.",
    )

    # -- Chapters & sections --------------------------------------------------
    split_chapters: bool = opt(
        False, label="Split file by chapters", section=SECTIONS, kind="bool"
    )
    download_sections: str = opt(
        "",
        label="Download only sections",
        section=SECTIONS,
        placeholder="*10:00-15:00",
        help="Time ranges or a chapter regex. Repeatable via ';'.",
    )
    force_keyframes: bool = opt(
        False,
        label="Force keyframes at cuts",
        section=SECTIONS,
        kind="bool",
        help="Slower but avoids glitched section boundaries.",
    )
    sponsorblock_mark: list[str] = opt(
        [],
        label="SponsorBlock: mark as chapters",
        section=SECTIONS,
        kind="list",
        placeholder="sponsor,intro,outro  or  all",
    )
    sponsorblock_remove: list[str] = opt(
        [],
        label="SponsorBlock: cut out",
        section=SECTIONS,
        kind="list",
        placeholder="sponsor,selfpromo",
    )

    # -- Playlist & filtering -------------------------------------------------
    playlist_handling: str = opt(
        "auto",
        label="Playlist handling",
        section=PLAYLIST,
        kind="choice",
        choices=("auto", "always", "never"),
        help="For a video URL that also carries a playlist ID.",
    )
    playlist_items: str = opt(
        "",
        label="Playlist items",
        section=PLAYLIST,
        placeholder="1-5,8,12-",
    )
    playlist_reverse: bool = opt(False, label="Reverse playlist order", section=PLAYLIST, kind="bool")
    max_downloads: int = opt(0, label="Stop after N downloads", section=PLAYLIST, kind="int")
    date_after: str = opt("", label="Uploaded after", section=PLAYLIST, placeholder="20240101 or today-1month")
    date_before: str = opt("", label="Uploaded before", section=PLAYLIST, placeholder="20241231")
    match_filter: str = opt(
        "",
        label="Match filter",
        section=PLAYLIST,
        placeholder="duration>60 & view_count>1000",
    )
    break_on_existing: bool = opt(
        False,
        label="Stop at first archived video",
        section=PLAYLIST,
        kind="bool",
        help="Useful for incrementally syncing a channel.",
    )

    # -- Network --------------------------------------------------------------
    concurrent_fragments: int = opt(4, label="Concurrent fragments", section=NETWORK, kind="int")
    parallel_jobs: int = opt(
        2,
        label="Parallel downloads",
        section=NETWORK,
        kind="int",
        help="How many queue entries ytkit works on at once.",
    )
    rate_limit: str = opt("", label="Rate limit", section=NETWORK, placeholder="2M")
    retries: int = opt(10, label="Retries", section=NETWORK, kind="int")
    fragment_retries: int = opt(10, label="Fragment retries", section=NETWORK, kind="int")
    socket_timeout: int = opt(0, label="Socket timeout (s)", section=NETWORK, kind="int")
    sleep_interval: float = opt(0.0, label="Sleep between downloads (s)", section=NETWORK)
    max_sleep_interval: float = opt(0.0, label="Max sleep (s)", section=NETWORK)
    proxy: str = opt("", label="Proxy", section=NETWORK, placeholder="socks5://127.0.0.1:1080")
    source_address: str = opt("", label="Bind source address", section=NETWORK)
    force_ip: str = opt(
        "auto", label="IP version", section=NETWORK, kind="choice", choices=("auto", "ipv4", "ipv6")
    )
    geo_bypass_country: str = opt("", label="Geo-bypass country", section=NETWORK, placeholder="US")
    impersonate: str = opt(
        "",
        label="Impersonate client",
        section=NETWORK,
        placeholder="chrome",
        help="TLS fingerprint impersonation; helps with some blocks.",
    )
    extractor_args: str = opt(
        "",
        label="Extractor arguments",
        section=NETWORK,
        placeholder="youtube:player_client=web,android",
    )

    # -- Authentication -------------------------------------------------------
    cookies_file: str = opt("", label="Cookies file", section=AUTH, kind="path")
    cookies_from_browser: str = opt(
        "",
        label="Cookies from browser",
        section=AUTH,
        kind="choice",
        choices=("", "chrome", "chromium", "firefox", "edge", "brave", "opera", "safari", "vivaldi"),
        help="Reads your logged-in session directly. Needed for private content.",
    )
    use_netrc: bool = opt(False, label="Use .netrc credentials", section=AUTH, kind="bool")

    # -- Behaviour ------------------------------------------------------------
    ignore_errors: bool = opt(
        True, label="Continue past failed items", section=BEHAVIOR, kind="bool"
    )
    live_from_start: bool = opt(False, label="Download livestreams from start", section=BEHAVIOR, kind="bool")
    ffmpeg_location: str = opt("", label="ffmpeg location", section=BEHAVIOR, kind="path")
    exec_cmd: str = opt(
        "",
        label="Run command after each download",
        section=BEHAVIOR,
        placeholder="echo {}",
        help="'{}' is replaced with the final filepath.",
    )
    verbose: bool = opt(False, label="Verbose engine log", section=BEHAVIOR, kind="bool")
    extra_args: str = opt(
        "",
        label="Extra yt-dlp arguments",
        section=BEHAVIOR,
        placeholder="--no-check-certificates",
        help="Appended verbatim. The escape hatch for anything not exposed above.",
    )

    # -- helpers --------------------------------------------------------------

    @classmethod
    def spec(cls) -> Iterator[tuple[str, dataclasses.Field]]:
        for f in fields(cls):
            yield f.name, f

    @classmethod
    def sections(cls) -> dict[str, list[dataclasses.Field]]:
        out: dict[str, list[dataclasses.Field]] = {}
        for f in fields(cls):
            out.setdefault(f.metadata["section"], []).append(f)
        return out

    @classmethod
    def default_for(cls, name: str) -> Any:
        f = {x.name: x for x in fields(cls)}[name]
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            return f.default_factory()  # type: ignore[misc]
        return f.default

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    def copy(self) -> Config:
        # deepcopy, not dataclasses.replace: replace() shallow-copies, so every
        # job would share the same sub_langs / sponsorblock list objects.
        return copy.deepcopy(self)

    def diff_from_defaults(self) -> dict[str, Any]:
        return {
            k: v for k, v in self.to_dict().items() if v != Config.default_for(k)
        }

    def set_from_string(self, name: str, raw: str | bool) -> bool:
        """Coerce a UI string into the field's declared type.

        Returns False and leaves the value untouched if the input doesn't
        parse, so a half-typed number in the settings form is harmless.
        """
        f = {x.name: x for x in fields(self)}[name]
        kind = f.metadata["kind"]
        try:
            if kind == "bool":
                parsed = parse_bool(raw)
                if parsed is None:
                    return False
                value: Any = parsed
            elif kind == "int":
                value = int(str(raw).strip()) if str(raw).strip() else 0
            elif kind == "list":
                value = [p.strip() for p in str(raw).split(",") if p.strip()]
            elif isinstance(Config.default_for(name), float):
                value = float(str(raw).strip()) if str(raw).strip() else 0.0
            else:
                value = str(raw)
        except (TypeError, ValueError):
            return False
        setattr(self, name, value)
        return True

    def display(self, name: str) -> str:
        v = getattr(self, name)
        if isinstance(v, list):
            return ",".join(v)
        return str(v)
