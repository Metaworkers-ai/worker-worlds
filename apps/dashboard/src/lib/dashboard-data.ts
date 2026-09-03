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

export type Agent = ApiEnvelope & {
  id: string;
  adapter: string;
  version: string;
  model_provider: string | null;
  model_name: string | null;
  ready: boolean;
  missing_requirements: string[];
  deterministic_test_infrastructure: boolean;
  supported_domain_ids: string[];
};

export type CapabilityDefinition = ApiEnvelope & {
  id: string;
  domain_id: string;
  version: string;
  label: string;
  description: string;
};

export type RoleDefinition = ApiEnvelope & {
  id: string;
  domain_id: string;
  version: string;
  label: string;
  description: string;
  capability_ids: string[];
};

export type DomainDefinition = ApiEnvelope & {
  id: string;
  version: string;
  label: string;
  description: string;
  world_names: string[];
  role_ids: string[];
  capability_ids: string[];
};

export type SuiteDefinition = ApiEnvelope & {
  id: string;
  domain_id: string;
  role_id: string;
  revision: string;
  label: string;
  tier: "smoke" | "standard" | "full" | "custom";
  scenario_ids: string[];
  capability_ids: string[];
  estimated_duration_s: number;
  default_limits: {
    wall_time_s: number;
    tool_calls: number;
    model_tokens: number;
    worker_turns: number;
    mutations: number;
    cost_minor: number;
    tool_timeout_s: number;
    injections: number;
  };
};

export type SuiteBudget = {
  deadline_s: number;
  scenarios: number;
  tool_calls: number;
  model_tokens: number;
  mutations: number;
  cost_minor: number;
};

export type ScenarioClassification = ApiEnvelope & {
  scenario_id: string;
  scenario_hash: string;
  domain_id: string;
  role_ids: string[];
  capability_id: string;
  difficulty: string;
  risk_category: string;
};

export type Catalog = ApiEnvelope & {
  catalog_version: string;
  domains: DomainDefinition[];
  roles: RoleDefinition[];
  capabilities: CapabilityDefinition[];
  suites: SuiteDefinition[];
  classifications: ScenarioClassification[];
};

export type SuiteScenario = ApiEnvelope & {
  scenario_id: string;
  ordinal: number;
  status: string;
  attempts: number;
  run_id: string | null;
  terminal_reason: string | null;
};

export type SuiteJob = ApiEnvelope & {
  id: string;
  request_key: string;
  status: "queued" | "running" | "cancelling" | "cancelled" | "completed" | "failed";
  catalog_version: string;
  domain_id: string;
  role_id: string;
  suite_id: string;
  suite_revision: string;
  agent_id: string;
  world: string;
  total_scenarios: number;
  completed_scenarios: number;
  passed_scenarios: number;
  failed_scenarios: number;
  cancel_requested: boolean;
  revision: number;
  scenarios: SuiteScenario[];
  error_type: string | null;
  error_message: string | null;
  suite_record_path: string | null;
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
  model_tokens: number | null;
  cost_minor: number | null;
  initial_snapshot_hash: string | null;
  final_snapshot_hash: string | null;
  events: WorldEvent[];
  verdicts: Verdict[];
  turns: Array<{
    index: number;
    message?: string | null;
    model_tokens?: number | null;
    cost_minor?: number | null;
    provider_response_ids?: string[];
    provider_request_ids?: string[];
    provider_retry_count?: number;
    model_provider?: string | null;
    model_name?: string | null;
    model_version?: string | null;
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
  domain_id?: string | null;
  role_id?: string | null;
  suite_id?: string | null;
  compatibility?: string | null;
};

export type ContextualComparison = ApiEnvelope & {
  id: string;
  compatibility: "compatible" | "incompatible";
  compatibility_reasons: string[];
  passed: boolean;
  role_summary: {
    baseline: { pass_rate: number; failures: number; tool_calls: number; duration_ms: number };
    candidate: { pass_rate: number; failures: number; tool_calls: number; duration_ms: number };
    pass_rate_delta: number;
    failure_delta: number;
    tool_call_delta: number;
    duration_delta_ms: number;
  };
};

export type DashboardData = {
  health: Health;
  overview: Overview;
  scenarios: Scenario[];
  agents: Agent[];
  runs: RunSummary[];
  comparisons: Comparison[];
  catalog: Catalog;
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
      detail?: string | { message?: string };
    } | null;
    const detail = body?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message ?? `Worker Worlds API returned ${response.status}`,
    );
  }
  return (await response.json()) as T;
}

