"""Engine plumbing: job lifecycle, event sink, and post-download sidecars."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from ytkit.config import Config
from ytkit.engine import TERMINAL, Engine, Event, Job, State, _entry_basepath, _iter_entries

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
hello world

00:00:03.000 --> 00:00:05.000
second line
"""


class StubYDL:
    """Stands in for YoutubeDL during post-processing."""

    def __init__(self, base: Path) -> None:
        self.base = base

    def prepare_filename(self, info):
        return str(self.base)


def info_dict(title="A Talk", vid="abc123"):
    return {
        "id": vid,
        "title": title,
        "webpage_url": f"https://youtu.be/{vid}",
        "uploader": "Someone",
        "upload_date": "20240115",
        "duration": 3725,
        "view_count": 12345,
        "like_count": 678,
        "description": "A description.",
        "chapters": [{"start_time": 0, "title": "Intro"}, {"start_time": 90, "title": "Main"}],
        "subtitles": {"en": [{"ext": "vtt"}]},
        "automatic_captions": {"en": [{"ext": "vtt"}], "fr": [{"ext": "vtt"}]},
    }


def drain(q: queue.Queue) -> list[Event]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


# -- job lifecycle ------------------------------------------------------------


def test_jobs_run_and_reach_a_terminal_state():
    events: queue.Queue[Event] = queue.Queue()
    engine = Engine(events.put, workers=2)
    jobs = engine.submit_urls(["https://a.test/1", "https://b.test/2"], Config())

    deadline = time.monotonic() + 5
    while any(j.state not in TERMINAL for j in jobs) and time.monotonic() < deadline:
        time.sleep(0.01)
    engine.shutdown(wait=True)

    assert all(j.state is State.DONE for j in jobs)
    kinds = {e.kind for e in drain(events)}
    assert "state" in kinds


def test_each_job_gets_an_independent_config_copy():
    engine = Engine(lambda e: None)
    cfg = Config()
    jobs = engine.submit_urls(["https://a.test/1", "https://b.test/2"], cfg)
    engine.shutdown(wait=True)
    jobs[0].config.sub_langs.append("fr")
    assert jobs[1].config.sub_langs == ["en"]
    assert cfg.sub_langs == ["en"]


def test_submit_after_shutdown_is_cancelled_not_raised():
    engine = Engine(lambda e: None)
    engine.shutdown(wait=True)
    job = engine.submit(Job(url="https://late.test", config=Config()))
    assert job.state is State.CANCELLED


def test_shutdown_is_idempotent():
    engine = Engine(lambda e: None)
    engine.shutdown(wait=True)
    engine.shutdown(wait=True)  # must not raise


def test_active_count_tracks_unfinished_jobs():
    engine = Engine(lambda e: None)
    assert engine.active == 0
    engine.submit_urls(["https://a.test/1"], Config())
    engine.shutdown(wait=True)
    assert engine.active == 0


def test_cancel_marks_a_queued_job_cancelled():
    gate = threading.Event()
    engine = Engine(lambda e: None, workers=1)

    def blocker(self, job):
        gate.wait(timeout=5)
        self._finish(job, State.DONE)

    engine._run = blocker.__get__(engine, Engine)
    first, second = engine.submit_urls(["https://a.test/1", "https://a.test/2"], Config())
    time.sleep(0.05)
    engine.cancel(second.id)  # still queued behind the blocked worker
    assert second.state is State.CANCELLED
    gate.set()
    engine.shutdown(wait=True)


# -- entry traversal ----------------------------------------------------------


def test_iter_entries_flattens_nested_playlists():
    info = {
        "_type": "playlist",
        "entries": [
            {"_type": "playlist", "entries": [info_dict(vid="a"), info_dict(vid="b")]},
            info_dict(vid="c"),
            None,
        ],
    }
    assert [e["id"] for e in _iter_entries(info)] == ["a", "b", "c"]


