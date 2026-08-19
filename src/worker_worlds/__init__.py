"""Worker Worlds public package."""

from worker_worlds.contracts import RunRecord, Scenario

__all__ = ["RunRecord", "Scenario"]
__version__ = "1.0.0rc1"
USER_AGENT = f"worker-worlds/{__version__}"
