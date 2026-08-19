# Worker Worlds dashboard

A responsive Next.js 16 dashboard for inspecting Worker Worlds runs, evidence,
scenario coverage, and behavioral comparisons.

```bash
npm install
npm run dev
```

Open `http://localhost:3000`. The preview currently uses typed demo records in
`src/lib/dashboard-data.ts`; it does not connect to Postgres or execute workers.
The intended next boundary is a versioned HTTP API that returns the existing
Worker Worlds `RunRecord`, `SuiteRecord`, scenario, and comparison contracts.

```bash
npm run lint
npm run build
```
