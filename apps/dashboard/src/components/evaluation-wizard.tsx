"use client";

import { useMemo, useState } from "react";
import { CheckCircle2, LoaderCircle, Play, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Agent, Catalog, SuiteJob } from "@/lib/dashboard-data";
import type { SuiteBudget, SuiteDefinition } from "@/lib/dashboard-data";

type WorldName = "stub" | "postgres" | "supply-chain" | "insurance";

export function EvaluationWizard({
  catalog,
  agents,
  running,
  latestJob,
  onStart,
  onCancel,
}: {
  catalog: Catalog;
  agents: Agent[];
  running: boolean;
  latestJob: SuiteJob | null;
  onStart: (input: {
    requestKey: string;
    domainId: string;
    roleId: string;
    suiteId: string;
    agentId: string;
    world: WorldName;
    concurrency: number;
    scenarioIds?: string[];
    seed?: number;
    limits?: SuiteDefinition["default_limits"];
    budget?: SuiteBudget;
  }) => void;
  onCancel: (jobId: string) => void;
}) {
  const [domainChoice, setDomainChoice] = useState("");
  const [roleChoice, setRoleChoice] = useState("");
  const [suiteChoice, setSuiteChoice] = useState("");
  const [agentChoice, setAgentChoice] = useState("");
  const [concurrency, setConcurrency] = useState(4);
  const [worldChoice, setWorldChoice] = useState<WorldName | "">("");
  const [customScenarioIds, setCustomScenarioIds] = useState<string[]>([]);
  const [seedOverride, setSeedOverride] = useState("");
  const [deadlineOverride, setDeadlineOverride] = useState("");
  const [tokenBudgetOverride, setTokenBudgetOverride] = useState("");
  const [mutationBudgetOverride, setMutationBudgetOverride] = useState("");
  const [injectionOverride, setInjectionOverride] = useState("");

  const domain =
    catalog.domains.find((item) => item.id === domainChoice) ?? catalog.domains[0];
  const roles = useMemo(
    () => catalog.roles.filter((item) => item.domain_id === domain?.id),
    [catalog.roles, domain?.id],
  );
  const role = roles.find((item) => item.id === roleChoice) ?? roles[0];
  const suites = useMemo(
    () => catalog.suites.filter((item) => item.role_id === role?.id),
    [catalog.suites, role?.id],
  );
  const suite =
    suites.find((item) => item.id === suiteChoice) ??
    suites.find((item) => item.tier === "smoke") ??
    suites[0];
  const compatibleAgents = agents.filter(
    (item) => !domain || item.supported_domain_ids?.includes(domain.id) !== false,
  );
  const agent =
    compatibleAgents.find((item) => item.id === agentChoice && item.ready) ??
    compatibleAgents.find((item) => item.ready);
  const capabilities = catalog.capabilities.filter((item) =>
    suite?.capability_ids.includes(item.id),
  );
  const selectedIds = new Set(suite?.scenario_ids ?? []);
  const risks = Array.from(
    new Set(
      catalog.classifications
        .filter((item) => selectedIds.has(item.scenario_id))
        .map((item) => item.risk_category),
    ),
  ).sort();
  const defaultWorld: WorldName =
    domain?.id === "insurance"
      ? "insurance"
      : role?.id === "supply-chain-analyst"
        ? "supply-chain"
        : "postgres";
  const world = worldChoice || defaultWorld;
  const eligibleCustomScenarios = catalog.classifications.filter(
    (item) => item.domain_id === domain?.id && item.role_ids.includes(role?.id ?? ""),
  );
  const custom = suite?.tier === "custom";
  const ready = Boolean(
    domain && role && suite && agent?.ready && (!custom || customScenarioIds.length),
  );

  return (
    <Card className="border-primary/30 bg-primary/[0.035] shadow-none">
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs text-primary">Guided evaluation</p>
            <CardTitle className="mt-1 text-lg">Choose business context, then run</CardTitle>
          </div>
          <Badge variant="outline" className="font-mono text-[10px]">
            catalog {catalog.catalog_version}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <WizardSelect
            step="1"
            label="Domain"
            value={domain?.id ?? ""}
            onChange={(value) => {
              setDomainChoice(value);
              setRoleChoice("");
              setSuiteChoice("");
              setWorldChoice("");
              setCustomScenarioIds([]);
            }}
            options={catalog.domains.map((item) => ({ value: item.id, label: item.label }))}
          />
          <WizardSelect
            step="2"
            label="Job role"
            value={role?.id ?? ""}
            disabled={!domain}
            onChange={(value) => {
              setRoleChoice(value);
              setSuiteChoice("");
              setWorldChoice("");
              setCustomScenarioIds([]);
            }}
            options={roles.map((item) => ({ value: item.id, label: item.label }))}
          />
          <WizardSelect
            step="3"
            label="Evaluation suite"
            value={suite?.id ?? ""}
            disabled={!role}
            onChange={(value) => {
              setSuiteChoice(value);
              setCustomScenarioIds([]);
            }}
            options={suites.map((item) => ({
              value: item.id,
              label: `${item.tier} · ${item.scenario_ids.length} scenarios`,
            }))}
          />
          <fieldset
            className="rounded-lg border border-border/70 bg-background/45 p-3"
            disabled={!suite}
          >
            <legend className="sr-only">Choose evaluation agent</legend>
            <span className="flex items-center gap-2 text-xs font-medium">
              <StepNumber value="4" /> AI agent
            </span>
            <div
              className="mt-3 space-y-1"
              role="radiogroup"
              aria-label="Choose evaluation agent"
            >
              {compatibleAgents.map((item) => (
                <label
                  key={item.id}
                  className="flex items-center gap-2 rounded-md border border-border/60 px-2.5 py-2 text-xs has-[:checked]:border-primary/50 has-[:checked]:bg-primary/[0.08] has-[:disabled]:opacity-50"
                >
                  <input
                    type="radio"
                    name="evaluation-agent"
                    value={item.id}
                    checked={agent?.id === item.id}
                    disabled={!item.ready}
                    onChange={() => setAgentChoice(item.id)}
                  />
                  <span className="min-w-0 truncate">{item.id}</span>
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {item.ready ? "ready" : "unavailable"}
                  </span>
                  {!item.ready && item.missing_requirements.length ? (
                    <span className="sr-only">
                      Missing {item.missing_requirements.join(", ")}
                    </span>
                  ) : null}
                </label>
              ))}
            </div>
            {!agent && compatibleAgents.length ? (
              <p className="mt-2 text-[11px] text-amber-300">
                No compatible agent is ready.
              </p>
            ) : null}
            {!agent ? (
              <ul className="mt-2 space-y-1 text-[10px] text-amber-300">
                {compatibleAgents
                  .filter((item) => !item.ready)
                  .map((item) => (
                    <li key={item.id}>
                      {item.id}: {item.missing_requirements.join(", ") || "not ready"}
                    </li>
                  ))}
              </ul>
            ) : null}
          </fieldset>
        </div>

        {custom ? (
          <fieldset className="rounded-md border border-border/70 p-3">
            <legend className="px-1 text-xs text-muted-foreground">Custom scenarios</legend>
            <div className="mt-1 grid max-h-44 gap-1 overflow-auto sm:grid-cols-2">
              {eligibleCustomScenarios.map((item) => (
                <label key={item.scenario_id} className="flex gap-2 rounded px-2 py-1.5 text-xs hover:bg-muted/40">
                  <input
                    type="checkbox"
                    checked={customScenarioIds.includes(item.scenario_id)}
                    onChange={(event) =>
                      setCustomScenarioIds((current) =>
                        event.target.checked
                          ? [...current, item.scenario_id].sort()
                          : current.filter((id) => id !== item.scenario_id),
                      )
                    }
                  />
                  <span className="font-mono text-[10px]">{item.scenario_id}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}

        {suite && role && domain ? (
          <div className="grid gap-4 rounded-lg border border-border/70 bg-background/50 p-4 lg:grid-cols-[1fr_auto]">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium">
                <StepNumber value="5" /> Review evaluation
              </div>
              <p className="mt-3 text-sm">
                {domain.label} <span className="text-muted-foreground">→</span> {role.label}{" "}
                <span className="text-muted-foreground">→</span> {suite.tier}
              </p>
              <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                <Badge variant="outline">
                  {custom ? customScenarioIds.length : suite.scenario_ids.length} scenarios
                </Badge>
                <Badge variant="outline">revision {suite.revision}</Badge>
                <Badge variant="outline">world {world}</Badge>
                <Badge variant="outline">seeded per scenario</Badge>
                <Badge variant="outline">~{suite.estimated_duration_s}s</Badge>
              </div>
              <p className="mt-3 text-xs text-muted-foreground">
                Capabilities: {capabilities.map((item) => item.label).join(", ") || "None"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Risk coverage: {risks.join(", ") || "Not classified"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Budgets per scenario: {suite.default_limits.tool_calls} tool calls, {" "}
                {suite.default_limits.model_tokens.toLocaleString()} tokens, {" "}
                {suite.default_limits.mutations} mutations, {suite.default_limits.wall_time_s}s
              </p>
            </div>
            <div className="flex min-w-52 flex-col justify-between gap-3">
              <label className="text-xs text-muted-foreground">
                World
                <select
                  aria-label="Suite world"
                  value={world}
                  onChange={(event) => setWorldChoice(event.target.value as WorldName)}
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-foreground"
                >
                  {domain?.id === "insurance" ? (
                    <option value="insurance">Insurance Postgres</option>
                  ) : role?.id === "supply-chain-analyst" ? (
                    <option value="supply-chain">Supply-chain Postgres</option>
                  ) : (
                    <>
                      <option value="postgres">Commerce Postgres</option>
                      <option value="stub">Deterministic stub</option>
                    </>
                  )}
                </select>
              </label>
              <label className="text-xs text-muted-foreground">
                Concurrency
                <input
                  aria-label="Suite concurrency"
                  type="number"
                  min={1}
                  max={16}
                  value={concurrency}
                  onChange={(event) =>
                    setConcurrency(Math.min(16, Math.max(1, Number(event.target.value) || 1)))
                  }
                  className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-foreground"
                />
              </label>
              <details className="rounded-md border border-border/70 p-3 text-xs">
                <summary className="cursor-pointer font-medium text-foreground">
                  Advanced controls
                </summary>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                  <NumericControl
                    label="Seed override"
                    value={seedOverride}
                    min={0}
                    onChange={setSeedOverride}
                  />
                  <NumericControl
                    label="Suite deadline (seconds)"
                    value={deadlineOverride}
                    min={1}
                    onChange={setDeadlineOverride}
                  />
                  <NumericControl
                    label="Suite token budget"
                    value={tokenBudgetOverride}
                    min={0}
                    onChange={setTokenBudgetOverride}
                  />
                  <NumericControl
                    label="Suite mutation budget"
                    value={mutationBudgetOverride}
                    min={0}
                    onChange={setMutationBudgetOverride}
                  />
                  <NumericControl
                    label="Injections per scenario"
                    value={injectionOverride}
                    min={0}
                    onChange={setInjectionOverride}
                  />
                </div>
              </details>
              <Button
                disabled={!ready || running}
                onClick={() => {
                  const scenarioCount = custom
                    ? customScenarioIds.length
                    : suite.scenario_ids.length;
                  const limits = {
                    ...suite.default_limits,
                    injections:
                      injectionOverride === ""
                        ? suite.default_limits.injections
                        : Number(injectionOverride),
                  };
                  onStart({
                    requestKey: crypto.randomUUID(),
                    domainId: domain.id,
                    roleId: role.id,
                    suiteId: suite.id,
                    agentId: agent?.id ?? "",
                    world,
                    concurrency,
                    scenarioIds: custom ? customScenarioIds : undefined,
                    seed: seedOverride === "" ? undefined : Number(seedOverride),
                    limits,
                    budget: {
                      deadline_s:
                        deadlineOverride === ""
                          ? Math.max(30, suite.estimated_duration_s * 2)
                          : Number(deadlineOverride),
                      scenarios: scenarioCount,
                      tool_calls: limits.tool_calls * scenarioCount,
                      model_tokens:
                        tokenBudgetOverride === ""
                          ? limits.model_tokens * scenarioCount
                          : Number(tokenBudgetOverride),
                      mutations:
                        mutationBudgetOverride === ""
                          ? limits.mutations * scenarioCount
                          : Number(mutationBudgetOverride),
                      cost_minor: limits.cost_minor * scenarioCount,
                    },
                  });
                }}
              >
                {running ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Play className="size-3.5 fill-current" />
                )}
                {running ? "Starting…" : "Start evaluation suite"}
              </Button>
            </div>
          </div>
        ) : null}

        {latestJob ? (
          <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/[0.05] p-4" role="status">
            <div className="flex items-center gap-2 text-sm font-medium text-emerald-300">
              {latestJob.status === "completed" ? (
                <CheckCircle2 className="size-4" />
              ) : (
                <ShieldCheck className="size-4" />
              )}
              Suite {latestJob.status}
            </div>
            <p className="mt-2 font-mono text-[11px] text-muted-foreground">{latestJob.id}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {latestJob.completed_scenarios}/{latestJob.total_scenarios} complete · {" "}
              {latestJob.passed_scenarios} passed · {latestJob.failed_scenarios} failed
            </p>
            {!(["completed", "failed", "cancelled"] as string[]).includes(latestJob.status) ? (
              <Button
                className="mt-3"
                size="sm"
                variant="outline"
                onClick={() => onCancel(latestJob.id)}
              >
                Cancel evaluation
              </Button>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function NumericControl({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: string;
  min: number;
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-[11px] text-muted-foreground">
      {label}
      <input
        aria-label={label}
        type="number"
        min={min}
        value={value}
        placeholder="Suite default"
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-foreground"
      />
    </label>
  );
}

function StepNumber({ value }: { value: string }) {
  return (
    <span className="grid size-5 place-items-center rounded-full bg-primary/15 font-mono text-[10px] text-primary">
      {value}
    </span>
  );
}

function WizardSelect({
  step,
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  step: string;
  label: string;
  value: string;
  options: Array<{ value: string; label: string }>;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="rounded-lg border border-border/70 bg-background/45 p-3">
      <span className="flex items-center gap-2 text-xs font-medium">
        <StepNumber value={step} /> {label}
      </span>
      <select
        aria-label={`Choose ${label.toLowerCase()}`}
        className="mt-3 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
        value={value}
        disabled={disabled || options.length === 0}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((item) => (
          <option key={item.value} value={item.value}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
}
