"""Every config the UI can produce must survive yt-dlp's own option parser."""

from __future__ import annotations

import pytest
import yt_dlp

from ytkit.argv import build_argv, build_command, format_selector, sort_expression
from ytkit.config import Config
from ytkit.engine import build_ydl_opts
from ytkit.presets import PRESETS

URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"


def parse(cfg: Config) -> dict:
    return build_ydl_opts(cfg, [URL])


def test_defaults_parse():
    opts = parse(Config())
    assert opts["format"] == "bv*+ba/b"
    assert opts["writesubtitles"] is True


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
def test_every_preset_parses(preset):
    cfg = preset.apply(Config())
    opts = parse(cfg)
    assert isinstance(opts, dict)
    # A preset must never produce contradictory media instructions.
    if cfg.mode in ("subtitles-only", "metadata-only"):
        assert opts.get("skip_download") is True
    else:
        assert not opts.get("skip_download")


@pytest.mark.parametrize(
    "field,value",
    [
        ("video_quality", "720"),
        ("video_quality", "worst"),
        ("video_codec", "av1"),
        ("video_codec", "h265"),
        ("container", "mkv"),
        ("container", "webm"),
        ("max_fps", 30),
        ("remux_to", "mkv"),
        ("recode_to", "mp4"),
        ("split_chapters", True),
        ("download_sections", "*10:00-15:00"),
        ("force_keyframes", True),
        ("sponsorblock_remove", ["sponsor", "selfpromo"]),
        ("sponsorblock_mark", ["all"]),
        ("playlist_items", "1-5,8"),
        ("playlist_reverse", True),
        ("max_downloads", 3),
        ("date_after", "20240101"),
        ("match_filter", "duration > 60"),
        ("break_on_existing", True),
        ("rate_limit", "2M"),
        ("proxy", "socks5://127.0.0.1:1080"),
        ("force_ip", "ipv4"),
        ("geo_bypass_country", "US"),
        ("extractor_args", "youtube:player_client=web"),
        ("cookies_from_browser", "firefox"),
        ("use_netrc", True),
        ("restrict_filenames", True),
        ("trim_filenames", 80),
        ("sub_format", "vtt"),
        ("sub_langs", ["all"]),
        ("embed_subs", True),
        ("convert_thumbnail", "jpg"),
        ("live_from_start", True),
        ("exec_cmd", "echo {}"),
        ("extra_args", "--no-check-certificates --no-part"),
        ("socket_timeout", 30),
        ("sleep_interval", 1.5),
        ("format_override", "bv*[height<=480]+ba"),
        ("format_sort", "res,fps"),
        ("prefer_free_formats", True),
        ("archive_file", "/tmp/archive.txt"),
        ("temp_dir", "/tmp/ytkit-temp"),
    ],
)
def test_individual_options_parse(field, value):
    cfg = Config()
    setattr(cfg, field, value)
    assert isinstance(parse(cfg), dict)


def test_every_field_toggled_still_parses():
    """Flip every boolean on, then every boolean off — both must parse."""
    for state in (True, False):
        cfg = Config()
        for name, f in Config.spec():
            if f.metadata["kind"] == "bool":
                setattr(cfg, name, state)
        assert isinstance(parse(cfg), dict)


def test_audio_mode_builds_extraction_postprocessor():
    cfg = Config(mode="audio", audio_format="opus", audio_quality="0")
    keys = [p["key"] for p in parse(cfg)["postprocessors"]]
    assert "FFmpegExtractAudio" in keys


def test_sponsorblock_ordering():
    """SponsorBlock must run before ModifyChapters, or cuts are not applied."""
    cfg = Config(sponsorblock_remove=["sponsor"])
    keys = [p["key"] for p in parse(cfg)["postprocessors"]]
    assert keys.index("SponsorBlock") < keys.index("ModifyChapters")


def test_format_selector_relaxes_progressively():
    cfg = Config(video_quality="1080", max_fps=60)
    selector = format_selector(cfg)
    assert selector.startswith("bv*[height<=1080][fps<=60]+ba")
    assert selector.endswith("/bv*+ba/b")  # unconstrained fallback


def test_format_override_wins():
    cfg = Config(video_quality="360", format_override="bestvideo+bestaudio")
    assert format_selector(cfg) == "bestvideo+bestaudio"


def test_sort_expression_combines_codec_container_and_user_sort():
    cfg = Config(video_codec="av1", container="mp4", format_sort="res")
    assert sort_expression(cfg) == "vcodec:av01,ext:mp4:m4a,res"


def test_max_comments_merges_into_user_extractor_args():
    cfg = Config(write_comments=True, max_comments=50, extractor_args="youtube:player_client=web")
    argv = build_argv(cfg)
    idx = argv.index("--extractor-args")
    assert argv[idx + 1] == "youtube:player_client=web;max_comments=50"
    assert argv.count("--extractor-args") == 1  # one entry per extractor, not two


def test_max_comments_without_user_args():
    cfg = Config(write_comments=True, max_comments=50)
    argv = build_argv(cfg)
    assert argv[argv.index("--extractor-args") + 1] == "youtube:max_comments=50"


def test_metadata_only_skips_media_but_keeps_sidecars():
    opts = parse(Config(mode="metadata-only"))
    assert opts["skip_download"] is True
    assert opts["writeinfojson"] is True


def test_multiple_download_sections():
    argv = build_argv(Config(download_sections="*0-10;*30-40"))
    assert argv.count("--download-sections") == 2


def test_build_command_is_shell_quoted():
    cfg = Config(output_template="%(title)s [%(id)s].%(ext)s")
    assert "'%(title)s [%(id)s].%(ext)s'" in build_command(cfg, [URL])


def test_urls_land_last():
    assert build_argv(Config(), [URL])[-1] == URL


def test_no_unknown_options_reach_ytdl():
    """Guards against a typo'd flag silently disabling everything after it."""
    cfg = PRESETS[1].apply(Config())  # archival: the widest surface
    parser = yt_dlp.parse_options(build_argv(cfg, [URL]))
    assert parser.urls == [URL]
