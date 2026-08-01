from __future__ import annotations

from ytkit.transcripts import Cue, convert_file, dedupe, find_captions, parse_cues, render

VTT_ROLLING = """WEBVTT
Kind: captions
Language: en

00:00:00.560 --> 00:00:02.879 align:start position:0%
we're going to talk

00:00:02.879 --> 00:00:05.120 align:start position:0%
we're going to talk about <c>caption</c> parsing

00:00:05.120 --> 00:00:08.000 align:start position:0%
about caption parsing
and why it matters

00:00:20.000 --> 00:00:22.000
a much later thought
"""

SRT_SAMPLE = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,000 --> 00:00:05,500
General Kenobi &amp; friends.
"""

ASS_SAMPLE = """[Script Info]
Title: test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an8}First line
Dialogue: 0,0:00:03.00,0:00:05.00,Default,,0,0,0,,Second\\Nline
"""


def test_vtt_parses_and_strips_tags():
    cues = parse_cues(VTT_ROLLING, ".vtt")
    assert len(cues) == 4
    assert "<c>" not in " ".join(line for c in cues for line in c.lines)
    assert cues[0].start == 0.56


def test_rolling_captions_are_deduplicated():
    text = render(parse_cues(VTT_ROLLING, ".vtt"))
    assert text.count("we're going to talk") == 1
    assert text.count("about caption parsing") == 1


def test_paragraph_break_on_long_gap():
    text = render(parse_cues(VTT_ROLLING, ".vtt"))
    assert "\n\n" in text  # the 12-second gap starts a new paragraph
    assert text.strip().endswith("a much later thought")


def test_timestamps_mode():
    text = render(parse_cues(VTT_ROLLING, ".vtt"), timestamps=True)
    assert text.startswith("[00:00:00] ")
    assert "[00:00:20] a much later thought" in text


def test_srt_and_entities():
    cues = parse_cues(SRT_SAMPLE, ".srt")
    assert len(cues) == 2
    assert cues[1].lines == ["General Kenobi & friends."]


def test_srt_sequence_numbers_are_not_content():
    text = render(parse_cues(SRT_SAMPLE, ".srt"))
    assert not text.startswith("1")


def test_ass_parsing():
    cues = parse_cues(ASS_SAMPLE, ".ass")
    assert [c.lines[0] for c in cues] == ["First line", "Second line"]


def test_dedupe_keeps_legitimate_repeats_far_apart():
    cues = [Cue(0, 1, ["yes"]), Cue(1, 2, ["a"]), Cue(2, 3, ["b"]), Cue(3, 4, ["c"]),
            Cue(4, 5, ["d"]), Cue(5, 6, ["e"]), Cue(6, 7, ["f"]), Cue(7, 8, ["g"]),
            Cue(8, 9, ["h"]), Cue(9, 10, ["yes"])]
    out = dedupe(cues)
    assert out[-1].lines == ["yes"]  # outside the 8-cue lookback window


def test_empty_input_renders_empty():
    assert render(parse_cues("WEBVTT\n\n", ".vtt")) == ""


def test_convert_file_roundtrip(tmp_path):
    caption = tmp_path / "Video Title [abc123].en.vtt"
    caption.write_text(VTT_ROLLING, encoding="utf-8")
    out = convert_file(caption, header="# Video Title\nhttps://example.com")
    assert out == tmp_path / "Video Title [abc123].en.txt"
    body = out.read_text(encoding="utf-8")
    assert body.startswith("# Video Title")
    assert "caption parsing" in body


def test_find_captions_handles_brackets_in_filenames(tmp_path):
    stem = "Video [abc123]"
    for suffix in (".en.vtt", ".es.srt", ".mp4", ".info.json"):
        (tmp_path / f"{stem}{suffix}").write_text("x", encoding="utf-8")
    found = find_captions(tmp_path, stem)
    assert [p.name for p in found] == [f"{stem}.en.vtt", f"{stem}.es.srt"]


def test_find_captions_ignores_other_videos(tmp_path):
    (tmp_path / "Video A.en.vtt").write_text("x", encoding="utf-8")
    (tmp_path / "Video B.en.vtt").write_text("x", encoding="utf-8")
    assert [p.name for p in find_captions(tmp_path, "Video A")] == ["Video A.en.vtt"]
