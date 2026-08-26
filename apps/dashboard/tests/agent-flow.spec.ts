import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const agentList = {
  schema_version: "1.0",
  total: 3,
  agents: [
    {
      schema_version: "1.0",
      id: "local-stub",
      adapter: "stub",
      version: "1.0.0",
      model_provider: null,
      model_name: null,
      ready: true,
      missing_requirements: [],
      deterministic_test_infrastructure: true,
    },
    {
      schema_version: "1.0",
      id: "openai-project",
      adapter: "openai-agents",
      version: "1.0.0",
      model_provider: "openai",
      model_name: "gpt-5-mini",
      ready: false,
      missing_requirements: ["Optional SDK package 'agents' is not installed"],
      deterministic_test_infrastructure: false,
    },
    {
      schema_version: "1.0",
      id: "langgraph-project",
      adapter: "langgraph",
      version: "1.0.0",
      model_provider: "openai",
      model_name: "gpt-5-mini",
      ready: false,
      missing_requirements: ["Environment variable OPENAI_API_KEY is not set"],
      deterministic_test_infrastructure: false,
    },
  ],
};

const scenarioList = {
  schema_version: "1.0",
  total: 1,
  scenarios: [
    {
      schema_version: "1.0",
      id: "refund.partial.happy",
      objective: "Refund the damaged item on order ord_900.",
      family: "refund",
      severity: "critical",
      tools: ["issue_refund"],
      tags: ["refund"],
      review_status: "approved",
      source: "refund.yaml",
    },
  ],
};

const catalog = {
  schema_version: "1.0",
  catalog_version: "1.0.0",
  domains: [
    {
      schema_version: "1.0",
      id: "commerce",
      version: "1.0.0",
      label: "Retail & E-commerce",
      description: "Commerce evaluations",
      world_names: ["postgres-commerce"],
      role_ids: ["refund-specialist"],
      capability_ids: ["refund-resolution"],
    },
    {
      schema_version: "1.0",
      id: "insurance",
      version: "1.0.0",
      label: "Insurance",
      description: "Claims evaluations",
      world_names: ["postgres-insurance"],
      role_ids: ["claims-adjuster"],
      capability_ids: ["claims-adjustment"],
    },
  ],
  roles: [
    {
      schema_version: "1.0",
      id: "refund-specialist",
      domain_id: "commerce",
      version: "1.0.0",
      label: "Refund Specialist",
      description: "Refund evaluations",
      capability_ids: ["refund-resolution"],
    },
    {
      schema_version: "1.0",
      id: "claims-adjuster",
      domain_id: "insurance",
      version: "1.0.0",
      label: "Claims Adjuster",
      description: "Claims evaluations",
      capability_ids: ["claims-adjustment"],
    },
  ],
  capabilities: [
    {
      schema_version: "1.0",
      id: "refund-resolution",
      domain_id: "commerce",
      version: "1.0.0",
      label: "Refund resolution",
      description: "Resolve refunds safely",
    },
    {
      schema_version: "1.0",
      id: "claims-adjustment",
      domain_id: "insurance",
      version: "1.0.0",
      label: "Claims adjustment",
      description: "Resolve claims safely",
    },
  ],
  suites: [
    {
      schema_version: "1.0",
      id: "commerce.refund-specialist.smoke",
      domain_id: "commerce",
      role_id: "refund-specialist",
      revision: "1.0.0",
      label: "Refund Specialist Smoke",
      tier: "smoke",
      scenario_ids: ["refund.partial.happy"],
      capability_ids: ["refund-resolution"],
      estimated_duration_s: 3,
      default_limits: {
        schema_version: "1.0",
        wall_time_s: 30,
        tool_calls: 20,
        model_tokens: 12000,
        worker_turns: 50,
        mutations: 20,
        cost_minor: 0,
        tool_timeout_s: 10,
        injections: 20,
      },
    },
    {
      schema_version: "1.0",
      id: "commerce.refund-specialist.custom",
      domain_id: "commerce",
      role_id: "refund-specialist",
      revision: "1.0.0",
      label: "Refund Specialist Custom",
      tier: "custom",
      scenario_ids: [],
      capability_ids: ["refund-resolution"],
      estimated_duration_s: 0,
      default_limits: {
        schema_version: "1.0",
        wall_time_s: 30,
        tool_calls: 20,
        model_tokens: 12000,
        worker_turns: 50,
        mutations: 20,
        cost_minor: 0,
        tool_timeout_s: 10,
        injections: 20,
      },
    },
    {
      schema_version: "1.0",
      id: "insurance.claims-adjuster.smoke",
      domain_id: "insurance",
      role_id: "claims-adjuster",
      revision: "1.0.0",
      label: "Claims Adjuster Smoke",
      tier: "smoke",
      scenario_ids: ["insurance.claims.001"],
      capability_ids: ["claims-adjustment"],
      estimated_duration_s: 4,
      default_limits: {
        schema_version: "1.0",
        wall_time_s: 30,
        tool_calls: 20,
        model_tokens: 12000,
        worker_turns: 50,
        mutations: 20,
        cost_minor: 0,
        tool_timeout_s: 10,
        injections: 20,
      },
    },
  ],
  classifications: [
    {
      schema_version: "1.0",
      scenario_id: "refund.partial.happy",
      scenario_hash: "a".repeat(64),
      domain_id: "commerce",
      role_ids: ["refund-specialist"],
      capability_id: "refund-resolution",
      difficulty: "basic",
      risk_category: "financial",
    },
    {
      schema_version: "1.0",
      scenario_id: "insurance.claims.001",
      scenario_hash: "b".repeat(64),
      domain_id: "insurance",
      role_ids: ["claims-adjuster"],
      capability_id: "claims-adjustment",
      difficulty: "basic",
      risk_category: "financial",
    },
  ],
};

