from __future__ import annotations

import pytest

from ytkit.config import Config
from ytkit.presets import BY_KEY, PRESETS
from ytkit.storage import load_config, load_profile, save_config, save_profile


def test_roundtrip_preserves_every_field(tmp_path):
    cfg = Config()
    for name, f in Config.spec():
        kind = f.metadata["kind"]
        if kind == "bool":
            setattr(cfg, name, not getattr(cfg, name))
        elif kind == "int":
            setattr(cfg, name, 7)
        elif kind == "list":
            setattr(cfg, name, ["x", "y"])
        elif kind == "choice":
            choices = f.metadata["choices"]
            setattr(cfg, name, choices[-1])
        elif isinstance(Config.default_for(name), float):
            setattr(cfg, name, 1.5)
        else:
            setattr(cfg, name, "value")

    path = save_config(cfg, tmp_path / "c.toml", full=True)
    assert load_config(path) == cfg


def test_only_changed_values_are_written(tmp_path):
    cfg = Config(video_quality="720")
    path = save_config(cfg, tmp_path / "c.toml")
    text = path.read_text()
    assert "video_quality" in text
    assert "concurrent_fragments" not in text  # untouched default stays implicit
    assert load_config(path).video_quality == "720"


def test_missing_file_yields_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_unknown_and_malformed_keys_are_ignored(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[output]\nnot_a_setting = 1\ntrim_filenames = "abc"\nvideo_quality = "480"\n')
    cfg = load_config(path)
    assert cfg.video_quality == "480"
    assert cfg.trim_filenames == Config.default_for("trim_filenames")


def test_flat_toml_is_accepted(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('video_quality = "360"\nsubtitles = false\n')
    cfg = load_config(path)
    assert cfg.video_quality == "360" and cfg.subtitles is False


def test_list_given_as_string_is_split(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('sub_langs = "en, fr ,de"\n')
    assert load_config(path).sub_langs == ["en", "fr", "de"]


def test_profiles_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("ytkit.storage.PROFILE_DIR", tmp_path)
    cfg = Config(mode="audio", audio_format="flac")
    save_profile("My Profile", cfg)
    assert load_profile("My Profile").audio_format == "flac"


def test_profile_names_are_sanitised(tmp_path, monkeypatch):
    monkeypatch.setattr("ytkit.storage.PROFILE_DIR", tmp_path)
    save_profile("../../evil name", Config())
    assert not list(tmp_path.parent.glob("evil*"))
    assert (tmp_path / "evil-name.toml").exists()


def test_set_from_string_coercion():
    cfg = Config()
    assert cfg.set_from_string("trim_filenames", "42") and cfg.trim_filenames == 42
    assert cfg.set_from_string("sub_langs", "en, es") and cfg.sub_langs == ["en", "es"]
    assert cfg.set_from_string("sleep_interval", "2.5") and cfg.sleep_interval == 2.5
    assert cfg.set_from_string("subtitles", False) and cfg.subtitles is False


def test_set_from_string_rejects_bad_numbers():
    cfg = Config()
    assert cfg.set_from_string("trim_filenames", "not a number") is False
    assert cfg.trim_filenames == 0


def test_diff_from_defaults_is_empty_for_fresh_config():
    assert Config().diff_from_defaults() == {}


def test_copy_is_independent():
    cfg = Config()
    clone = cfg.copy()
    clone.sub_langs.append("fr")
    assert cfg.sub_langs == ["en"]


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
def test_presets_only_set_real_fields(preset):
    for key in preset.overrides:
        assert hasattr(Config(), key), f"{preset.key} sets unknown field {key}"


def test_preset_application_does_not_mutate_the_source():
    cfg = Config()
    BY_KEY["audio"].apply(cfg)
    assert cfg.mode == "video"


def test_every_field_declares_usable_metadata():
    for name, f in Config.spec():
        meta = f.metadata
        assert meta["label"], f"{name} has no label"
        assert meta["section"], f"{name} has no section"
        assert meta["kind"] in ("bool", "int", "text", "path", "dir", "choice", "list")
        if meta["kind"] == "choice":
            assert meta["choices"], f"{name} is a choice with no options"
            assert Config.default_for(name) in meta["choices"], f"{name} default not in choices"


# -- boolean parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True), ("True", True), ("TRUE", True), ("yes", True),
        ("on", True), ("1", True), ("y", True), (" true ", True), (True, True),
        ("false", False), ("False", False), ("no", False), ("off", False),
        ("0", False), ("n", False), ("", False), (False, False),
    ],
)
def test_parse_bool_words(raw, expected):
    from ytkit.config import parse_bool

    assert parse_bool(raw) is expected


@pytest.mark.parametrize("raw", ["maybe", "sometimes", "2ish", "yeah nah"])
def test_parse_bool_rejects_nonsense(raw):
    from ytkit.config import parse_bool

    assert parse_bool(raw) is None


def test_set_from_string_disables_a_boolean():
    """bool('false') is True — the reason this needs a real parser."""
    cfg = Config()
    assert cfg.subtitles is True
    assert cfg.set_from_string("subtitles", "false") is True
    assert cfg.subtitles is False


def test_set_from_string_enables_a_boolean():
    cfg = Config(subtitles=False)
    assert cfg.set_from_string("subtitles", "true") is True
    assert cfg.subtitles is True


def test_set_from_string_rejects_an_unparseable_boolean():
    cfg = Config()
    assert cfg.set_from_string("subtitles", "maybe") is False
    assert cfg.subtitles is True  # untouched


def test_toml_string_booleans_are_parsed_not_truthy(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('subtitles = "false"\nembed_subs = "yes"\n')
    cfg = load_config(path)
    assert cfg.subtitles is False
    assert cfg.embed_subs is True


def test_toml_native_booleans_still_work(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("subtitles = false\nembed_subs = true\n")
    cfg = load_config(path)
    assert cfg.subtitles is False
    assert cfg.embed_subs is True


def test_parse_value_checks_without_setting():
    cfg = Config()
    assert Config.parse_value("max_fps", "30") == (True, 30)
    assert Config.parse_value("max_fps", "-")[0] is False
    assert Config.parse_value("sub_langs", "en, fr") == (True, ["en", "fr"])
    assert cfg == Config()