export async function loadDashboard(): Promise<DashboardData> {
  const [health, overview, scenarioList, agentList, runList, comparisonList, catalog] =
    await Promise.all([
      request<Health>("/health"),
      request<Overview>("/overview"),
      request<ApiEnvelope & { scenarios: Scenario[] }>("/scenarios"),
      request<ApiEnvelope & { agents: Agent[] }>("/agents"),
      request<ApiEnvelope & { runs: RunSummary[] }>("/runs?limit=100"),
      request<ApiEnvelope & { comparisons: Comparison[] }>("/comparisons"),
      request<Catalog>("/catalog"),
    ]);
  return {
    health,
    overview,
    scenarios: scenarioList.scenarios,
    agents: agentList.agents,
    runs: runList.runs,
    comparisons: comparisonList.comparisons,
    catalog,
  };
}

export function loadRun(runId: string): Promise<RunRecord> {
  return request<RunRecord>(`/runs/${encodeURIComponent(runId)}`);
}

export function startRun(
  scenarioId: string,
  agentId: string,
  world: "stub" | "postgres" = "postgres",
): Promise<RunRecord> {
  return request<RunRecord>("/runs", {
    method: "POST",
    body: JSON.stringify({
      schema_version: "1.0",
      scenario_id: scenarioId,
      agent_id: agentId,
      world,
    }),
  });
}

export function startSuiteJob(input: {
  requestKey: string;
  domainId: string;
  roleId: string;
  suiteId: string;
  agentId: string;
  world: "stub" | "postgres" | "supply-chain" | "insurance" | "marketing";
  concurrency: number;
  scenarioIds?: string[];
  seed?: number;
  limits?: SuiteDefinition["default_limits"];
  budget?: SuiteBudget;
}): Promise<SuiteJob> {
  return request<SuiteJob>("/suite-jobs", {
    method: "POST",
    body: JSON.stringify({
      schema_version: "1.0",
      request_key: input.requestKey,
      domain_id: input.domainId,
      role_id: input.roleId,
      suite_id: input.suiteId,
      agent_id: input.agentId,
      world: input.world,
      concurrency: input.concurrency,
      scenario_ids: input.scenarioIds ?? [],
      seed: input.seed,
      limits: input.limits,
      budget: input.budget,
    }),
  });
}

export function loadSuiteJob(jobId: string): Promise<SuiteJob> {
  return request<SuiteJob>(`/suite-jobs/${encodeURIComponent(jobId)}`);
}

export function cancelSuiteJob(jobId: string): Promise<SuiteJob> {
  return request<SuiteJob>(`/suite-jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });
}

export async function loadSuiteJobs(): Promise<SuiteJob[]> {
  const response = await request<ApiEnvelope & { jobs: SuiteJob[] }>("/suite-jobs?limit=200");
  return response.jobs;
}

export function compareSuiteJobs(
  baselineJobId: string,
  candidateJobId: string,
): Promise<ContextualComparison> {
  return request<ContextualComparison>("/comparisons/contextual", {
    method: "POST",
    body: JSON.stringify({
      schema_version: "1.0",
      baseline_job_id: baselineJobId,
      candidate_job_id: candidateJobId,
    }),
  });
}

export function suiteEvidenceUrl(jobId: string): string {
  return `${API_URL}/suite-jobs/${encodeURIComponent(jobId)}/evidence`;
}