def test_iter_entries_handles_a_single_video():
    assert [e["id"] for e in _iter_entries(info_dict())] == ["abc123"]


def test_basepath_prefers_the_real_downloaded_path(tmp_path):
    entry = info_dict() | {"requested_downloads": [{"filepath": str(tmp_path / "Real.mp4")}]}
    assert _entry_basepath(StubYDL(tmp_path / "Guess.mp4"), entry) == tmp_path / "Real.mp4"


def test_basepath_falls_back_to_the_template(tmp_path):
    assert _entry_basepath(StubYDL(tmp_path / "Guess.mp4"), info_dict()) == tmp_path / "Guess.mp4"


def test_basepath_survives_a_broken_template(tmp_path):
    class Broken:
        def prepare_filename(self, info):
            raise ValueError("bad template")

    assert _entry_basepath(Broken(), info_dict()) is None


# -- post-download sidecars ---------------------------------------------------


def make_engine_and_job(tmp_path, **overrides):
    events: queue.Queue[Event] = queue.Queue()
    engine = Engine(events.put)
    cfg = Config(output_dir=str(tmp_path), **overrides)
    job = Job(url="https://youtu.be/abc123", config=cfg)
    return engine, job, events


def test_postprocess_writes_transcript_and_summary(tmp_path):
    media = tmp_path / "A Talk [abc123].mp4"
    media.write_bytes(b"")
    (tmp_path / "A Talk [abc123].en.vtt").write_text(VTT, encoding="utf-8")

    engine, job, events = make_engine_and_job(tmp_path)
    entry = info_dict() | {"requested_downloads": [{"filepath": str(media)}]}
    engine._postprocess(job, StubYDL(media), entry)
    engine.shutdown(wait=True)

    transcript = tmp_path / "A Talk [abc123].en.txt"
    summary = tmp_path / "A Talk [abc123].summary.md"
    assert transcript.is_file() and summary.is_file()

    body = transcript.read_text()
    assert body.startswith("# A Talk")
    assert "hello world second line" in body

    md = summary.read_text()
    assert "# A Talk" in md and "Someone" in md and "01:02:05" in md  # duration
    assert "Intro" in md and "Main" in md  # chapters
    assert job.items_done == 1
    assert {transcript, summary} <= set(job.files)


def test_postprocess_respects_disabled_sidecars(tmp_path):
    media = tmp_path / "A Talk [abc123].mp4"
    media.write_bytes(b"")
    (tmp_path / "A Talk [abc123].en.vtt").write_text(VTT, encoding="utf-8")

    engine, job, _ = make_engine_and_job(tmp_path, transcript_txt=False, write_summary=False)
    engine._postprocess(job, StubYDL(media), info_dict())
    engine.shutdown(wait=True)

    assert not (tmp_path / "A Talk [abc123].en.txt").exists()
    assert not (tmp_path / "A Talk [abc123].summary.md").exists()


def test_postprocess_converts_every_language(tmp_path):
    media = tmp_path / "Talk [x].mp4"
    media.write_bytes(b"")
    for lang in ("en", "fr", "de"):
        (tmp_path / f"Talk [x].{lang}.vtt").write_text(VTT, encoding="utf-8")

    engine, job, _ = make_engine_and_job(tmp_path)
    engine._postprocess(job, StubYDL(media), info_dict())
    engine.shutdown(wait=True)

    assert sorted(p.name for p in tmp_path.glob("*.txt")) == [
        "Talk [x].de.txt",
        "Talk [x].en.txt",
        "Talk [x].fr.txt",
    ]


def test_postprocess_handles_a_playlist(tmp_path):
    engine, job, _ = make_engine_and_job(tmp_path)
    entries = []
    for vid in ("a", "b"):
        media = tmp_path / f"Vid {vid}.mp4"
        media.write_bytes(b"")
        entries.append(info_dict(title=f"Vid {vid}", vid=vid) | {"requested_downloads": [{"filepath": str(media)}]})

    engine._postprocess(job, StubYDL(tmp_path), {"_type": "playlist", "entries": entries})
    engine.shutdown(wait=True)

    assert job.items_done == 2
    assert (tmp_path / "Vid a.summary.md").is_file()
    assert (tmp_path / "Vid b.summary.md").is_file()


