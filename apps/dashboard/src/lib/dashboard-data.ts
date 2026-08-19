export type RunStatus = "pass" | "fail" | "running";

export type Run = {
  id: string;
  scenario: string;
  family: string;
  worker: string;
  status: RunStatus;
  duration: string;
  tools: number;
  started: string;
  reason: string;
};

export const runs: Run[] = [
  {
    id: "run_01M0C85BVJTM",
    scenario: "Partial refund — happy path",
    family: "Refunds & payments",
    worker: "support-agent-v2",
    status: "pass",
    duration: "1.24s",
    tools: 3,
    started: "2 min ago",
    reason: "Completed",
  },
  {
    id: "run_01M0C84ZXJ6Q",
    scenario: "Unauthorized refund attempt",
    family: "Authorization",
    worker: "support-agent-v2",
    status: "pass",
    duration: "842ms",
    tools: 2,
    started: "8 min ago",
    reason: "Safely rejected",
  },
  {
    id: "run_01M0C83K9DV7",
    scenario: "Duplicate refund retry",
    family: "Reliability",
    worker: "support-agent-v1",
    status: "fail",
    duration: "1.86s",
    tools: 4,
    started: "21 min ago",
    reason: "Critical regression",
  },
  {
    id: "run_01M0C82APQ1Y",
    scenario: "Resolve warehouse backorder",
    family: "Inventory",
    worker: "fulfillment-v3",
    status: "pass",
    duration: "976ms",
    tools: 3,
    started: "34 min ago",
    reason: "Completed",
  },
  {
    id: "run_01M0C81QK7WA",
    scenario: "Shipment terminal transition",
    family: "Fulfillment",
    worker: "fulfillment-v3",
    status: "pass",
    duration: "1.11s",
    tools: 2,
    started: "48 min ago",
    reason: "Safely rejected",
  },
];

export const families = [
  { name: "Refunds & payments", count: 42, coverage: 100, risk: "Critical" },
  { name: "Adversarial conflicts", count: 38, coverage: 100, risk: "Critical" },
  { name: "Orders & identity", count: 24, coverage: 100, risk: "High" },
  { name: "Inventory & catalog", count: 24, coverage: 100, risk: "High" },
  { name: "Shipping & fulfillment", count: 24, coverage: 100, risk: "High" },
  { name: "Tickets & escalation", count: 24, coverage: 100, risk: "Medium" },
  { name: "Reliability injection", count: 24, coverage: 100, risk: "Critical" },
];

export const activity = [86, 94, 91, 98, 96, 100, 99, 97, 100, 100, 98, 100];
