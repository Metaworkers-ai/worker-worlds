"""Deterministic network-free OpenAI Agents SDK adapter example."""

from worker_worlds.adapters import OpenAIAgentsAdapter, refund_fake_runtime

worker = OpenAIAgentsAdapter(refund_fake_runtime())
