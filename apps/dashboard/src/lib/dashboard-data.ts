export type ApiEnvelope = { schema_version: "1.0" };

export type Health = ApiEnvelope & {
  status: "ready" | "degraded";
  package_version: string;
  database_ready: boolean;
  database: string;
  artifact_directory: string;
};

export type Overview = ApiEnvelope & {
  total_runs: number;
  passed_runs: number;
  failed_runs: number;
  critical_regressions: number;
  pass_rate: number;
  median_duration_ms: number;
  scenario_count: number;
  recent_pass_rates: number[];
};

export type Scenario = ApiEnvelope & {
  id: string;
  objective: string;
  family: string;
  severity: string;
  tools: string[];
  tags: string[];
  review_status: string;
  source: string;
};

export type RunSummary = ApiEnvelope & {
  id: string;
  scenario_id: string;
  scenario_name: string;
  family: string;
  worker: string;
  status: "pass" | "fail" | "error";
  terminal_reason: string;
  duration_ms: number;
  tool_calls: number;
  mutations: number;
  started_at: string;
  cleanup_succeeded: boolean;
};

export type Verdict = {
  id: string;
  assertion_id: string;
  status: string;
  severity: string;
  message: string;
  reason_code: string;
};

export type WorldEvent = {
  id: string;
  sequence: number;
  event_type: string;
  entity_type: string | null;
  entity_id: string | null;
  occurred_at: string;
  before: unknown;
  after: unknown;
};

export type RunRecord = ApiEnvelope & {
  id: string;
  scenario_id: string;
  worker: string;
  terminal_reason: string;
  started_at: string;
  ended_at: string;
  total_duration_ms: number;
  tool_call_count: number;
  mutation_count: number;
  cleanup_succeeded: boolean;
  incomplete_evidence: boolean;
  initial_snapshot_hash: string | null;
  final_snapshot_hash: string | null;
  events: WorldEvent[];
  verdicts: Verdict[];
  turns: Array<{
    index: number;
    content?: string | null;
    tool_call?: {
      tool_name: string;
      arguments: Record<string, unknown>;
    } | null;
    tool_result?: {
      status: string;
      error_type?: string | null;
      output?: unknown;
    } | null;
  }>;
};

export type Comparison = ApiEnvelope & {
  id: string;
  gate: "pass" | "fail";
  baseline_worker: string;
  candidate_worker: string;
  new_critical: number;
  new_high: number;
  pass_rate_delta: number;
  path: string;
};

export type DashboardData = {
  health: Health;
  overview: Overview;
  scenarios: Scenario[];
  runs: RunSummary[];
  comparisons: Comparison[];
};

const API_URL = (
  process.env.NEXT_PUBLIC_WORKER_WORLDS_API_URL ??
  "http://127.0.0.1:8000/api/v1"
).replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(
      body?.detail ?? `Worker Worlds API returned ${response.status}`,
    );
  }
  return (await response.json()) as T;
}

export async function loadDashboard(): Promise<DashboardData> {
  const [health, overview, scenarioList, runList, comparisonList] =
    await Promise.all([
      request<Health>("/health"),
      request<Overview>("/overview"),
      request<ApiEnvelope & { scenarios: Scenario[] }>("/scenarios"),
      request<ApiEnvelope & { runs: RunSummary[] }>("/runs?limit=100"),
      request<ApiEnvelope & { comparisons: Comparison[] }>("/comparisons"),
    ]);
  return {
    health,
    overview,
    scenarios: scenarioList.scenarios,
    runs: runList.runs,
    comparisons: comparisonList.comparisons,
  };
}

export function loadRun(runId: string): Promise<RunRecord> {
  return request<RunRecord>(`/runs/${encodeURIComponent(runId)}`);
}

export function startRun(
  scenarioId: string,
  world: "stub" | "postgres" = "postgres",
): Promise<RunRecord> {
  return request<RunRecord>("/runs", {
    method: "POST",
    body: JSON.stringify({
      schema_version: "1.0",
      scenario_id: scenarioId,
      worker: "stub",
      world,
    }),
  });
}
