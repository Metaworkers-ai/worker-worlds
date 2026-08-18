"""Deterministic network-free LangGraph adapter example."""

from worker_worlds.adapters import LangGraphAdapter, refund_fake_runtime

worker = LangGraphAdapter(refund_fake_runtime())
