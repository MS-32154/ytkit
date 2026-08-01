"""Romanisation of non-Latin metadata into terminal-navigable filenames."""

from __future__ import annotations

import pytest

from ytkit.romanise import ORIGINAL_PREFIX, original, romanise, romanise_info
from ytkit.sidecar import summarise

ARABIC_TITLE = "كيف نجد الكواكب الصالحة للحياة؟ - وثائقي الفضاء"
ARABIC_CHANNEL = "الفيلم الوثائقي العربي"


def test_arabic_becomes_ascii():
    out = romanise(ARABIC_TITLE)
    assert out.isascii() and out


@pytest.mark.parametrize(
    "text",
    [
        ARABIC_TITLE,
        ARABIC_CHANNEL,
        "Москва — документальный фильм",
        "日本語のタイトル",
        "한국어 제목",
        "Ελληνικά",
        "עברית",
        "混合 mixed العربية text",
    ],
)
def test_every_script_yields_usable_ascii(text):
    from yt_dlp.utils import sanitize_filename

    out = romanise(text)
    assert out.isascii(), f"non-ascii survived: {out!r}"
    assert out.strip(), "produced an empty name"
    # It must still be ASCII *after* yt-dlp sanitises it into a filename.
    assert sanitize_filename(out).isascii()
    assert sanitize_filename(out, restricted=True).isascii()


@pytest.mark.parametrize("char", list("`'$!&;(){}[]~^"))
def test_shell_hostile_characters_are_removed(char):
    """A backtick from Arabic ain would be dangerous in an unquoted path."""
    assert char not in romanise(f"abc{char}def")


@pytest.mark.parametrize("char", list('":/\\|?*<>'))
def test_fullwidth_triggers_never_reach_yt_dlp(char):
    """yt-dlp rewrites these to fullwidth forms, undoing the whole exercise."""
    from yt_dlp.utils import sanitize_filename

    assert sanitize_filename(romanise(f"abc{char}def")).isascii()


def test_yt_dlp_fullwidth_triggers_are_removed():
    """yt-dlp rewrites ? : | etc. to fullwidth forms, which are not ASCII."""
    assert romanise("what? this|that").isascii()
    assert "?" not in romanise("what?")


def test_ascii_titles_are_left_completely_alone():
    info = {"id": "x", "title": "Already ASCII: fine? yes!", "uploader": "Some Channel"}
    romanise_info(info)
    assert info["title"] == "Already ASCII: fine? yes!"
    assert original(info, "title") is None


def test_originals_are_stashed():
    info = {"id": "Q88", "title": ARABIC_TITLE, "uploader": ARABIC_CHANNEL}
    romanise_info(info)
    assert info["title"].isascii()
    assert info["uploader"].isascii()
    assert original(info, "title") == ARABIC_TITLE
    assert original(info, "uploader") == ARABIC_CHANNEL


def test_channel_is_romanised_because_it_becomes_a_directory():
    info = {"id": "x", "uploader": ARABIC_CHANNEL, "channel": ARABIC_CHANNEL}
    romanise_info(info)
    assert info["uploader"].isascii() and info["channel"].isascii()


def test_untranslatable_title_falls_back_to_the_id():
    info = {"id": "abc123", "title": "؟؟؟"}  # romanises to punctuation only
    romanise_info(info)
    assert info["title"] == "abc123"


def test_emoji_titles_survive_as_names():
    info = {"id": "x", "title": "🎬 وثائقي"}
    romanise_info(info)
    assert info["title"].isascii() and info["title"].strip()


def test_romanise_info_is_idempotent():
    info = {"id": "x", "title": ARABIC_TITLE}
    romanise_info(info)
    once = info["title"]
    romanise_info(info)
    assert info["title"] == once
    assert original(info, "title") == ARABIC_TITLE  # not overwritten by pass two


def test_summary_keeps_the_original_title():
    """The sidecar is where the searchable Arabic name has to survive."""
    info = {"id": "Q88", "title": ARABIC_TITLE, "uploader": ARABIC_CHANNEL}
    romanise_info(info)
    md = summarise(info)
    assert ARABIC_TITLE in md
    assert ARABIC_CHANNEL in md
    assert info["title"] in md  # the filename form is shown too


def test_summary_unaffected_for_ascii_titles():
    md = summarise({"id": "x", "title": "Plain Title", "uploader": "Chan"})
    assert "# Plain Title" in md
    assert "Filename uses" not in md


def test_original_prefix_keys_do_not_collide_with_ytdlp_fields():
    assert ORIGINAL_PREFIX.startswith("ytkit_")
