# ADR 002: checked-in contract schemas

Status: accepted, 2026-08-18.

Pydantic models remain the executable source of truth. Their JSON Schemas are
checked into `schemas/v1/` as canonical sorted JSON. `worker-worlds-schema
check` regenerates schemas in memory and fails on any drift. Compatible fields
may be added with defaults within major version 1; removals, reinterpretations,
or new required fields require a major schema version.
