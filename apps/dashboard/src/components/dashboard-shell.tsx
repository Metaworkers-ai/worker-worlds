"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Blocks,
  BookOpen,
  Box,
  CheckCircle2,
  ChevronDown,
  ChevronsUpDown,
  ChevronRight,
  CircleDot,
  GitCompareArrows,
  LayoutDashboard,
  LoaderCircle,
  Menu,
  Play,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  TerminalSquare,
  TriangleAlert,
  X,
  Zap,
} from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  loadDashboard,
  loadRun,
  startRun,
  type Agent,
  type Comparison,
  type DashboardData,
  type Overview,
  type RunRecord,
  type RunSummary,
  type Scenario,
} from "@/lib/dashboard-data";

type View = "overview" | "runs" | "scenarios" | "comparisons";

const navigation = [
  { id: "overview" as const, label: "Overview", icon: LayoutDashboard },
  { id: "runs" as const, label: "Runs", icon: Activity },
  { id: "scenarios" as const, label: "Scenarios", icon: BookOpen },
  { id: "comparisons" as const, label: "Comparisons", icon: GitCompareArrows },
];

function Logo() {
  return (
    <div className="flex items-center gap-3">
      <div className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground shadow-[0_0_24px_-6px_var(--primary)]">
        <Blocks className="size-4" strokeWidth={2.4} />
      </div>
      <div>
        <p className="text-sm font-semibold tracking-tight">Worker Worlds</p>
        <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          Evaluation console
        </p>
      </div>
    </div>
  );
}

function Sidebar({
  view,
  setView,
  data,
}: {
  view: View;
  setView: (view: View) => void;
  data: DashboardData | null;
}) {
  const counts: Partial<Record<View, number>> = {
    runs: data?.runs.length,
    scenarios: data?.scenarios.length,
    comparisons: data?.comparisons.length,
  };
  return (
    <div className="flex h-full flex-col bg-sidebar p-3">
      <div className="px-2 py-3">
        <Logo />
      </div>
      <p className="mt-5 px-2 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
        Workspace
      </p>
      <nav className="mt-2 space-y-1">
        {navigation.map((item) => (
          <button
            key={item.id}
            onClick={() => setView(item.id)}
            className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${view === item.id ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"}`}
          >
            <item.icon className="size-4" />
            {item.label}
            {counts[item.id] !== undefined ? (
              <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                {counts[item.id]}
              </span>
            ) : null}
          </button>
        ))}
      </nav>
      <div className="mt-auto rounded-lg border border-border/70 bg-card/50 p-3">
        <div className="flex items-center gap-2 text-xs font-medium">
          <CircleDot
            className={`size-3.5 ${data?.health.database_ready ? "text-emerald-400" : "text-amber-400"}`}
          />
          {data?.health.status === "ready" ? "System ready" : "System degraded"}
        </div>
        <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
          {data?.health.database ?? "Connecting to the local API…"}
        </p>
        <p className="mt-1 font-mono text-[10px] text-muted-foreground">
          {data ? `worker-worlds ${data.health.package_version}` : "api/v1"}
        </p>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: RunSummary["status"] }) {
  const style =
    status === "pass"
      ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
      : status === "fail"
        ? "border-red-500/20 bg-red-500/10 text-red-400"
        : "border-amber-500/20 bg-amber-500/10 text-amber-400";
  return (
    <Badge
      variant="outline"
      className={`${style} gap-1.5 font-mono text-[10px] uppercase`}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {status}
    </Badge>
  );
}

function Metric({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof Activity;
}) {
  return (
    <Card className="border-border/70 bg-card/70 shadow-none">
      <CardContent className="p-5">
        <div className="flex items-center justify-between text-muted-foreground">
          <span className="text-xs font-medium">{label}</span>
          <Icon className="size-4" />
        </div>
        <p className="mt-4 font-mono text-3xl font-medium tracking-tight">
          {value}
        </p>
        <p className="mt-2 text-[11px] text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  );
}

function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="grid min-h-56 place-items-center rounded-lg border border-dashed border-border p-8 text-center">
      <div>
        <div className="mx-auto grid size-10 place-items-center rounded-full bg-muted">
          <TerminalSquare className="size-4 text-muted-foreground" />
        </div>
        <h2 className="mt-4 text-sm font-medium">{title}</h2>
        <p className="mx-auto mt-2 max-w-sm text-xs leading-5 text-muted-foreground">
          {description}
        </p>
        {action ? <div className="mt-4">{action}</div> : null}
      </div>
    </div>
  );
}

