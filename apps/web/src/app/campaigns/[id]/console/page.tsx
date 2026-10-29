"use client";

import Link from "next/link";
import { use, useState } from "react";
import { Status, useLoad } from "@/components/useLoad";
import { api } from "@/lib/api";

type Diagnostics = {
  spoiler_warning: string;
  campaign_version: number;
  actions: { id: string; input: string; status: string; proposal: unknown; validation: unknown; result: unknown;
    diagnostics: unknown; error_code: string | null; created_at: string }[];
  events: { sequence: number; type: string; summary: string; visibility: string; payload?: unknown }[];
  npc_knowledge: { npc: string; disposition: string; memories: { content: string; learned_via: string }[] }[];
  restricted: Record<string, string>;
  rules_coverage: { mechanic: string; status: string }[];
  house_rules: { rule_key: string; description: string }[];
};

function Console({ id }: { id: string }) {
  const { data, loading, error, reload } = useLoad(() => api<Diagnostics>(`/campaigns/${id}/diagnostics`), [id]);
  return (
    <>
      <div className="row"><h1 style={{ margin: 0 }}>DM console</h1><button className="secondary" onClick={reload}>Refresh</button></div>
      <Status loading={loading && !data} error={error} />
      {data && (
        <>
          <p className="notice">{data.spoiler_warning} Campaign version {data.campaign_version}.</p>
          <h2>Recent actions</h2>
          {data.actions.map((a) => (
            <details key={a.id} className="card">
              <summary><span className="badge">{a.status}</span> {a.input} {a.error_code && <span className="badge danger">{a.error_code}</span>}</summary>
              <h3>Proposal (model)</h3><pre>{JSON.stringify(a.proposal, null, 2)}</pre>
              <h3>Validation</h3><pre>{JSON.stringify(a.validation, null, 2)}</pre>
              <h3>Committed result</h3><pre>{JSON.stringify(a.result, null, 2)}</pre>
              <h3>Retrieved context and provider diagnostics</h3><pre>{JSON.stringify(a.diagnostics, null, 2)}</pre>
            </details>
          ))}
          <h2>NPC knowledge</h2>
          {data.npc_knowledge.map((n) => (
            <div key={n.npc} className="card small"><strong>{n.npc}</strong> <span className="badge">{n.disposition}</span>
              <ul>{n.memories.map((m, i) => <li key={i}><span className="badge">{m.learned_via}</span> {m.content}</li>)}</ul></div>
          ))}
          <h2>Undiscovered DM-only content</h2>
          <ul className="small">{Object.entries(data.restricted).map(([k, t]) => <li key={k}>{t}</li>)}</ul>
          <h2>Event log (all visibilities)</h2>
          <table><tbody>{data.events.map((e) => (
            <tr key={e.sequence}><td>{e.sequence}</td><td className="mono small">{e.type}</td><td>{e.summary}</td>
              <td><span className={`badge ${e.visibility === "DM_ONLY" ? "warn" : ""}`}>{e.visibility}</span></td></tr>
          ))}</tbody></table>
          <h2>House rules accepted for this campaign</h2>
          <ul className="small">{data.house_rules.map((h) => <li key={h.rule_key}><span className="mono">{h.rule_key}</span></li>)}</ul>
        </>
      )}
    </>
  );
}

export default function ConsolePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [revealed, setRevealed] = useState(false);
  return (
    <>
      <p><Link href={`/campaigns/${id}/play`}>← Back to play</Link></p>
      {revealed ? <Console id={id} /> : (
        <div className="card stack">
          <h1 style={{ margin: 0 }}>DM console</h1>
          <p>This diagnostic view shows retrieved passages, model proposals, validation results, NPC knowledge, and
            undiscovered secrets. Opening it will spoil the adventure.</p>
          <button onClick={() => setRevealed(true)}>Show DM-only diagnostics</button>
        </div>
      )}
    </>
  );
}
