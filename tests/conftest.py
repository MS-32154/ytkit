from __future__ import annotations

import pytest

from ytkit.engine import Engine, Job, State


@pytest.fixture(autouse=True)
def offline(monkeypatch, request):
    """Stop any test from reaching the network.

    Jobs still travel through the real queue, worker pool and event sink — only
    the yt-dlp call itself is replaced — so the UI plumbing is exercised for real.
    """
    if "network" in request.keywords or "real_run" in request.keywords:
        return

    def fake_run(self: Engine, job: Job) -> None:
        job.title = f"Fake · {job.url}"
        self._emit_state(job, State.RUNNING)
        job.percent = 100.0
        self._finish(job, State.DONE)

    monkeypatch.setattr(Engine, "_run", fake_run)
