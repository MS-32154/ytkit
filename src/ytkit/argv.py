"""Translate a :class:`Config` into yt-dlp command-line arguments.

Going through the real CLI parser (rather than hand-assembling ``ydl_opts``)
means postprocessor wiring and ordering — SponsorBlock before extraction,
metadata before thumbnail embedding — is handled by yt-dlp itself, and the
exact equivalent command can always be shown to the user.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from .config import Config

CODEC_SORT = {
    "h264": "vcodec:h264",
    "h265": "vcodec:h265",
    "vp9": "vcodec:vp9",
    "av1": "vcodec:av01",
}

EXT_SORT = {"mp4": "ext:mp4:m4a", "webm": "ext:webm:webm", "mov": "ext:mov:m4a"}


def _expand(value: str) -> str:
    return str(Path(value).expanduser())


def format_selector(cfg: Config) -> str:
    """Build the -f expression from the quality/fps pickers."""
    if cfg.format_override.strip():
        return cfg.format_override.strip()

    if cfg.mode == "audio":
        return "bestaudio/best"

    filters = ""
    if cfg.video_quality not in ("best", "worst"):
        filters += f"[height<={cfg.video_quality}]"
    if cfg.max_fps > 0:
        filters += f"[fps<={cfg.max_fps}]"

    if cfg.video_quality == "worst":
        return "wv*+wa/w"
    if not filters:
        return "bv*+ba/b"
    # Progressively relax: merged filtered -> single filtered -> anything.
    return f"bv*{filters}+ba/b{filters}/bv*+ba/b"


def sort_expression(cfg: Config) -> str:
    parts: list[str] = []
    if cfg.video_codec != "any" and cfg.mode != "audio":
        parts.append(CODEC_SORT[cfg.video_codec])
    if cfg.container in EXT_SORT and cfg.mode != "audio":
        parts.append(EXT_SORT[cfg.container])
    if cfg.format_sort.strip():
        parts.append(cfg.format_sort.strip())
    return ",".join(parts)


def _extractor_args(cfg: Config) -> list[str]:
    """Merge the user's extractor args with our comment cap, avoiding clobber."""
    user = cfg.extractor_args.strip()
    cap = cfg.max_comments > 0 and cfg.write_comments
    if not cap:
        return ["--extractor-args", user] if user else []
    if user.startswith("youtube:"):
        return ["--extractor-args", f"{user};max_comments={cfg.max_comments}"]
    args = ["--extractor-args", f"youtube:max_comments={cfg.max_comments}"]
    if user:
        args += ["--extractor-args", user]
    return args