def test_postprocess_ignores_missing_captions(tmp_path):
    media = tmp_path / "No Captions.mp4"
    media.write_bytes(b"")
    engine, job, _ = make_engine_and_job(tmp_path)
    engine._postprocess(job, StubYDL(media), info_dict(title="No Captions"))
    engine.shutdown(wait=True)
    assert (tmp_path / "No Captions.summary.md").is_file()


def test_only_existing_files_are_reported(tmp_path):
    """skip-download reports an intended filepath that was never written."""
    engine, job, events = make_engine_and_job(tmp_path)
    engine._add_file(job, tmp_path / "never-written.mp4")
    real = tmp_path / "written.mp4"
    real.write_bytes(b"")
    engine._add_file(job, real)
    engine.shutdown(wait=True)
    assert job.files == [real]


def test_files_are_not_reported_twice(tmp_path):
    engine, job, _ = make_engine_and_job(tmp_path)
    real = tmp_path / "x.mp4"
    real.write_bytes(b"")
    engine._add_file(job, real)
    engine._add_file(job, real)
    engine.shutdown(wait=True)
    assert job.files == [real]


def test_stale_files_are_pruned_when_the_job_finishes(tmp_path):
    """A .vtt recorded mid-run is gone once --convert-subs rewrites it as .srt."""
    engine, job, _ = make_engine_and_job(tmp_path)
    survivor = tmp_path / "kept.srt"
    survivor.write_text("x", encoding="utf-8")
    doomed = tmp_path / "gone.vtt"
    doomed.write_text("x", encoding="utf-8")
    engine._add_file(job, survivor)
    engine._add_file(job, doomed)
    doomed.unlink()

    engine._finish(job, State.DONE)
    engine.shutdown(wait=True)
    assert job.files == [survivor]


def test_final_caption_tracks_are_reported(tmp_path):
    media = tmp_path / "Talk [x].mp4"
    media.write_bytes(b"")
    caption = tmp_path / "Talk [x].en.srt"
    caption.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi there\n", encoding="utf-8")

    engine, job, _ = make_engine_and_job(tmp_path)
    engine._postprocess(job, StubYDL(media), info_dict(title="Talk", vid="x"))
    engine.shutdown(wait=True)
    assert caption in job.files
    assert tmp_path / "Talk [x].en.txt" in job.files


def test_sidecars_written_without_hooks_are_still_reported(tmp_path):
    """info.json / description / thumbnail arrive with no progress hook at all."""
    media = tmp_path / "Talk [x].mp3"
    media.write_bytes(b"")
    for suffix in (".info.json", ".description", ".webp"):
        (tmp_path / f"Talk [x]{suffix}").write_bytes(b"")

    engine, job, _ = make_engine_and_job(tmp_path)
    engine._postprocess(job, StubYDL(media), info_dict(title="Talk", vid="x"))
    engine._finish(job, State.DONE)
    engine.shutdown(wait=True)

    names = {p.name for p in job.files}
    assert names == {
        "Talk [x].mp3",
        "Talk [x].info.json",
        "Talk [x].description",
        "Talk [x].webp",
        "Talk [x].summary.md",
    }


def test_sibling_sweep_does_not_steal_a_similarly_named_entry(tmp_path):
    from ytkit.engine import _sibling_outputs

    (tmp_path / "Talk 1.mp4").write_bytes(b"")
    (tmp_path / "Talk 1.info.json").write_bytes(b"")
    (tmp_path / "Talk 10.mp4").write_bytes(b"")

    found = {p.name for p in _sibling_outputs(tmp_path / "Talk 1.mp4")}
    assert found == {"Talk 1.mp4", "Talk 1.info.json"}