function HealthChart({ overview }: { overview: Overview }) {
  const values = overview.recent_pass_rates.length
    ? overview.recent_pass_rates
    : [0, 0, 0, 0, 0, 0];
  return (
    <Card className="border-border/70 bg-card/70 shadow-none lg:col-span-2">
      <CardHeader>
        <CardTitle className="text-base">Evaluation health</CardTitle>
        <CardDescription>
          Pass rate derived from persisted run evidence
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex h-40 items-end gap-2 border-b border-border/60 pb-1">
          {values.map((value, index) => (
            <div key={index} className="flex h-full flex-1 items-end">
              <div
                className="w-full rounded-sm bg-primary/25 transition-colors hover:bg-primary/45"
                style={{ height: `${Math.max(4, value * 100)}%` }}
                title={`${(value * 100).toFixed(1)}%`}
              >
                <div className="h-1 rounded-t-sm bg-primary" />
              </div>
            </div>
          ))}
        </div>
        <div className="mt-3 flex justify-between font-mono text-[10px] text-muted-foreground">
          <span>Older</span>
          <span>{overview.total_runs} recorded runs</span>
          <span>Latest</span>
        </div>
      </CardContent>
    </Card>
  );
}

function RunsTable({
  runs,
  onSelect,
}: {
  runs: RunSummary[];
  onSelect: (run: RunSummary) => void;
}) {
  return (
    <div className="w-full max-w-full overflow-x-auto rounded-lg border border-border/70">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/30 hover:bg-muted/30">
            <TableHead>Scenario</TableHead>
            <TableHead>Worker</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="hidden md:table-cell">Duration</TableHead>
            <TableHead className="hidden lg:table-cell">Started</TableHead>
            <TableHead className="w-10" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((run) => (
            <TableRow
              key={run.id}
              onClick={() => onSelect(run)}
              className="cursor-pointer"
            >
              <TableCell>
                <p className="max-w-md truncate text-sm font-medium">
                  {run.scenario_name}
                </p>
                <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {run.id}
                </p>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {run.worker}
              </TableCell>
              <TableCell>
                <StatusBadge status={run.status} />
              </TableCell>
              <TableCell className="hidden font-mono text-xs text-muted-foreground md:table-cell">
                {run.duration_ms}ms
              </TableCell>
              <TableCell className="hidden text-xs text-muted-foreground lg:table-cell">
                {new Date(run.started_at).toLocaleString()}
              </TableCell>
              <TableCell>
                <ChevronRight className="size-4 text-muted-foreground" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function OverviewView({
  data,
  setView,
  onSelect,
}: {
  data: DashboardData;
  setView: (view: View) => void;
  onSelect: (run: RunSummary) => void;
}) {
  const overview = data.overview;
  const latestFailure = data.runs.find((run) => run.status !== "pass");
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">
          Local evaluation workspace
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          {overview.total_runs
            ? "Worker behavior, backed by evidence."
            : "Ready for your first real run."}
        </h1>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Pass rate"
          value={`${(overview.pass_rate * 100).toFixed(1)}%`}
          detail={`${overview.passed_runs} passed · ${overview.failed_runs} not passed`}
          icon={ShieldCheck}
        />
        <Metric
          label="Persisted runs"
          value={String(overview.total_runs)}
          detail="Canonical RunRecord artifacts"
          icon={Activity}
        />
        <Metric
          label="Critical failures"
          value={String(overview.critical_regressions)}
          detail="Across recorded verdicts"
          icon={TriangleAlert}
        />
        <Metric
          label="Median duration"
          value={`${overview.median_duration_ms}ms`}
          detail="End-to-end execution"
          icon={Zap}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <HealthChart overview={overview} />
        <Card
          className={`shadow-none ${latestFailure ? "border-red-500/20 bg-red-500/[0.04]" : "border-border/70 bg-card/70"}`}
        >
          <CardHeader>
            <div
              className={`grid size-9 place-items-center rounded-md ${latestFailure ? "bg-red-500/10 text-red-400" : "bg-emerald-500/10 text-emerald-400"}`}
            >
              {latestFailure ? (
                <TriangleAlert className="size-4" />
              ) : (
                <CheckCircle2 className="size-4" />
              )}
            </div>
          </CardHeader>
          <CardContent>
            <CardTitle className="text-base">
              {latestFailure
                ? "Failure needs inspection"
                : data.runs.length
                  ? "Latest evidence passed"
                  : "No evidence recorded yet"}
            </CardTitle>
            <CardDescription className="mt-2 leading-5">
              {latestFailure
                ? latestFailure.scenario_name
                : data.runs.length
                  ? "No failed verdicts exist in the current persisted run set."
                  : "Start an evaluation to populate this workspace with real evidence."}
            </CardDescription>
            <button
              onClick={() =>
                latestFailure ? onSelect(latestFailure) : setView("runs")
              }
              className="mt-5 flex items-center gap-1 text-xs font-medium text-primary"
            >
              {latestFailure
                ? "Inspect evidence"
                : data.runs.length
                  ? "View runs"
                  : "Start a run"}{" "}
              <ArrowRight className="size-3" />
            </button>
          </CardContent>
        </Card>
      </div>
      <Card className="border-border/70 bg-card/70 shadow-none">
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base">Recent runs</CardTitle>
            <CardDescription>
              Persisted evidence from the local API
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setView("runs")}>
            View all <ArrowRight className="size-3.5" />
          </Button>
        </CardHeader>
        <CardContent>
          {data.runs.length ? (
            <RunsTable runs={data.runs.slice(0, 5)} onSelect={onSelect} />
          ) : (
            <EmptyState
              title="No runs yet"
              description="The API is connected, but its artifact directory contains no RunRecord files."
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function humanizeFamily(family: string): string {
  return family
    .split("-")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function ScenarioPicker({
  scenarios,
  value,
  onValueChange,
}: {
  scenarios: Scenario[];
  value: string;
  onValueChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [family, setFamily] = useState("all");
  const selected = scenarios.find((scenario) => scenario.id === value);
  const families = useMemo(
    () => Array.from(new Set(scenarios.map((scenario) => scenario.family))).sort(),
    [scenarios],
  );
  const visibleScenarios =
    family === "all"
      ? scenarios
      : scenarios.filter((scenario) => scenario.family === family);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="Choose a scenario"
          className="h-auto min-h-9 w-full justify-between px-3 py-2 text-left font-normal"
        >
          {selected ? (
            <span className="min-w-0">
              <span className="block truncate text-sm text-foreground">
                {selected.objective}
              </span>
              <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">
                {selected.id} · {humanizeFamily(selected.family)}
              </span>
            </span>
          ) : (
            <span className="text-muted-foreground">Choose a scenario</span>
          )}
          <ChevronsUpDown className="ml-3 size-4 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[min(42rem,calc(100vw-2rem))] gap-0 p-0"
      >
        <Command>
          <CommandInput placeholder="Search by task, ID, family, tag, or tool…" />
          <div
            className="no-scrollbar flex gap-1 overflow-x-auto border-b px-2 py-2"
            aria-label="Filter scenarios by family"
          >
            {["all", ...families].map((item) => (
              <Button
                key={item}
                type="button"
                size="sm"
                variant={family === item ? "secondary" : "ghost"}
                className="h-7 shrink-0 px-2 text-xs"
                onClick={() => setFamily(item)}
              >
                {item === "all" ? "All" : humanizeFamily(item)}
              </Button>
            ))}
          </div>
          <CommandList className="max-h-80">
            <CommandEmpty>No matching scenarios.</CommandEmpty>
            <CommandGroup
              heading={
                family === "all"
                  ? `${scenarios.length} scenarios`
                  : humanizeFamily(family)
              }
            >
              {visibleScenarios.map((scenario) => (
                <CommandItem
                  key={scenario.id}
                  value={`${scenario.id} ${scenario.objective} ${scenario.family} ${scenario.tags.join(" ")} ${scenario.tools.join(" ")}`}
                  data-checked={scenario.id === value}
                  className="items-start py-2.5"
                  onSelect={() => {
                    onValueChange(scenario.id);
                    setOpen(false);
                  }}
                >
                  <span className="min-w-0 pr-6">
                    <span className="block text-sm leading-snug">
                      {scenario.objective}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
                      <Badge variant="outline" className="px-1.5 py-0 text-[9px]">
                        {humanizeFamily(scenario.family)}
                      </Badge>
                      <span className="font-mono">{scenario.id}</span>
                    </span>
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function AgentPicker({
  agents,
  value,
  onValueChange,
}: {
  agents: Agent[];
  value: string;
  onValueChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selected = agents.find((agent) => agent.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="Choose a registered agent"
          className="h-auto min-h-9 w-full justify-between px-3 py-2 text-left font-normal"
        >
          {selected ? (
            <span className="min-w-0">
              <span className="flex items-center gap-2 text-sm text-foreground">
                <span className="truncate">{selected.id}</span>
                <Badge
                  variant="outline"
                  className={
                    selected.ready
                      ? "border-emerald-500/20 text-emerald-400"
                      : "border-amber-500/20 text-amber-400"
                  }
                >
                  {selected.ready ? "Ready" : "Unavailable"}
                </Badge>
              </span>
              <span className="mt-0.5 block truncate font-mono text-[10px] text-muted-foreground">
                {selected.adapter} · {selected.model_provider ?? "local"}/
                {selected.model_name ?? "deterministic"}
              </span>
            </span>
          ) : (
            <span className="text-muted-foreground">Choose an agent</span>
          )}
          <ChevronsUpDown className="ml-3 size-4 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-[min(36rem,calc(100vw-2rem))] p-0"
      >
        <Command>
          <CommandInput placeholder="Search by agent, adapter, provider, or model…" />
          <CommandList className="max-h-80">
            <CommandEmpty>No matching agents.</CommandEmpty>
            <CommandGroup heading={`${agents.length} registered agents`}>
              {agents.map((agent) => (
                <CommandItem
                  key={agent.id}
                  value={`${agent.id} ${agent.adapter} ${agent.model_provider ?? ""} ${agent.model_name ?? ""}`}
                  disabled={!agent.ready}
                  aria-disabled={!agent.ready}
                  className="items-start py-2.5"
                  onSelect={() => {
                    if (!agent.ready) return;
                    onValueChange(agent.id);
                    setOpen(false);
                  }}
                >
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2 text-sm">
                      {agent.id}
                      <Badge
                        variant="outline"
                        className={
                          agent.ready
                            ? "border-emerald-500/20 text-emerald-400"
                            : "border-amber-500/20 text-amber-400"
                        }
                      >
                        {agent.ready ? "Ready" : "Unavailable"}
                      </Badge>
                    </span>
                    <span className="mt-1 block font-mono text-[10px] text-muted-foreground">
                      {agent.adapter} {agent.version} · {agent.model_provider ?? "local"}/
                      {agent.model_name ?? "deterministic"}
                    </span>
                    {agent.deterministic_test_infrastructure ? (
                      <span className="mt-1 block text-[10px] text-sky-300">
                        Deterministic test infrastructure — no provider call
                      </span>
                    ) : null}
                    {!agent.ready && agent.missing_requirements.length ? (
                      <span className="mt-1 block text-[10px] text-amber-300">
                        Unavailable because: {agent.missing_requirements.join("; ")}
                      </span>
                    ) : null}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function RunsView({
  runs,
  scenarios,
  agents,
  running,
  onRun,
  onSelect,
}: {
  runs: RunSummary[];
  scenarios: Scenario[];
  agents: Agent[];
  running: boolean;
  onRun: (
    scenarioId: string,
    agentId: string,
    world: "stub" | "postgres",
  ) => void;
  onSelect: (run: RunSummary) => void;
}) {
  const [query, setQuery] = useState("");
  const [scenarioId, setScenarioId] = useState("");
  const [agentId, setAgentId] = useState("");
  const [world, setWorld] = useState<"stub" | "postgres">("postgres");
  const preferred =
    scenarios.find((item) => item.id === "refund.partial.happy")?.id ??
    scenarios[0]?.id ??
    "";
  const selected = scenarioId || preferred;
  const preferredAgent = agents.find((agent) => agent.ready)?.id ?? "";
  const selectedAgentId = agentId || preferredAgent;
  const selectedAgent = agents.find((agent) => agent.id === selectedAgentId);
  const filtered = useMemo(
    () =>
      runs.filter((run) =>
        `${run.scenario_name} ${run.worker} ${run.id}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [query, runs],
  );
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">Execution history</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">Runs</h1>
      </div>
      <Card className="border-primary/20 bg-primary/[0.03] shadow-none">
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <div className="min-w-64 flex-1">
            <p className="mb-2 text-xs text-muted-foreground">Scenario</p>
            <ScenarioPicker
              scenarios={scenarios}
              value={selected}
              onValueChange={setScenarioId}
            />
          </div>
          <div className="min-w-64 flex-1">
            <p className="mb-2 text-xs text-muted-foreground">Agent</p>
            <AgentPicker
              agents={agents}
              value={selectedAgentId}
              onValueChange={setAgentId}
            />
          </div>
          <div>
            <p className="mb-2 text-xs text-muted-foreground">World</p>
            <Select
              value={world}
              onValueChange={(value) => setWorld(value as "stub" | "postgres")}
            >
              <SelectTrigger className="w-32" aria-label="Choose a world">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="postgres">Postgres</SelectItem>
                <SelectItem value="stub">In-memory</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button
            disabled={!selected || !selectedAgent?.ready || running}
            onClick={() => onRun(selected, selectedAgentId, world)}
          >
            {running ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Play className="size-3.5 fill-current" />
            )}
            {running ? "Running…" : "Start run"}
          </Button>
        </CardContent>
      </Card>
      {selectedAgent?.deterministic_test_infrastructure ? (
        <p className="text-xs text-sky-300" role="status">
          Selected agent is deterministic test infrastructure and does not call a provider.
        </p>
      ) : null}
      {!agents.some((agent) => agent.ready) ? (
        <p className="text-xs text-amber-300" role="status">
          No registered agent is ready. Resolve the displayed requirements before starting a run.
        </p>
      ) : null}
      <div className="relative">
        <Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="pl-9"
          placeholder="Search real runs"
          aria-label="Search runs"
        />
      </div>
      {filtered.length ? (
        <RunsTable runs={filtered} onSelect={onSelect} />
      ) : (
        <EmptyState
          title="No matching runs"
          description={
            runs.length
              ? "Try another search term."
              : "Choose a scenario above to create the first persisted run."
          }
        />
      )}
    </div>
  );
}

function ScenariosView({ scenarios }: { scenarios: Scenario[] }) {
  const [expandedFamily, setExpandedFamily] = useState<string | null>(null);
  const groups = useMemo(() => {
    const map = new Map<string, Scenario[]>();
    for (const scenario of scenarios)
      map.set(scenario.family, [...(map.get(scenario.family) ?? []), scenario]);
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [scenarios]);
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">Validated YAML library</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          {scenarios.length} available scenarios
        </h1>
      </div>
      {groups.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {groups.map(([family, items]) => (
            <Card
              key={family}
              className={`border-border/70 bg-card/70 shadow-none transition-colors ${expandedFamily === family ? "border-primary/35" : ""}`}
            >
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div className="grid size-9 place-items-center rounded-md bg-muted">
                    <Box className="size-4 text-muted-foreground" />
                  </div>
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {items[0]?.severity}
                  </Badge>
                </div>
                <CardTitle className="pt-3 text-base">
                  {humanizeFamily(family)}
                </CardTitle>
                <Button
                  variant="link"
                  className="h-auto w-fit justify-start p-0 text-sm text-muted-foreground hover:text-foreground"
                  aria-expanded={expandedFamily === family}
                  aria-controls={`scenario-family-${family}`}
                  onClick={() =>
                    setExpandedFamily((current) =>
                      current === family ? null : family,
                    )
                  }
                >
                  {items.length} validated scenarios
                  <ChevronDown
                    className={`size-3.5 transition-transform ${expandedFamily === family ? "rotate-180" : ""}`}
                  />
                </Button>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Loaded by API</span>
                  <span className="font-mono text-emerald-400">100%</span>
                </div>
                <Progress value={100} className="mt-2 h-1.5" />
                <p className="mt-4 truncate font-mono text-[10px] text-muted-foreground">
                  {items[0]?.source}
                </p>
                {expandedFamily === family ? (
                  <div
                    id={`scenario-family-${family}`}
                    className="mt-4 max-h-72 space-y-2 overflow-y-auto border-t pt-3"
                  >
                    {items.map((scenario) => (
                      <div
                        key={scenario.id}
                        className="rounded-md border border-border/60 bg-background/40 p-2.5"
                      >
                        <p className="text-xs leading-relaxed text-foreground">
                          {scenario.objective}
                        </p>
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                          <span className="font-mono text-[9px] text-muted-foreground">
                            {scenario.id}
                          </span>
                          <Badge
                            variant="outline"
                            className="px-1.5 py-0 text-[8px]"
                          >
                            {scenario.review_status.replaceAll("_", " ")}
                          </Badge>
                          {scenario.tools.slice(0, 2).map((tool) => (
                            <Badge
                              key={tool}
                              variant="secondary"
                              className="px-1.5 py-0 font-mono text-[8px]"
                            >
                              {tool}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No scenarios found"
          description="Set WORKER_WORLDS_SCENARIO_DIR to a directory containing valid Worker Worlds YAML."
        />
      )}
    </div>
  );
}

function ComparisonsView({ comparisons }: { comparisons: Comparison[] }) {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">
          Behavioral release gates
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Comparisons
        </h1>
      </div>
      {comparisons.length ? (
        comparisons.map((comparison) => (
          <Card
            key={comparison.id}
            className={
              comparison.gate === "fail"
                ? "border-red-500/20 bg-card/70 shadow-none"
                : "border-emerald-500/20 bg-card/70 shadow-none"
            }
          >
            <CardHeader className="flex-row items-start justify-between">
              <div>
                <CardTitle className="text-base">
                  {comparison.baseline_worker} → {comparison.candidate_worker}
                </CardTitle>
                <CardDescription className="mt-1 font-mono text-xs">
                  {comparison.id}
                </CardDescription>
              </div>
              <Badge
                className={
                  comparison.gate === "fail"
                    ? "bg-red-500/10 text-red-400"
                    : "bg-emerald-500/10 text-emerald-400"
                }
              >
                {comparison.gate}
              </Badge>
            </CardHeader>
            <CardContent className="grid gap-3 sm:grid-cols-3">
              <Metric
                label="Pass-rate delta"
                value={`${(comparison.pass_rate_delta * 100).toFixed(1)}%`}
                detail="Candidate vs baseline"
                icon={Activity}
              />
              <Metric
                label="New critical"
                value={String(comparison.new_critical)}
                detail="Blocking findings"
                icon={TriangleAlert}
              />
              <Metric
                label="New high"
                value={String(comparison.new_high)}
                detail="High-severity findings"
                icon={AlertCircle}
              />
            </CardContent>
          </Card>
        ))
      ) : (
        <EmptyState
          title="No comparisons found"
          description="Comparison artifacts will appear after a baseline and candidate suite are evaluated beneath the API artifact directory."
        />
      )}
    </div>
  );
}

function RunDetail({
  summary,
  record,
  loading,
  close,
}: {
  summary: RunSummary;
  record: RunRecord | null;
  loading: boolean;
  close: () => void;
}) {
  const providerTurn = record?.turns.findLast(
    (turn) =>
      Boolean(turn.model_provider) ||
      Boolean(turn.provider_response_ids?.length) ||
      Boolean(turn.provider_request_ids?.length),
  );
  const responseIds = record?.turns.flatMap(
    (turn) => turn.provider_response_ids ?? [],
  );
  const requestIds = record?.turns.flatMap(
    (turn) => turn.provider_request_ids ?? [],
  );
  const retries = record?.turns.reduce(
    (total, turn) => total + (turn.provider_retry_count ?? 0),
    0,
  );
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/55 backdrop-blur-sm"
      onClick={close}
    >
      <aside
        className="h-full w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-6 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <StatusBadge status={summary.status} />
            <h2 className="mt-3 text-xl font-semibold">
              {summary.scenario_name}
            </h2>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {summary.id}
            </p>
          </div>
          <Button
            size="icon"
            variant="ghost"
            onClick={close}
            aria-label="Close run details"
          >
            <X className="size-4" />
          </Button>
        </div>
        {loading ? (
          <div className="grid h-72 place-items-center">
            <LoaderCircle className="size-6 animate-spin text-primary" />
          </div>
        ) : record ? (
          <div className="mt-7 space-y-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Metric
                label="Adapter"
                value={summary.worker}
                detail={
                  providerTurn?.model_provider
                    ? `${providerTurn.model_provider}/${providerTurn.model_name ?? "unknown"}`
                    : summary.worker === "stub"
                      ? "Deterministic test infrastructure"
                      : "Provider metadata unavailable"
                }
                icon={Blocks}
              />
              <Metric
                label="Tokens"
                value={record.model_tokens?.toLocaleString() ?? "Unsupported"}
                detail="Normalized model usage"
                icon={Zap}
              />
              <Metric
                label="Retries"
                value={String(retries ?? 0)}
                detail="Provider retry provenance"
                icon={RefreshCw}
              />
              <Metric
                label="Cost"
                value={
                  record.cost_minor === null
                    ? "Unsupported"
                    : `${record.cost_minor} minor units`
                }
                detail="Recorded provider cost"
                icon={Activity}
              />
            </div>
            <div className="rounded-md border border-border bg-card p-4">
              <p className="text-xs font-medium">Provider provenance</p>
              <dl className="mt-3 grid gap-3 text-[10px] sm:grid-cols-2">
                <div>
                  <dt className="text-muted-foreground">Response IDs</dt>
                  <dd className="mt-1 break-all font-mono">
                    {responseIds?.length ? responseIds.join(", ") : "Not provided"}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Request IDs</dt>
                  <dd className="mt-1 break-all font-mono">
                    {requestIds?.length ? requestIds.join(", ") : "Not provided"}
                  </dd>
                </div>
              </dl>
            </div>
            <Tabs defaultValue="evidence">
            <TabsList>
              <TabsTrigger value="evidence">Evidence</TabsTrigger>
              <TabsTrigger value="events">
                Events ({record.events.length})
              </TabsTrigger>
              <TabsTrigger value="record">Record</TabsTrigger>
            </TabsList>
            <TabsContent value="evidence" className="mt-5 space-y-3">
              {record.verdicts.map((verdict) => (
                <div
                  key={verdict.id}
                  className="flex gap-3 rounded-md border border-border bg-card p-4"
                >
                  <div
                    className={`grid size-7 shrink-0 place-items-center rounded-full ${verdict.status === "pass" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}
                  >
                    {verdict.status === "pass" ? (
                      <CheckCircle2 className="size-3.5" />
                    ) : (
                      <TriangleAlert className="size-3.5" />
                    )}
                  </div>
                  <div>
                    <p className="text-sm font-medium">
                      {verdict.assertion_id}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      {verdict.message}
                    </p>
                    <p className="mt-2 font-mono text-[10px] text-muted-foreground">
                      {verdict.severity} · {verdict.reason_code}
                    </p>
                  </div>
                </div>
              ))}
            </TabsContent>
            <TabsContent value="events" className="mt-5 space-y-3">
              {record.events.length ? (
                record.events.map((event) => (
                  <div
                    key={event.id}
                    className="rounded-md border border-border bg-card p-4"
                  >
                    <div className="flex justify-between gap-3">
                      <p className="font-mono text-xs text-primary">
                        {event.sequence}. {event.event_type}
                      </p>
                      <p className="font-mono text-[10px] text-muted-foreground">
                        {event.entity_type}:{event.entity_id}
                      </p>
                    </div>
                  </div>
                ))
              ) : (
                <EmptyState
                  title="No mutation events"
                  description="This run completed without changing world state."
                />
              )}
            </TabsContent>
            <TabsContent value="record" className="mt-5">
              <pre className="max-h-[65vh] overflow-auto rounded-md border border-border bg-card p-4 font-mono text-[10px] leading-5 text-muted-foreground">
                {JSON.stringify(record, null, 2)}
              </pre>
            </TabsContent>
            </Tabs>
          </div>
        ) : (
          <EmptyState
            title="Run detail unavailable"
            description="The summary exists, but its canonical RunRecord could not be loaded."
          />
        )}
      </aside>
    </div>
  );
}

export function DashboardShell() {
  const [view, setView] = useState<View>("overview");
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [selected, setSelected] = useState<RunSummary | null>(null);
  const [record, setRecord] = useState<RunRecord | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await loadDashboard());
      setError(null);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not connect to Worker Worlds API",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    loadDashboard()
      .then((result) => {
        if (active) {
          setData(result);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not connect to Worker Worlds API",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const selectRun = useCallback(async (summary: RunSummary) => {
    setSelected(summary);
    setRecord(null);
    setDetailLoading(true);
    try {
      setRecord(await loadRun(summary.id));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Run detail could not be loaded",
      );
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const runScenario = useCallback(
    async (
      scenarioId: string,
      agentId: string,
      world: "stub" | "postgres",
    ) => {
      setRunning(true);
      setError(null);
      try {
        await startRun(scenarioId, agentId, world);
        await refresh();
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Evaluation failed to start",
        );
      } finally {
        setRunning(false);
      }
    },
    [refresh],
  );

  return (
    <div className="dark min-h-screen bg-background text-foreground">
      <div className="grid min-h-screen lg:grid-cols-[232px_1fr]">
        <aside className="hidden border-r border-border lg:block">
          <Sidebar view={view} setView={setView} data={data} />
        </aside>
        <div className="min-w-0">
          <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border/70 bg-background/90 px-4 backdrop-blur-xl md:px-7">
            <Sheet>
              <SheetTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="lg:hidden"
                  aria-label="Open navigation"
                >
                  <Menu className="size-4" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-[250px] p-0">
                <Sidebar view={view} setView={setView} data={data} />
              </SheetContent>
            </Sheet>
            <div className="lg:hidden">
              <Logo />
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Badge
                variant="outline"
                className={`hidden gap-1.5 sm:flex ${data?.health.database_ready ? "border-emerald-500/20 text-emerald-400" : "border-amber-500/20 text-amber-400"}`}
              >
                <span className="size-1.5 rounded-full bg-current" />
                {data?.health.database_ready ? "API connected" : "API degraded"}
              </Badge>
              <Button size="sm" onClick={() => setView("runs")}>
                <Play className="size-3 fill-current" />
                Run evaluation
              </Button>
              <Button
                size="icon"
                variant="ghost"
                onClick={() => void refresh()}
                aria-label="Refresh dashboard"
              >
                <RefreshCw
                  className={`size-4 ${loading ? "animate-spin" : ""}`}
                />
              </Button>
              <Avatar className="hidden size-8 sm:flex">
                <AvatarFallback className="bg-primary/15 text-xs text-primary">
                  WW
                </AvatarFallback>
              </Avatar>
            </div>
          </header>
          <main className="mx-auto max-w-[1440px] p-4 md:p-7">
            {error ? (
              <div
                className="mb-5 flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/[0.06] p-4 text-sm"
                role="alert"
                aria-live="assertive"
              >
                <AlertCircle className="mt-0.5 size-4 shrink-0 text-red-400" />
                <div>
                  <p className="font-medium text-red-300">
                    Worker Worlds API error
                  </p>
                  <p className="mt-1 text-xs text-red-300/75">{error}</p>
                  <p className="mt-2 font-mono text-[10px] text-muted-foreground">
                    Start with: worker-worlds-api
                  </p>
                </div>
              </div>
            ) : null}
            {loading && !data ? (
              <div className="grid min-h-[70vh] place-items-center">
                <div className="text-center">
                  <LoaderCircle className="mx-auto size-7 animate-spin text-primary" />
                  <p className="mt-3 text-xs text-muted-foreground">
                    Loading real Worker Worlds evidence…
                  </p>
                </div>
              </div>
            ) : data ? (
              <>
                {view === "overview" ? (
                  <OverviewView
                    data={data}
                    setView={setView}
                    onSelect={(run) => void selectRun(run)}
                  />
                ) : null}
                {view === "runs" ? (
                  <RunsView
                    runs={data.runs}
                    scenarios={data.scenarios}
                    agents={data.agents}
                    running={running}
                    onRun={(scenarioId, agentId, world) =>
                      void runScenario(scenarioId, agentId, world)
                    }
                    onSelect={(run) => void selectRun(run)}
                  />
                ) : null}
                {view === "scenarios" ? (
                  <ScenariosView scenarios={data.scenarios} />
                ) : null}
                {view === "comparisons" ? (
                  <ComparisonsView comparisons={data.comparisons} />
                ) : null}
              </>
            ) : (
              <EmptyState
                title="API not connected"
                description="Start worker-worlds-api on port 8000, then refresh this page."
                action={
                  <Button variant="outline" onClick={() => void refresh()}>
                    <Server className="size-4" />
                    Retry connection
                  </Button>
                }
              />
            )}
          </main>
        </div>
      </div>
      {selected ? (
        <RunDetail
          summary={selected}
          record={record}
          loading={detailLoading}
          close={() => {
            setSelected(null);
            setRecord(null);
          }}
        />
      ) : null}
    </div>
  );
}
