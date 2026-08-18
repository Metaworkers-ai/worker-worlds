from pathlib import Path

from worker_worlds.grading import DeterministicGrader
from worker_worlds.protocols import Grader, Reporter, WorkerAdapter, World
from worker_worlds.reporting import JsonReporter
from worker_worlds.stubs import StubWorkerAdapter, StubWorld


def test_runtime_protocol_conformance(tmp_path: Path) -> None:
    assert isinstance(StubWorld(), World)
    assert isinstance(StubWorkerAdapter(), WorkerAdapter)
    assert isinstance(DeterministicGrader(), Grader)
    assert isinstance(JsonReporter(tmp_path), Reporter)
