from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ytkit.cli import app
from ytkit.config import Config

runner = CliRunner()

URL = "https://youtu.be/abc123"
VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello world\n"


def run(*args, **kwargs):
    return runner.invoke(app, list(args), **kwargs)


def out(result) -> str:
    """Collapse Rich's line wrapping so assertions aren't width-sensitive."""
    return " ".join(result.output.split())


def test_version():
    result = run("--version")
    assert result.exit_code == 0
    assert "ytkit" in result.output


def test_presets_listing():
    result = run("presets")
    assert result.exit_code == 0
    for key in ("balanced", "archival", "audio", "transcript", "metadata"):
        assert key in out(result)


def test_dry_run_prints_the_command_without_downloading():
    result = run("get", URL, "--dry-run")
    assert result.exit_code == 0
    text = out(result)
    assert "yt-dlp" in text and URL in text


def test_dry_run_reflects_flags():
    result = run("get", URL, "--mode", "audio", "--audio-format", "flac", "--dry-run")
    text = out(result)
    assert "--extract-audio" in text and "flac" in text


def test_preset_flag_is_applied():
    result = run("get", URL, "--preset", "mobile", "--dry-run")
    assert "height<=720" in out(result)


def test_unknown_preset_is_rejected():
    result = run("get", URL, "--preset", "nope", "--dry-run")
    assert result.exit_code != 0
    assert "nope" in out(result)


def test_set_override_reaches_the_command():
    result = run("get", URL, "-s", "rate_limit=3M", "-s", "concurrent_fragments=8", "--dry-run")
    text = out(result)
    assert "--limit-rate 3M" in text and "--concurrent-fragments 8" in text


def test_set_accepts_dashed_keys():
    result = run("get", URL, "-s", "video-quality=480", "--dry-run")
    assert "height<=480" in out(result)


def test_set_rejects_unknown_keys():
    result = run("get", URL, "-s", "not_a_setting=1", "--dry-run")
    assert result.exit_code != 0
    assert "not_a_setting" in out(result)


def test_set_rejects_missing_equals():
    result = run("get", URL, "-s", "rate_limit", "--dry-run")
    assert result.exit_code != 0


def test_set_rejects_bad_value_type():
    result = run("get", URL, "-s", "concurrent_fragments=lots", "--dry-run")
    assert result.exit_code != 0


def test_invalid_mode_is_rejected():
    result = run("get", URL, "--mode", "sideways", "--dry-run")
    assert result.exit_code != 0


def test_sponsorblock_flag():
    result = run("get", URL, "--sponsorblock", "--dry-run")
    assert "--sponsorblock-remove sponsor,selfpromo" in out(result)


def test_langs_flag_splits():
    result = run("get", URL, "--langs", "en,fr, de", "--dry-run")
    assert "--sub-langs en,fr,de" in out(result)


def test_no_subs_flag():
    result = run("get", URL, "--no-subs", "--dry-run")
    assert "--write-subs" not in out(result)


def test_output_and_template_flags(tmp_path):
    result = run("get", URL, "-o", str(tmp_path), "-t", "%(id)s.%(ext)s", "--dry-run")
    text = out(result)
    assert str(tmp_path) in text and "%(id)s.%(ext)s" in text


def test_config_path_is_reported():
    result = run("config", "--path")
    assert result.exit_code == 0
    assert "config.toml" in out(result)


def test_config_init_and_show(tmp_path):
    target = tmp_path / "c.toml"
    assert run("config", "--init", "-c", str(target)).exit_code == 0
    assert target.is_file()
    result = run("config", "--show", "-c", str(target))
    assert result.exit_code == 0
    assert "output_dir" in out(result)


def test_convert_writes_transcript(tmp_path):
    caption = tmp_path / "clip.en.vtt"
    caption.write_text(VTT, encoding="utf-8")
    result = run("convert", str(caption))
    assert result.exit_code == 0
    assert (tmp_path / "clip.en.txt").read_text().strip() == "hello world"


def test_convert_to_stdout(tmp_path):
    caption = tmp_path / "clip.srt"
    caption.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello world\n", encoding="utf-8")
    result = run("convert", str(caption), "--stdout")
    assert result.exit_code == 0
    assert "hello world" in result.output
    assert not (tmp_path / "clip.txt").exists()


