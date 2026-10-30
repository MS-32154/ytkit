"""What a queue row says about a job, before any widget is involved."""

from __future__ import annotations

import pytest

from ytkit.config import Config
from ytkit.engine import Job, State
from ytkit.tui.app import PROGRESS_WIDTH, progress_cell, short_reason


@pytest.mark.parametrize(
    "error,reason",
    [
        ("ERROR: [youtube] jNQXAC9IVRw: Private video. Sign in if you've been granted access", "Private video"),
        ("ERROR: [youtube] abc: Video unavailable", "Video unavailable"),
        ("ERROR: Unsupported URL: https://example.com/", "Unsupported URL: https://example.com/"),
        ("ConnectionError: timed out", "ConnectionError: timed out"),
        ("", "failed"),
    ],
)
def test_short_reason(error, reason):
    assert short_reason(error) == reason


def job(state: State, **fields) -> Job:
    return Job(url="https://example.com/x", config=Config(), state=state, **fields)


def test_waiting_jobs_show_no_bar():
    assert str(progress_cell(job(State.QUEUED))) == ""
    assert str(progress_cell(job(State.CANCELLED))) == ""


def test_progress_survives_a_cancel_partway():
    assert "40.0%" in str(progress_cell(job(State.CANCELLED, percent=40.0)))


def test_long_reasons_fit_the_column():
    cell = progress_cell(job(State.FAILED, error="ERROR: " + "x" * 80))
    assert cell.cell_len <= PROGRESS_WIDTH
    assert str(cell).endswith("…")
