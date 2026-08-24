"use client";

import { useEffect, useMemo, useState } from "react";
import { GitCompareArrows, LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  compareSuiteJobs,
  loadSuiteJobs,
  suiteEvidenceUrl,
  type Catalog,
  type ContextualComparison,
  type SuiteJob,
} from "@/lib/dashboard-data";

export function ComparisonBuilder({ catalog }: { catalog: Catalog }) {
  const [jobs, setJobs] = useState<SuiteJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ContextualComparison | null>(null);
  const [domainId, setDomainId] = useState(catalog.domains[0]?.id ?? "");
  const roles = useMemo(
    () => catalog.roles.filter((item) => item.domain_id === domainId),
    [catalog.roles, domainId],
  );
  const [roleId, setRoleId] = useState(roles[0]?.id ?? "");
  const suites = useMemo(
    () => catalog.suites.filter((item) => item.role_id === roleId),
    [catalog.suites, roleId],
  );
  const [suiteId, setSuiteId] = useState(suites[0]?.id ?? "");
  const compatible = jobs.filter(
    (item) =>
      item.status === "completed" &&
      item.domain_id === domainId &&
      item.role_id === roleId &&
      item.suite_id === suiteId &&
      Boolean(item.suite_record_path),
  );
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const baseline = compatible.find((item) => item.id === baselineId) ?? compatible[0];
  const candidate =
    compatible.find((item) => item.id === candidateId && item.id !== baseline?.id) ??
    compatible.find((item) => item.id !== baseline?.id);

  useEffect(() => {
    let active = true;
    loadSuiteJobs()
      .then((items) => {
        if (active) setJobs(items);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Suite jobs unavailable");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <Card className="border-primary/25 bg-primary/[0.025] shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <GitCompareArrows className="size-4 text-primary" /> Context-safe comparison
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <ComparisonSelect
            label="Domain"
            value={domainId}
            onChange={(value) => {
              setDomainId(value);
              const nextRole = catalog.roles.find((item) => item.domain_id === value);
              setRoleId(nextRole?.id ?? "");
              setSuiteId(
                catalog.suites.find((item) => item.role_id === nextRole?.id)?.id ?? "",
              );
            }}
            options={catalog.domains.map((item) => ({ id: item.id, label: item.label }))}
          />
          <ComparisonSelect
            label="Job role"
            value={roleId}
            onChange={(value) => {
              setRoleId(value);
              setSuiteId(catalog.suites.find((item) => item.role_id === value)?.id ?? "");
            }}
            options={roles.map((item) => ({ id: item.id, label: item.label }))}
          />
          <ComparisonSelect
            label="Suite"
            value={suiteId}
            onChange={setSuiteId}
            options={suites.map((item) => ({ id: item.id, label: item.label }))}
          />
          <ComparisonSelect
            label="Baseline agent"
            value={baseline?.id ?? ""}
            onChange={setBaselineId}
            options={compatible.map((item) => ({
              id: item.id,
              label: `${item.agent_id} · ${item.id}`,
            }))}
          />
          <ComparisonSelect
            label="Candidate agent"
            value={candidate?.id ?? ""}
            onChange={setCandidateId}
            options={compatible
              .filter((item) => item.id !== baseline?.id)
              .map((item) => ({ id: item.id, label: `${item.agent_id} · ${item.id}` }))}
          />
        </div>
        {loading ? <p className="text-xs text-muted-foreground">Loading suite evidence…</p> : null}
        {!loading && compatible.length < 2 ? (
          <p className="text-xs text-muted-foreground">
            Run this suite with two agent versions before comparing it.
          </p>
        ) : null}
        {error ? <p role="alert" className="text-xs text-red-400">{error}</p> : null}
        <Button
          disabled={!baseline || !candidate || submitting}
          onClick={async () => {
            if (!baseline || !candidate) return;
            setSubmitting(true);
            setError(null);
            try {
              setResult(await compareSuiteJobs(baseline.id, candidate.id));
            } catch (reason) {
              setError(reason instanceof Error ? reason.message : "Comparison failed");
            } finally {
              setSubmitting(false);
            }
          }}
        >
          {submitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
          Compare completed suites
        </Button>
        {result ? (
          <div role="status" className="rounded-md border border-border/70 p-3 text-sm">
            <div className="flex items-center gap-2">
              <Badge variant="outline">{result.compatibility}</Badge>
              <span>{result.passed ? "Regression gate passed" : "Regression gate failed"}</span>
            </div>
            {result.compatibility_reasons.length ? (
              <p className="mt-2 text-xs text-amber-300">
                {result.compatibility_reasons.join("; ")}
              </p>
            ) : null}
            <p className="mt-2 font-mono text-[10px] text-muted-foreground">{result.id}</p>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
              <ComparisonMetric label="Pass-rate delta" value={`${(result.role_summary.pass_rate_delta * 100).toFixed(1)} pp`} />
              <ComparisonMetric label="Failure delta" value={`${result.role_summary.failure_delta}`} />
              <ComparisonMetric label="Tool-call delta" value={`${result.role_summary.tool_call_delta}`} />
              <ComparisonMetric label="Latency delta" value={`${result.role_summary.duration_delta_ms} ms`} />
            </div>
            <div className="mt-3 flex gap-3 text-xs">
              <a className="text-primary underline" href={suiteEvidenceUrl(baseline?.id ?? "")}>
                Baseline evidence
              </a>
              <a className="text-primary underline" href={suiteEvidenceUrl(candidate?.id ?? "")}>
                Candidate evidence
              </a>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ComparisonMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border/70 p-2">
      <p className="text-[10px] text-muted-foreground">{label}</p>
      <p className="mt-1 font-mono">{value}</p>
    </div>
  );
}

function ComparisonSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<{ id: string; label: string }>;
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-xs text-muted-foreground">
      {label}
      <select
        aria-label={`Choose comparison ${label.toLowerCase()}`}
        className="mt-1 w-full rounded-md border border-border bg-background px-2.5 py-2 text-foreground"
        value={value}
        disabled={!options.length}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
    </label>
  );
}
