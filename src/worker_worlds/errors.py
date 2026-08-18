"""Structured Worker Worlds exceptions."""


class WorkerWorldsError(Exception):
    """Base class for public runtime failures."""


class ScenarioLoadError(WorkerWorldsError):
    """A scenario could not be decoded or validated."""


class InfrastructureError(WorkerWorldsError):
    """The harness could not collect complete world evidence."""


class AdapterError(WorkerWorldsError):
    """A worker framework adapter failed outside worker behavior."""


class ProviderError(WorkerWorldsError):
    """A model provider failed outside worker behavior."""


class ScenarioAuthoringError(WorkerWorldsError):
    """A scenario cannot be executed as authored."""