const runRecord = {
  schema_version: "1.0",
  id: "run_browser_test",
  scenario_id: "refund.partial.happy",
  worker: "stub",
  terminal_reason: "completed",
  started_at: "2026-08-20T00:00:00Z",
  ended_at: "2026-08-20T00:00:01Z",
  total_duration_ms: 1000,
  tool_call_count: 1,
  mutation_count: 1,
  cleanup_succeeded: true,
  incomplete_evidence: false,
  model_tokens: null,
  cost_minor: null,
  initial_snapshot_hash: "before",
  final_snapshot_hash: "after",
  events: [],
  verdicts: [],
  turns: [],
};

function providerRunRecord(adapter: string) {
  return {
    ...runRecord,
    id: `run_${adapter}`,
    worker: adapter,
    model_tokens: 37,
    cost_minor: 2,
    turns: [
      {
        index: 0,
        message: "completed",
        model_tokens: 37,
        cost_minor: 2,
        provider_response_ids: [`resp_${adapter}`],
        provider_request_ids: [`req_${adapter}`],
        provider_retry_count: 1,
        model_provider: "openai",
        model_name: "gpt-5-mini",
        model_version: "2025-08-07",
      },
    ],
  };
}

async function mockDashboardApi(
  page: Page,
  postRun: (body: Record<string, unknown>) => Promise<void> | void,
  postSuite?: (body: Record<string, unknown>) => Promise<void> | void,
) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/runs" && request.method() === "POST") {
      await postRun(request.postDataJSON() as Record<string, unknown>);
      await route.fulfill({ status: 201, json: runRecord });
      return;
    }
    if (path === "/api/v1/suite-jobs" && request.method() === "POST") {
      await postSuite?.(request.postDataJSON() as Record<string, unknown>);
      await route.fulfill({
        status: 202,
        json: {
          schema_version: "1.0",
          id: "suitejob_browser",
          request_key: "browser-request",
          status: "completed",
          catalog_version: "1.0.0",
          domain_id: "commerce",
          role_id: "refund-specialist",
          suite_id: "commerce.refund-specialist.smoke",
          suite_revision: "1.0.0",
          agent_id: "local-stub",
          world: "postgres",
          configuration: {},
          total_scenarios: 1,
          completed_scenarios: 1,
          passed_scenarios: 1,
          failed_scenarios: 0,
          cancel_requested: false,
          revision: 2,
          scenarios: [],
          error_type: null,
          error_message: null,
          suite_record_path: "suitejob_browser/suite.json",
          created_at: "2026-08-20T00:00:00Z",
          updated_at: "2026-08-20T00:00:01Z",
          started_at: "2026-08-20T00:00:00Z",
          ended_at: "2026-08-20T00:00:01Z",
        },
      });
      return;
    }
    const responses: Record<string, unknown> = {
      "/api/v1/health": {
        schema_version: "1.0",
        status: "ready",
        package_version: "1.0.0rc1",
        database_ready: true,
        database: "ready",
        artifact_directory: ".worker-worlds/api",
      },
      "/api/v1/overview": {
        schema_version: "1.0",
        total_runs: 0,
        passed_runs: 0,
        failed_runs: 0,
        critical_regressions: 0,
        pass_rate: 0,
        median_duration_ms: 0,
        scenario_count: 1,
        recent_pass_rates: [],
      },
      "/api/v1/scenarios": scenarioList,
      "/api/v1/agents": agentList,
      "/api/v1/runs": { schema_version: "1.0", runs: [], total: 0 },
      "/api/v1/comparisons": {
        schema_version: "1.0",
        comparisons: [],
        total: 0,
      },
      "/api/v1/catalog": catalog,
    };
    await route.fulfill({ status: 200, json: responses[path] });
  });
}

