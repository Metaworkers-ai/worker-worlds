# Reference

Run `worker-worlds --help` and each subcommand's `--help` for the canonical CLI.
Exit code 0 means the requested operation and gate passed, 1 means evaluated
behavior or drift failed, and 2 means configuration, input, or infrastructure was
invalid. Scenario and contract schemas live in `schemas/v1`; policy identities are
listed in `worker_worlds.policies`; tool schemas are returned by `World.tools`.

Configuration precedence is built-in defaults, `worker-worlds.yaml`, explicit
`WORKER_WORLDS_*` variables, then CLI arguments. Database URLs are never emitted
by normal commands. Supported runtime: Python 3.12; Postgres 17; LangGraph
`>=0.6,<1`; OpenAI Agents SDK `>=0.2,<1`. Schema major 1 is preserved throughout
the 1.x package line.
