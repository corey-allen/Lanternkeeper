"use client";

import { Status, useLoad } from "@/components/useLoad";
import { api } from "@/lib/api";

type Profile = {
  key: string; name: string; version: string; description: string;
  entries: Record<string, { status: string; source: string; notes: string; value: unknown }>;
  coverage: { mechanic: string; description: string; status: string; blocking_rules: string[] }[];
};

const tone = (s: string) => (s === "AVAILABLE" || s === "VERIFIED" ? "ok" : s === "HOUSE_RULE" ? "warn" : "danger");

export default function RulesPage() {
  const { data, loading, error } = useLoad(() => api<Profile[]>("/rules/profiles"));
  return (
    <>
      <h1>Rules coverage</h1>
      <p className="notice">Only mechanics marked AVAILABLE are resolved by the engine. Everything else is reported as
        unsupported; the DM model never fills gaps with invented rules.</p>
      <Status loading={loading} error={error} />
      {data?.map((p) => (
        <section key={p.key} className="card" aria-labelledby={`p-${p.key}`}>
          <h2 id={`p-${p.key}`}>{p.name} <span className="badge">{p.key}@{p.version}</span></h2>
          <p className="muted">{p.description}</p>
          <h3>Mechanics</h3>
          <table>
            <thead><tr><th>Mechanic</th><th>Status</th><th>Blocked by</th></tr></thead>
            <tbody>
              {p.coverage.map((c) => (
                <tr key={c.mechanic}>
                  <td><span className="mono">{c.mechanic}</span><div className="small muted">{c.description}</div></td>
                  <td><span className={`badge ${tone(c.status)}`}>{c.status}</span></td>
                  <td className="mono small">{c.blocking_rules.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>Rule values</h3>
          <table>
            <thead><tr><th>Rule</th><th>Status</th><th>Value</th><th>Source</th></tr></thead>
            <tbody>
              {Object.entries(p.entries).map(([k, e]) => (
                <tr key={k}>
                  <td className="mono">{k}</td>
                  <td><span className={`badge ${tone(e.status)}`}>{e.status}</span></td>
                  <td><pre>{e.value === null ? "—" : JSON.stringify(e.value, null, 1)}</pre></td>
                  <td className="small">{e.source}{e.notes ? ` — ${e.notes}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </>
  );
}