def build_argv(cfg: Config, urls: list[str] | None = None) -> list[str]:
    """Full argument vector, excluding the ``yt-dlp`` program name."""
    a: list[str] = []
    skip_media = cfg.mode in ("subtitles-only", "metadata-only")

    # -- output ---------------------------------------------------------------
    a += ["--paths", f"home:{_expand(cfg.output_dir)}"]
    if cfg.temp_dir.strip():
        a += ["--paths", f"temp:{_expand(cfg.temp_dir)}"]
    a += ["--output", cfg.output_template]
    if cfg.archive_file.strip():
        a += ["--download-archive", _expand(cfg.archive_file)]
    if cfg.restrict_filenames:
        a.append("--restrict-filenames")
    if cfg.windows_filenames:
        a.append("--windows-filenames")
    if cfg.trim_filenames > 0:
        a += ["--trim-filenames", str(cfg.trim_filenames)]
    a.append("--no-overwrites" if cfg.no_overwrites else "--force-overwrites")
    a.append("--continue" if cfg.continue_partial else "--no-continue")
    if cfg.write_url_link:
        a.append("--write-url-link")

    # -- format ---------------------------------------------------------------
    if skip_media:
        a.append("--skip-download")
    elif cfg.mode == "audio":
        a += ["--format", format_selector(cfg), "--extract-audio"]
        if cfg.audio_format != "best":
            a += ["--audio-format", cfg.audio_format]
        if cfg.audio_quality.strip():
            a += ["--audio-quality", cfg.audio_quality.strip()]
        if cfg.keep_video:
            a.append("--keep-video")
    else:
        a += ["--format", format_selector(cfg)]
        if cfg.container != "auto":
            a += ["--merge-output-format", cfg.container]
        if cfg.remux_to:
            a += ["--remux-video", cfg.remux_to]
        if cfg.recode_to:
            a += ["--recode-video", cfg.recode_to]

    sort = sort_expression(cfg)
    if sort:
        a += ["--format-sort", sort]
    if cfg.prefer_free_formats:
        a.append("--prefer-free-formats")

    # -- subtitles ------------------------------------------------------------
    if cfg.subtitles or cfg.auto_subtitles or cfg.mode == "subtitles-only":
        if cfg.subtitles or cfg.mode == "subtitles-only":
            a.append("--write-subs")
        if cfg.auto_subtitles:
            a.append("--write-auto-subs")
        langs = ",".join(cfg.sub_langs) or "en"
        a += ["--sub-langs", langs]
        if cfg.sub_format not in ("", "best"):
            # --sub-format picks what to fetch; conversion guarantees the result.
            a += ["--convert-subs", cfg.sub_format]
        if cfg.embed_subs and not skip_media:
            a.append("--embed-subs")

    # -- metadata -------------------------------------------------------------
    if cfg.write_info_json:
        a.append("--write-info-json")
    if cfg.write_description:
        a.append("--write-description")
    if cfg.write_comments:
        a.append("--write-comments")
    if cfg.write_thumbnail:
        a.append("--write-thumbnail")
    if cfg.convert_thumbnail:
        a += ["--convert-thumbnails", cfg.convert_thumbnail]
    if cfg.embed_thumbnail and not skip_media:
        a.append("--embed-thumbnail")
    if cfg.embed_metadata and not skip_media:
        a.append("--embed-metadata")
    if not skip_media:
        a.append("--embed-chapters" if cfg.embed_chapters else "--no-embed-chapters")

    # -- chapters & sections --------------------------------------------------
    if cfg.split_chapters and not skip_media:
        a.append("--split-chapters")
    for section in (s.strip() for s in cfg.download_sections.split(";")):
        if section:
            a += ["--download-sections", section]
    if cfg.force_keyframes:
        a.append("--force-keyframes-at-cuts")
    if cfg.sponsorblock_mark:
        a += ["--sponsorblock-mark", ",".join(cfg.sponsorblock_mark)]
    if cfg.sponsorblock_remove:
        a += ["--sponsorblock-remove", ",".join(cfg.sponsorblock_remove)]

    # -- playlist & filtering -------------------------------------------------
    if cfg.playlist_handling == "always":
        a.append("--yes-playlist")
    elif cfg.playlist_handling == "never":
        a.append("--no-playlist")
    if cfg.playlist_items.strip():
        a += ["--playlist-items", cfg.playlist_items.strip()]
    if cfg.playlist_reverse:
        a.append("--playlist-reverse")
    if cfg.max_downloads > 0:
        a += ["--max-downloads", str(cfg.max_downloads)]
    if cfg.date_after.strip():
        a += ["--dateafter", cfg.date_after.strip()]
    if cfg.date_before.strip():
        a += ["--datebefore", cfg.date_before.strip()]
    if cfg.match_filter.strip():
        a += ["--match-filters", cfg.match_filter.strip()]
    if cfg.break_on_existing:
        a.append("--break-on-existing")

    # -- network --------------------------------------------------------------
    if cfg.concurrent_fragments > 1:
        a += ["--concurrent-fragments", str(cfg.concurrent_fragments)]
    if cfg.rate_limit.strip():
        a += ["--limit-rate", cfg.rate_limit.strip()]
    a += ["--retries", str(cfg.retries), "--fragment-retries", str(cfg.fragment_retries)]
    if cfg.socket_timeout > 0:
        a += ["--socket-timeout", str(cfg.socket_timeout)]
    if cfg.sleep_interval > 0:
        a += ["--sleep-interval", str(cfg.sleep_interval)]
    if cfg.max_sleep_interval > 0:
        a += ["--max-sleep-interval", str(cfg.max_sleep_interval)]
    if cfg.proxy.strip():
        a += ["--proxy", cfg.proxy.strip()]
    if cfg.source_address.strip():
        a += ["--source-address", cfg.source_address.strip()]
    if cfg.force_ip == "ipv4":
        a.append("--force-ipv4")
    elif cfg.force_ip == "ipv6":
        a.append("--force-ipv6")
    if cfg.geo_bypass_country.strip():
        a += ["--geo-bypass-country", cfg.geo_bypass_country.strip()]
    if cfg.impersonate.strip():
        a += ["--impersonate", cfg.impersonate.strip()]
    a += _extractor_args(cfg)

    # -- authentication -------------------------------------------------------
    if cfg.cookies_file.strip():
        a += ["--cookies", _expand(cfg.cookies_file)]
    if cfg.cookies_from_browser:
        a += ["--cookies-from-browser", cfg.cookies_from_browser]
    if cfg.use_netrc:
        a.append("--netrc")

    # -- behaviour ------------------------------------------------------------
    a.append("--ignore-errors" if cfg.ignore_errors else "--abort-on-error")
    if cfg.live_from_start:
        a.append("--live-from-start")
    if cfg.ffmpeg_location.strip():
        a += ["--ffmpeg-location", _expand(cfg.ffmpeg_location)]
    if cfg.exec_cmd.strip():
        a += ["--exec", cfg.exec_cmd.strip()]
    if cfg.verbose:
        a.append("--verbose")
    if cfg.extra_args.strip():
        a += shlex.split(cfg.extra_args)

    a += list(urls or [])
    return a


def build_command(cfg: Config, urls: list[str] | None = None) -> str:
    """The equivalent shell command, for display and copy-paste."""
    return "yt-dlp " + " ".join(shlex.quote(x) for x in build_argv(cfg, urls))
