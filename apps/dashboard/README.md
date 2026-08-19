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
scenario YAML, derives metrics from persisted `RunRecord` artifacts, starts the
existing runner, and displays its real verdict and event evidence. Override the
API location with `NEXT_PUBLIC_WORKER_WORLDS_API_URL`.

```bash
npm run lint
npm run build
```
