"use client";

import { useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Blocks,
  BookOpen,
  Box,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Code2,
  Command,
  GitCompareArrows,
  LayoutDashboard,
  Menu,
  MoreHorizontal,
  Play,
  Search,
  Settings,
  ShieldCheck,
  TerminalSquare,
  TriangleAlert,
  X,
  Zap,
} from "lucide-react";

import {
  activity,
  families,
  runs,
  type Run,
  type RunStatus,
} from "@/lib/dashboard-data";
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
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

type View = "overview" | "runs" | "scenarios" | "comparisons";

const navigation = [
  { id: "overview" as const, label: "Overview", icon: LayoutDashboard },
  { id: "runs" as const, label: "Runs", icon: Activity, count: "24" },
  {
    id: "scenarios" as const,
    label: "Scenarios",
    icon: BookOpen,
    count: "200",
  },
  {
    id: "comparisons" as const,
    label: "Comparisons",
    icon: GitCompareArrows,
    count: "1",
  },
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
}: {
  view: View;
  setView: (view: View) => void;
}) {
  return (
    <div className="flex h-full flex-col bg-sidebar p-3">
      <div className="px-2 py-3">
        <Logo />
      </div>
      <div className="mt-5 px-2 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
        Workspace
      </div>
      <nav className="mt-2 space-y-1">
        {navigation.map((item) => (
          <button
            key={item.id}
            onClick={() => setView(item.id)}
            className={`flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${view === item.id ? "bg-sidebar-accent text-sidebar-accent-foreground" : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"}`}
          >
            <item.icon className="size-4" />
            <span>{item.label}</span>
            {item.count && (
              <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                {item.count}
              </span>
            )}
          </button>
        ))}
      </nav>
      <div className="mt-6 px-2 text-[10px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
        Developer
      </div>
      <nav className="mt-2 space-y-1">
        {[
          { label: "Worlds", icon: Box },
          { label: "Adapters", icon: Code2 },
          { label: "Settings", icon: Settings },
        ].map((item) => (
          <button
            key={item.label}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent/60 hover:text-foreground"
          >
            <item.icon className="size-4" />
            {item.label}
          </button>
        ))}
      </nav>
      <div className="mt-auto rounded-lg border border-border/70 bg-card/50 p-3">
        <div className="flex items-center gap-2 text-xs font-medium">
          <CircleDot className="size-3.5 text-emerald-400" /> Preview ready
        </div>
        <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
          Contract-shaped demo evidence
        </p>
        <p className="font-mono text-[10px] text-muted-foreground">
          API connection pending
        </p>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: RunStatus }) {
  const styles =
    status === "pass"
      ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400"
      : status === "fail"
        ? "border-red-500/20 bg-red-500/10 text-red-400"
        : "border-amber-500/20 bg-amber-500/10 text-amber-400";
  return (
    <Badge
      variant="outline"
      className={`${styles} gap-1.5 font-mono text-[10px] uppercase`}
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
  trend,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail: string;
  trend?: "up" | "down";
  icon: typeof Activity;
}) {
  return (
    <Card className="border-border/70 bg-card/70 shadow-none">
      <CardContent className="p-5">
        <div className="flex items-center justify-between text-muted-foreground">
          <span className="text-xs font-medium">{label}</span>
          <Icon className="size-4" />
        </div>
        <div className="mt-4 flex items-end justify-between">
          <p className="font-mono text-3xl font-medium tracking-tight">
            {value}
          </p>
          <p
            className={`flex items-center text-[11px] ${trend === "down" ? "text-emerald-400" : trend === "up" ? "text-emerald-400" : "text-muted-foreground"}`}
          >
            {trend === "up" && <ArrowUpRight className="size-3" />}
            {trend === "down" && <ArrowDownRight className="size-3" />}
            {detail}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function ActivityChart() {
  return (
    <Card className="border-border/70 bg-card/70 shadow-none lg:col-span-2">
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div>
          <CardTitle className="text-base">Evaluation health</CardTitle>
          <CardDescription>Pass rate across the last 12 suites</CardDescription>
        </div>
        <Select defaultValue="30">
          <SelectTrigger className="h-8 w-[110px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="7">7 days</SelectItem>
            <SelectItem value="30">30 days</SelectItem>
            <SelectItem value="90">90 days</SelectItem>
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent>
        <div className="flex h-40 items-end gap-2 border-b border-border/60 pb-1">
          {activity.map((value, index) => (
            <div
              key={index}
              className="group relative flex h-full flex-1 items-end"
            >
              <div
                className="w-full rounded-sm bg-primary/20 transition-colors group-hover:bg-primary/50"
                style={{ height: `${value}%` }}
              >
                <div className="h-1 rounded-t-sm bg-primary" />
              </div>
            </div>
          ))}
        </div>
        <div className="mt-3 flex justify-between font-mono text-[10px] text-muted-foreground">
          <span>Jul 22</span>
          <span>Aug 01</span>
          <span>Aug 19</span>
        </div>
      </CardContent>
    </Card>
  );
}

function RegressionCard({ setView }: { setView: (view: View) => void }) {
  return (
    <Card className="border-red-500/20 bg-red-500/[0.04] shadow-none">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="grid size-9 place-items-center rounded-md bg-red-500/10">
            <TriangleAlert className="size-4 text-red-400" />
          </div>
          <Badge variant="outline" className="border-red-500/20 text-red-400">
            Critical
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <CardTitle className="text-base">New regression detected</CardTitle>
        <CardDescription className="mt-2 leading-5">
          support-agent-v2 reused an idempotency key with conflicting refund
          input.
        </CardDescription>
        <button
          onClick={() => setView("comparisons")}
          className="mt-5 flex items-center gap-1 text-xs font-medium text-red-400 hover:text-red-300"
        >
          Inspect comparison <ArrowRight className="size-3" />
        </button>
      </CardContent>
    </Card>
  );
}

function RunsTable({
  onSelect,
  compact = false,
  data = runs,
}: {
  onSelect: (run: Run) => void;
  compact?: boolean;
  data?: Run[];
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border/70">
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
          {data.slice(0, compact ? 4 : data.length).map((run) => (
            <TableRow
              key={run.id}
              onClick={() => onSelect(run)}
              className="cursor-pointer"
            >
              <TableCell>
                <p className="text-sm font-medium">{run.scenario}</p>
                <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {run.id}
                </p>
              </TableCell>
              <TableCell>
                <span className="text-xs text-muted-foreground">
                  {run.worker}
                </span>
              </TableCell>
              <TableCell>
                <StatusBadge status={run.status} />
              </TableCell>
              <TableCell className="hidden font-mono text-xs text-muted-foreground md:table-cell">
                {run.duration}
              </TableCell>
              <TableCell className="hidden text-xs text-muted-foreground lg:table-cell">
                {run.started}
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

function Overview({
  setView,
  onSelect,
}: {
  setView: (view: View) => void;
  onSelect: (run: Run) => void;
}) {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">Good morning, Piyush</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Your workers are behaving safely.
        </h1>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Pass rate"
          value="98.7%"
          detail="2.4%"
          trend="up"
          icon={ShieldCheck}
        />
        <Metric
          label="Runs this month"
          value="2,418"
          detail="12%"
          trend="up"
          icon={Activity}
        />
        <Metric
          label="Critical regressions"
          value="1"
          detail="3 fixed"
          trend="down"
          icon={TriangleAlert}
        />
        <Metric
          label="Median duration"
          value="1.2s"
          detail="180ms"
          trend="down"
          icon={Zap}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <ActivityChart />
        <RegressionCard setView={setView} />
      </div>
      <Card className="border-border/70 bg-card/70 shadow-none">
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle className="text-base">Recent runs</CardTitle>
            <CardDescription>
              Latest evidence from your evaluation workspace
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setView("runs")}>
            View all <ArrowRight className="size-3.5" />
          </Button>
        </CardHeader>
        <CardContent>
          <RunsTable compact onSelect={onSelect} />
        </CardContent>
      </Card>
    </div>
  );
}

function RunsView({ onSelect }: { onSelect: (run: Run) => void }) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () =>
      runs.filter((run) =>
        `${run.scenario} ${run.worker} ${run.id}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [query],
  );
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">Execution history</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Runs</h1>
        </div>
        <Button>
          <Play className="size-3.5 fill-current" /> New run
        </Button>
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="relative min-w-64 flex-1">
          <Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="pl-9"
            placeholder="Search scenarios, workers, or run IDs"
            aria-label="Search runs"
          />
        </div>
        <Select defaultValue="all">
          <SelectTrigger className="w-32" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All status</SelectItem>
            <SelectItem value="pass">Passed</SelectItem>
            <SelectItem value="fail">Failed</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline">
          <Command className="size-4" /> Filters
        </Button>
      </div>
      <RunsTable onSelect={onSelect} data={filtered} />
      <p className="text-xs text-muted-foreground">
        Showing {filtered.length} of 2,418 runs · retained for 90 days
      </p>
    </div>
  );
}

function ScenariosView() {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">
            Deterministic release library
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            200 commerce scenarios
          </h1>
        </div>
        <Button variant="outline">
          <BookOpen className="size-4" /> Review package
        </Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {families.map((family) => (
          <Card
            key={family.name}
            className="group border-border/70 bg-card/70 shadow-none transition-colors hover:border-primary/30"
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="grid size-9 place-items-center rounded-md bg-muted">
                  <Box className="size-4 text-muted-foreground" />
                </div>
                <Badge variant="secondary" className="font-mono text-[10px]">
                  {family.risk}
                </Badge>
              </div>
              <CardTitle className="pt-3 text-base">{family.name}</CardTitle>
              <CardDescription>
                {family.count} independently readable scenarios
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Risk coverage</span>
                <span className="font-mono text-emerald-400">
                  {family.coverage}%
                </span>
              </div>
              <Progress value={family.coverage} className="mt-2 h-1.5" />
              <div className="mt-5 flex items-center justify-between text-xs text-muted-foreground">
                <span>Domain review pending</span>
                <ChevronRight className="size-4 transition-transform group-hover:translate-x-0.5" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

function ComparisonsView() {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-muted-foreground">Behavioral release gate</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Worker comparison
        </h1>
      </div>
      <Card className="border-red-500/20 bg-card/70 shadow-none">
        <CardHeader className="flex-row items-start justify-between">
          <div>
            <CardTitle className="text-lg">
              support-agent-v1 → support-agent-v2
            </CardTitle>
            <CardDescription className="mt-1 font-mono text-xs">
              comparison_0000000000YJ28VZKK
            </CardDescription>
          </div>
          <Badge className="bg-red-500/10 text-red-400 hover:bg-red-500/10">
            Gate failed
          </Badge>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            <Metric
              label="Pass-rate delta"
              value="−0.5%"
              detail="candidate"
              icon={ArrowDownRight}
            />
            <Metric
              label="New critical"
              value="1"
              detail="blocking"
              icon={TriangleAlert}
            />
            <Metric
              label="Fixed failures"
              value="3"
              detail="improved"
              icon={CheckCircle2}
            />
            <Metric
              label="Sample size"
              value="200"
              detail="runs"
              icon={Blocks}
            />
          </div>
          <Separator className="my-6" />
          <div className="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
            <div>
              <h3 className="text-sm font-medium">Release decision</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                The candidate introduced one previously unseen critical outcome
                signature. Sample size cannot override a critical safety
                regression.
              </p>
              <div className="mt-4 rounded-md border border-red-500/20 bg-red-500/[0.05] p-3 text-xs text-red-300">
                <TriangleAlert className="mr-2 inline size-3.5" />
                Do not deploy candidate worker
              </div>
            </div>
            <div>
              <h3 className="text-sm font-medium">New evidence signature</h3>
              <div className="mt-3 rounded-md border border-border bg-background/70 p-4 font-mono text-xs">
                <div className="flex gap-3">
                  <span className="text-red-400">FAIL</span>
                  <span>refund.idempotency.conflict</span>
                </div>
                <p className="mt-3 text-muted-foreground">
                  Different input reused request key refund-customer-001
                </p>
                <div className="mt-4 grid grid-cols-2 gap-3 text-muted-foreground">
                  <span>Baseline: no mutation</span>
                  <span>Candidate: duplicate attempt</span>
                </div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function RunDetail({ run, close }: { run: Run; close: () => void }) {
  const failed = run.status === "fail";
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
            <StatusBadge status={run.status} />
            <h2 className="mt-3 text-xl font-semibold">{run.scenario}</h2>
            <p className="mt-1 font-mono text-xs text-muted-foreground">
              {run.id}
            </p>
          </div>
          <Button size="icon" variant="ghost" onClick={close} aria-label="Close run details">
            <X className="size-4" />
          </Button>
        </div>
        <div className="mt-6 grid grid-cols-3 gap-3">
          {[
            ["Worker", run.worker],
            ["Duration", run.duration],
            ["Tool calls", String(run.tools)],
          ].map(([label, value]) => (
            <div
              key={label}
              className="rounded-md border border-border bg-card p-3"
            >
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {label}
              </p>
              <p className="mt-2 truncate font-mono text-xs">{value}</p>
            </div>
          ))}
        </div>
        <Tabs defaultValue="evidence" className="mt-7">
          <TabsList>
            <TabsTrigger value="evidence">Evidence</TabsTrigger>
            <TabsTrigger value="state">State diff</TabsTrigger>
            <TabsTrigger value="record">Run record</TabsTrigger>
          </TabsList>
          <TabsContent value="evidence" className="mt-5">
            <div className="space-y-0">
              {[
                {
                  icon: Search,
                  title: "get_order",
                  note: "order_01M0C8 · authorized",
                  time: "0ms",
                  ok: true,
                },
                {
                  icon: ShieldCheck,
                  title: "Authorization evaluated",
                  note: "customer owns order · refund:write",
                  time: "132ms",
                  ok: true,
                },
                {
                  icon: failed ? TriangleAlert : Check,
                  title: "issue_refund",
                  note: failed
                    ? "Idempotency key reused with conflicting input"
                    : "2,500 USD minor units · committed atomically",
                  time: "418ms",
                  ok: !failed,
                },
                {
                  icon: failed ? X : CheckCircle2,
                  title: failed
                    ? "Critical assertion failed"
                    : "Evidence complete",
                  note: failed
                    ? "No successful mutation event was appended"
                    : "State and event assertions passed",
                  time: run.duration,
                  ok: !failed,
                },
              ].map((item, index) => (
                <div key={item.title} className="relative flex gap-4 pb-6">
                  <div
                    className={`relative z-10 grid size-8 shrink-0 place-items-center rounded-full border ${item.ok ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400" : "border-red-500/30 bg-red-500/10 text-red-400"}`}
                  >
                    <item.icon className="size-3.5" />
                  </div>
                  {index < 3 && (
                    <div className="absolute left-4 top-8 h-[calc(100%-2rem)] border-l border-border" />
                  )}
                  <div className="flex-1 pt-1">
                    <div className="flex justify-between gap-4">
                      <p className="text-sm font-medium">{item.title}</p>
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {item.time}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {item.note}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </TabsContent>
          <TabsContent value="state" className="mt-5">
            <div className="rounded-md border border-border bg-card p-4 font-mono text-xs leading-6">
              <p className="text-muted-foreground">orders.order_01M0C8</p>
              <p className="text-red-400">− refunded_minor: 0</p>
              <p className="text-emerald-400">
                + refunded_minor: {failed ? "0" : "2500"}
              </p>
              <p className="text-red-400">− refundable_minor: 10000</p>
              <p className="text-emerald-400">
                + refundable_minor: {failed ? "10000" : "7500"}
              </p>
            </div>
          </TabsContent>
          <TabsContent value="record" className="mt-5">
            <pre className="overflow-x-auto rounded-md border border-border bg-card p-4 font-mono text-[11px] leading-5 text-muted-foreground">
              {JSON.stringify(
                {
                  schema_version: "1.0",
                  id: run.id,
                  status: run.status,
                  terminal_reason: run.reason
                    .toLowerCase()
                    .replaceAll(" ", "_"),
                  evidence_complete: true,
                },
                null,
                2,
              )}
            </pre>
          </TabsContent>
        </Tabs>
      </aside>
    </div>
  );
}

export function DashboardShell() {
  const [view, setView] = useState<View>("overview");
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  return (
    <div className="dark min-h-screen bg-background text-foreground">
      <div className="grid min-h-screen lg:grid-cols-[232px_1fr]">
        <aside className="hidden border-r border-border lg:block">
          <Sidebar view={view} setView={setView} />
        </aside>
        <div className="min-w-0">
          <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border/70 bg-background/90 px-4 backdrop-blur-xl md:px-7">
            <Sheet>
              <SheetTrigger asChild>
                <Button size="icon" variant="ghost" className="lg:hidden" aria-label="Open navigation">
                  <Menu className="size-4" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-[250px] p-0">
                <Sidebar view={view} setView={setView} />
              </SheetContent>
            </Sheet>
            <div className="lg:hidden">
              <Logo />
            </div>
            <div className="hidden flex-1 md:block">
              <button className="flex h-9 w-72 items-center gap-2 rounded-md border border-border bg-muted/30 px-3 text-xs text-muted-foreground">
                <Search className="size-3.5" />
                Search workspace
                <span className="ml-auto rounded border border-border px-1.5 py-0.5 font-mono text-[9px]">
                  ⌘ K
                </span>
              </button>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Badge
                variant="outline"
                className="hidden gap-1.5 border-emerald-500/20 text-emerald-400 sm:flex"
              >
                <span className="size-1.5 rounded-full bg-emerald-400" />
                Demo data
              </Badge>
              <Button size="sm" onClick={() => setView("runs")}>
                <Play className="size-3 fill-current" />
                Run evaluation
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="icon" variant="ghost" aria-label="Open workspace menu" className="hidden sm:inline-flex">
                    <MoreHorizontal className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem>
                    <TerminalSquare /> Open CLI
                  </DropdownMenuItem>
                  <DropdownMenuItem>
                    <Settings /> Workspace settings
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <Avatar className="hidden size-8 sm:flex">
                <AvatarFallback className="bg-primary/15 text-xs text-primary">
                  PK
                </AvatarFallback>
              </Avatar>
            </div>
          </header>
          <main className="mx-auto max-w-[1440px] p-4 md:p-7">
            {view === "overview" && (
              <Overview setView={setView} onSelect={setSelectedRun} />
            )}
            {view === "runs" && <RunsView onSelect={setSelectedRun} />}
            {view === "scenarios" && <ScenariosView />}
            {view === "comparisons" && <ComparisonsView />}
          </main>
        </div>
      </div>
      {selectedRun && (
        <RunDetail run={selectedRun} close={() => setSelectedRun(null)} />
      )}
    </div>
  );
}