def test_convert_with_timestamps(tmp_path):
    caption = tmp_path / "clip.en.vtt"
    caption.write_text(VTT, encoding="utf-8")
    run("convert", str(caption), "--timestamps")
    assert (tmp_path / "clip.en.txt").read_text().startswith("[00:00:01]")


def test_convert_reports_missing_file(tmp_path):
    result = run("convert", str(tmp_path / "nope.vtt"))
    assert result.exit_code == 1


def test_profile_roundtrip_through_cli(tmp_path, monkeypatch):
    monkeypatch.setattr("ytkit.storage.PROFILE_DIR", tmp_path)
    assert run("profile", "save", "hifi", "-p", "audio", "-s", "audio_format=flac").exit_code == 0
    assert "hifi" in out(run("profile", "list"))
    assert "flac" in out(run("profile", "show", "hifi"))
    assert run("get", URL, "-P", "hifi", "--dry-run").exit_code == 0
    assert run("profile", "delete", "hifi").exit_code == 0
    assert "hifi" not in out(run("profile", "list"))


def test_profile_show_missing_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("ytkit.storage.PROFILE_DIR", tmp_path)
    assert run("profile", "show", "ghost").exit_code == 1


def test_wizard_requires_a_tty(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    result = run("wizard")
    assert result.exit_code == 2


@pytest.mark.parametrize("command", ["get", "info", "transcript", "convert", "config", "profile"])
def test_help_for_every_command(command):
    assert run(command, "--help").exit_code == 0


def test_get_runs_downloads_and_exits_zero(tmp_path):
    """The offline fixture stubs the yt-dlp call; the runner path is real."""
    result = run("get", URL, "-o", str(tmp_path))
    assert result.exit_code == 0
    assert "Summary" in out(result)


def test_bracketed_filenames_survive_rich_markup(tmp_path):
    """Video IDs are bracketed, which Rich would otherwise parse as markup."""
    caption = tmp_path / "Clip [dQw4w9WgXcQ].en.vtt"
    caption.write_text(VTT, encoding="utf-8")
    result = run("convert", str(caption))
    assert result.exit_code == 0
    assert "[dQw4w9WgXcQ]" in result.output


# -- entry points -------------------------------------------------------------


def test_bare_invocation_launches_the_tui(monkeypatch):
    """`ytkit` with no arguments is the primary entry point."""
    import ytkit.tui.app as tui_app

    captured = {}
    monkeypatch.setattr(tui_app, "run", lambda cfg, urls, saved: captured.update(cfg=cfg, urls=urls, saved=saved))
    result = run()
    assert result.exit_code == 0, result.output
    assert isinstance(captured["cfg"], Config)
    assert captured["urls"] == []


def test_tui_subcommand_passes_urls_and_preset(monkeypatch, tmp_path):
    monkeypatch.setattr("ytkit.storage.CONFIG_PATH", tmp_path / "config.toml")
    import ytkit.tui.app as tui_app

    captured = {}
    monkeypatch.setattr(tui_app, "run", lambda cfg, urls, saved: captured.update(cfg=cfg, urls=urls, saved=saved))
    result = run("tui", URL, "--preset", "audio")
    assert result.exit_code == 0, result.output
    assert captured["urls"] == [URL]
    assert captured["cfg"].mode == "audio"
    # The unsaved mark compares against the file, which the preset hasn't touched.
    assert captured["saved"].mode != "audio"


def test_tui_subcommand_reports_a_missing_profile(monkeypatch, tmp_path):
    monkeypatch.setattr("ytkit.storage.PROFILE_DIR", tmp_path)
    result = run("tui", "--profile", "ghost")
    assert result.exit_code != 0


def test_set_can_disable_a_boolean_option():
    """`--set x=false` must switch the option off, not on."""
    result = run("get", URL, "-s", "subtitles=false", "-s", "auto_subtitles=false", "--dry-run")
    assert result.exit_code == 0
    assert "--write-subs" not in out(result)


def test_set_can_enable_a_boolean_option():
    result = run("get", URL, "-s", "embed_subs=true", "--dry-run")
    assert "--embed-subs" in out(result)


def test_set_rejects_an_unparseable_boolean():
    result = run("get", URL, "-s", "subtitles=maybe", "--dry-run")
    assert result.exit_code != 0
    assert "subtitles" in out(result)


def test_version_comes_from_pyproject():
    import tomllib
    from pathlib import Path

    from ytkit import __version__

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]
