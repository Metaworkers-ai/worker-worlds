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
) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/runs" && request.method() === "POST") {
      await postRun(request.postDataJSON() as Record<string, unknown>);
      await route.fulfill({ status: 201, json: runRecord });
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
    };
    await route.fulfill({ status: 200, json: responses[path] });
  });
}

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
