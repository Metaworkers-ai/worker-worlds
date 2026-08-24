# Reference

Run `worker-worlds --help` and each subcommand's `--help` for the canonical CLI.
Exit code 0 means the requested operation and gate passed, 1 means evaluated
behavior or drift failed, and 2 means configuration, input, or infrastructure was
invalid. Scenario and contract schemas live in `schemas/v1`; policy identities are
listed in `worker_worlds.policies`; tool schemas are returned by `World.tools`.

## Agent registry

The optional `agents` mapping describes selectable workers without storing credentials or
importing provider SDKs while configuration is loaded. IDs and versions are explicit, native
frameworks use a lazy `module:callable` factory, and `required_env` contains variable names only:

```yaml
agents:
  support-agent:
    schema_version: "1.0"
    id: support-agent
    version: "1.0.0"
    adapter: openai-agents
    factory: my_project.agents:create_support_agent
    required_env: [OPENAI_API_KEY]
    model:
      provider: openai
      name: gpt-5-mini
```

Factories receive an `AgentFactoryContext` and may be synchronous or asynchronous. They must
return an object satisfying the framework-neutral `WorkerAdapter` protocol. Readiness diagnostics
report missing environment variable names, never their values. Agent factories execute trusted
project Python code and are not a sandbox boundary.

## Native tool bridge

SDK-managed adapters opt into the asynchronous bridge by implementing
`run_with_tools(scenario, tools)`. Each `NativeToolHandler` queues a normalized `ToolCall`; only
the runner invokes the world, and the handler awaits the correlated structured `ToolResult`.
Concurrent submissions retain FIFO order. Authorization is derived from the trusted scenario and
cannot be supplied by model arguments. Validation and authorization failures return to an opted-in
SDK loop, while legacy `NativeRuntime.decide` and stub adapters retain terminal tool-error behavior.
Cancellation fails all pending callbacks and provider termination with unresolved callbacks marks
the run evidence incomplete.

Configuration precedence is built-in defaults, `worker-worlds.yaml`, explicit
`WORKER_WORLDS_*` variables, then CLI arguments. Database URLs are never emitted
by normal commands. Supported runtime: Python 3.12; Postgres 17; tested adapter
compatibility pins are LangGraph `1.2.10`, LangChain `1.3.14`, LangChain OpenAI
`1.4.1`, and OpenAI Agents SDK `0.19.4`. Schema major 1 is preserved throughout the 1.x package line.