test("completes the domain-role-suite-agent wizard with real catalog values", async ({
  page,
}) => {
  let submitted: Record<string, unknown> | null = null;
  await mockDashboardApi(
    page,
    () => undefined,
    (body) => {
      submitted = body;
    },
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByLabel("Choose domain")).toHaveValue("commerce");
  await expect(page.getByLabel("Choose job role")).toHaveValue("refund-specialist");
  await expect(page.getByLabel("Choose evaluation suite")).toHaveValue(
    "commerce.refund-specialist.smoke",
  );
  await expect(page.getByText("Risk coverage: financial")).toBeVisible();
  await page.getByRole("button", { name: "Start evaluation suite" }).click();
  await expect.poll(() => submitted).not.toBeNull();
  expect(submitted).toMatchObject({
    schema_version: "1.0",
    domain_id: "commerce",
    role_id: "refund-specialist",
    suite_id: "commerce.refund-specialist.smoke",
    agent_id: "local-stub",
    world: "postgres",
  });
  await expect(page.getByText("Suite completed")).toBeVisible();
});

test("selects insurance and submits advanced deterministic budgets", async ({ page }) => {
  let submitted: Record<string, unknown> | null = null;
  await mockDashboardApi(page, () => undefined, (body) => {
    submitted = body;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await page.getByLabel("Choose domain").selectOption("insurance");
  await expect(page.getByLabel("Choose job role")).toHaveValue("claims-adjuster");
  await expect(page.getByLabel("Suite world")).toHaveValue("insurance");
  await page.getByText("Advanced controls").click();
  await page.getByLabel("Seed override").fill("7001");
  await page.getByLabel("Suite deadline (seconds)").fill("45");
  await page.getByLabel("Suite token budget").fill("5000");
  await page.getByLabel("Suite mutation budget").fill("3");
  await page.getByLabel("Injections per scenario").fill("2");
  await page.getByRole("button", { name: "Start evaluation suite" }).click();
  await expect.poll(() => submitted).not.toBeNull();
  expect(submitted).toMatchObject({
    domain_id: "insurance",
    role_id: "claims-adjuster",
    suite_id: "insurance.claims-adjuster.smoke",
    world: "insurance",
    seed: 7001,
    limits: { injections: 2 },
    budget: {
      deadline_s: 45,
      scenarios: 1,
      model_tokens: 5000,
      mutations: 3,
    },
  });
});

test("builds a custom role suite without exposing internal database choices", async ({ page }) => {
  let submitted: Record<string, unknown> | null = null;
  await mockDashboardApi(page, () => undefined, (body) => {
    submitted = body;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await page.getByLabel("Choose evaluation suite").selectOption(
    "commerce.refund-specialist.custom",
  );
  await page.getByText("refund.partial.happy", { exact: true }).click();
  await page.getByRole("button", { name: "Start evaluation suite" }).click();
  await expect.poll(() => submitted).not.toBeNull();
  expect(submitted).toMatchObject({
    suite_id: "commerce.refund-specialist.custom",
    scenario_ids: ["refund.partial.happy"],
    world: "postgres",
  });
});

test("compares two completed agents only inside the selected evaluation context", async ({
  page,
}) => {
  await mockDashboardApi(page, () => undefined);
  const job = (id: string, agentId: string) => ({
    schema_version: "1.0",
    id,
    request_key: `request-${id}`,
    status: "completed",
    catalog_version: "1.0.0",
    domain_id: "commerce",
    role_id: "refund-specialist",
    suite_id: "commerce.refund-specialist.smoke",
    suite_revision: "1.0.0",
    agent_id: agentId,
    world: "postgres",
    configuration: {},
    total_scenarios: 1,
    completed_scenarios: 1,
    passed_scenarios: 1,
    failed_scenarios: 0,
    cancel_requested: false,
    revision: 2,
    scenarios: [],
    error_type: null,
    error_message: null,
    suite_record_path: `${id}/suite.json`,
    created_at: "2026-08-22T00:00:00Z",
    updated_at: "2026-08-22T00:01:00Z",
    started_at: "2026-08-22T00:00:01Z",
    ended_at: "2026-08-22T00:01:00Z",
  });
  const baseline = job("suitejob_baseline", "openai-project-v1");
  const candidate = job("suitejob_candidate", "openai-project-v2");
  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/v1/suite-jobs?limit=200", (route) =>
    route.fulfill({
      status: 200,
      json: { schema_version: "1.0", jobs: [baseline, candidate], total: 2 },
    }),
  );
  await page.route("**/api/v1/comparisons/contextual", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      json: {
        schema_version: "1.0",
        id: "context-comparison_test",
        compatibility: "compatible",
        compatibility_reasons: [],
        passed: true,
        role_summary: {
          baseline: { pass_rate: 1, failures: 0, tool_calls: 6, duration_ms: 100 },
          candidate: { pass_rate: 1, failures: 0, tool_calls: 5, duration_ms: 90 },
          pass_rate_delta: 0,
          failure_delta: 0,
          tool_call_delta: -1,
          duration_delta_ms: -10,
        },
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Comparisons" }).click();
  await expect(page.getByLabel("Choose comparison domain")).toHaveValue("commerce");
  await page.getByRole("button", { name: "Compare completed suites" }).click();
  await expect.poll(() => submitted).not.toBeNull();
  expect(submitted).toMatchObject({
    baseline_job_id: "suitejob_baseline",
    candidate_job_id: "suitejob_candidate",
  });
  await expect(page.getByText("Regression gate passed")).toBeVisible();
});

test("cancels a running suite through the evaluation wizard", async ({ page }) => {
  await mockDashboardApi(page, () => undefined);

  let cancelled = false;
  let cancelRequestReceived = false;

  const job = () => ({
    schema_version: "1.0",
    id: "suitejob_cancel_test",
    request_key: "browser-cancel-request",
    status: cancelled ? "cancelled" : "running",
    catalog_version: "1.0.0",
    domain_id: "commerce",
    role_id: "refund-specialist",
    suite_id: "commerce.refund-specialist.smoke",
    suite_revision: "1.0.0",
    agent_id: "local-stub",
    world: "postgres",
    configuration: {},
    total_scenarios: 1,
    completed_scenarios: 0,
    passed_scenarios: 0,
    failed_scenarios: 0,
    cancel_requested: cancelled,
    revision: cancelled ? 2 : 1,
    scenarios: [],
    error_type: null,
    error_message: null,
    // The suite is still in flight until cancelled: no path is published yet, matching
    // how the wizard's own poll loop decides a job is still running.
    suite_record_path: cancelled ? "suitejob_cancel_test/suite.json" : null,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:00:00Z",
    started_at: "2026-08-20T00:00:00Z",
    ended_at: cancelled ? "2026-08-20T00:00:01Z" : null,
  });

  // Nothing was already running before this test's own suite is created.
  await page.route("**/api/v1/suite-jobs?limit=200", (route) =>
    route.fulfill({ status: 200, json: { schema_version: "1.0", jobs: [], total: 0 } }),
  );

  await page.route("**/api/v1/suite-jobs", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    // The mock backend never resolves this job on its own -- it stays "running" until
    // the browser sends the real DELETE cancellation request below, guaranteeing the UI
    // is in a controlled, non-racing running state when the test clicks Cancel.
    await route.fulfill({ status: 202, json: job() });
  });

  await page.route("**/api/v1/suite-jobs/suitejob_cancel_test", async (route) => {
    const method = route.request().method();
    if (method === "DELETE") {
      cancelRequestReceived = true;
      cancelled = true;
      await route.fulfill({ status: 200, json: job() });
      return;
    }
    if (method === "GET") {
      await route.fulfill({ status: 200, json: job() });
      return;
    }
    await route.fallback();
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByLabel("Choose evaluation suite")).toHaveValue(
    "commerce.refund-specialist.smoke",
  );
  await page.getByRole("button", { name: "Start evaluation suite" }).click();

  // The suite has genuinely reached a running state in the UI -- not a guess about
  // timing -- because the mock never returns a terminal status until cancelled.
  await expect(page.getByText("Suite running")).toBeVisible();
  const cancelButton = page.getByRole("button", { name: "Cancel evaluation" });
  await expect(cancelButton).toBeVisible();

  await cancelButton.click();

  await expect.poll(() => cancelRequestReceived).toBe(true);
  await expect(page.getByText("Suite cancelled")).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel evaluation" })).toHaveCount(0);
  await expect(page.getByText("Suite completed")).toHaveCount(0);
});

test("selects a ready agent, disables unavailable agents, and submits agent_id", async ({
  page,
}) => {
  let submitted: Record<string, unknown> | null = null;
  await mockDashboardApi(page, (body) => {
    submitted = body;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Run evaluation" }).click();

  await expect(page.getByText("Deterministic test infrastructure", { exact: false })).toBeVisible();
  await page.getByRole("combobox", { name: "Choose a registered agent" }).click();
  await page.getByPlaceholder("Search by agent, adapter, provider, or model…").fill("openai");
  const unavailable = page.getByRole("option", { name: /openai-project/i });
  await expect(unavailable).toBeDisabled();
  await expect(
    page.getByText("Unavailable because: Optional SDK package 'agents' is not installed"),
  ).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Start run" }).click();
  await expect.poll(() => submitted).not.toBeNull();
  expect(submitted).toMatchObject({
    schema_version: "1.0",
    scenario_id: "refund.partial.happy",
    agent_id: "local-stub",
    world: "postgres",
  });
  expect(submitted).not.toHaveProperty("worker");

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("renders the readable message from a typed API error", async ({ page }) => {
  await mockDashboardApi(page, async () => {
    // The route is replaced below for this test.
  });
  await page.route("**/api/v1/runs", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 409,
      json: {
        detail: {
          type: "AgentNotReady",
          message: "registered agent is not ready",
          missing_requirements: ["OPENAI_API_KEY"],
        },
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await page.getByRole("button", { name: "Start run" }).click();
  const apiAlert = page.getByRole("alert").filter({ hasText: "Worker Worlds API error" });
  await expect(apiAlert).toContainText("registered agent is not ready");
  await expect(apiAlert).not.toContainText("[object Object]");
});

for (const agent of [
  { id: "openai-project", adapter: "openai-agents" },
  { id: "langgraph-project", adapter: "langgraph" },
]) {
  test(`selects ${agent.id} and inspects completed provider evidence`, async ({ page }) => {
    let created = false;
    const record = providerRunRecord(agent.adapter);
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (path === "/api/v1/runs" && request.method() === "POST") {
        expect(request.postDataJSON()).toMatchObject({ agent_id: agent.id });
        created = true;
        await route.fulfill({ status: 201, json: record });
        return;
      }
      if (path === `/api/v1/runs/${record.id}`) {
        await route.fulfill({ status: 200, json: record });
        return;
      }
      const summary = {
        schema_version: "1.0",
        id: record.id,
        scenario_id: "refund.partial.happy",
        scenario_name: "Refund the damaged item",
        family: "refund",
        worker: agent.adapter,
        status: "pass",
        terminal_reason: "completed",
        duration_ms: 1000,
        tool_calls: 1,
        mutations: 1,
        started_at: "2026-08-20T00:00:00Z",
        cleanup_succeeded: true,
      };
      const responses: Record<string, unknown> = {
        "/api/v1/health": {
          schema_version: "1.0",
          status: "ready",
          package_version: "1.0.0rc1",
          database_ready: true,
          database: "ready",
          artifact_directory: ".worker-worlds/api",
        },
        "/api/v1/overview": {
          schema_version: "1.0",
          total_runs: created ? 1 : 0,
          passed_runs: created ? 1 : 0,
          failed_runs: 0,
          critical_regressions: 0,
          pass_rate: created ? 1 : 0,
          median_duration_ms: created ? 1000 : 0,
          scenario_count: 1,
          recent_pass_rates: [],
        },
        "/api/v1/scenarios": scenarioList,
        "/api/v1/agents": {
          ...agentList,
          agents: agentList.agents.map((item) =>
            item.id === agent.id ? { ...item, ready: true, missing_requirements: [] } : item,
          ),
        },
        "/api/v1/runs": {
          schema_version: "1.0",
          runs: created ? [summary] : [],
          total: created ? 1 : 0,
        },
        "/api/v1/comparisons": { schema_version: "1.0", comparisons: [], total: 0 },
        "/api/v1/catalog": catalog,
      };
      await route.fulfill({ status: 200, json: responses[path] });
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Run evaluation" }).click();
    await page.getByRole("combobox", { name: "Choose a registered agent" }).click();
    await page.getByPlaceholder("Search by agent, adapter, provider, or model…").fill(agent.id);
    await page.getByRole("option", { name: new RegExp(agent.id, "i") }).click();
    await page.getByRole("button", { name: "Start run" }).click();
    await page.getByRole("row").filter({ hasText: record.id }).click();
    await expect(page.getByText("openai/gpt-5-mini", { exact: true })).toBeVisible();
    await expect(page.getByText("37", { exact: true })).toBeVisible();
    await expect(page.getByText("2 minor units")).toBeVisible();
    await expect(page.getByText(`resp_${agent.adapter}`)).toBeVisible();
    await expect(page.getByText(`req_${agent.adapter}`)).toBeVisible();
  });
}

test("shows loading, degraded, empty, and no-ready-agent states", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 150));
    const path = new URL(route.request().url()).pathname;
    const responses: Record<string, unknown> = {
      "/api/v1/health": {
        schema_version: "1.0",
        status: "degraded",
        package_version: "1.0.0rc1",
        database_ready: false,
        database: "database unavailable",
        artifact_directory: ".worker-worlds/api",
      },
      "/api/v1/overview": {
        schema_version: "1.0",
        total_runs: 0,
        passed_runs: 0,
        failed_runs: 0,
        critical_regressions: 0,
        pass_rate: 0,
        median_duration_ms: 0,
        scenario_count: 0,
        recent_pass_rates: [],
      },
      "/api/v1/scenarios": { schema_version: "1.0", scenarios: [], total: 0 },
      "/api/v1/agents": {
        ...agentList,
        agents: agentList.agents.map((item) => ({
          ...item,
          ready: false,
          missing_requirements: ["Database is unavailable"],
        })),
      },
      "/api/v1/runs": { schema_version: "1.0", runs: [], total: 0 },
      "/api/v1/comparisons": { schema_version: "1.0", comparisons: [], total: 0 },
      "/api/v1/catalog": catalog,
    };
    await route.fulfill({ status: 200, json: responses[path] });
  });
  await page.goto("/");
  await expect(page.getByText("Loading real Worker Worlds evidence…")).toBeVisible();
  await expect(page.getByText("API degraded")).toBeVisible();
  await page.getByRole("button", { name: "Run evaluation" }).click();
  await expect(page.getByText("No registered agent is ready", { exact: false })).toBeVisible();
  await expect(page.getByText("No matching runs")).toBeVisible();
});

test("shows a disconnected API state", async ({ page }) => {
  await page.route("**/api/v1/**", (route) => route.abort("connectionrefused"));
  await page.goto("/");
  await expect(
    page.getByRole("alert").filter({ hasText: "Worker Worlds API error" }),
  ).toContainText("Worker Worlds API error");
  await expect(page.getByText("API not connected")).toBeVisible();
});
