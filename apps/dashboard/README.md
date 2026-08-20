# Worker Worlds dashboard

A responsive Next.js 16 dashboard for inspecting Worker Worlds runs, evidence,
scenario coverage, and behavioral comparisons.

```bash
npm install
npm run dev
```

Start the Python service from the repository root first:

```bash
pip install -e '.[dev,api]'
worker-worlds-api
```

Open `http://localhost:3000`. The dashboard reads `/api/v1`, discovers actual
registered agents and scenarios, disables agents that are not ready, and submits
the selected `agent_id` when starting a run. Run evidence includes normalized
adapter/model identity, provider request and response IDs, token usage, retries,
and cost when supported. Metrics come from persisted `RunRecord` artifacts and
the UI displays real verdict and event evidence. Override the API location with
`NEXT_PUBLIC_WORKER_WORLDS_API_URL`.

Run the browser and accessibility contract with:

```bash
npm run test:e2e
```

```bash
npm run lint
npm run build
```
