"use client";

import Link from "next/link";
import { use } from "react";
import { Status, useLoad } from "@/components/useLoad";
import { api } from "@/lib/api";

type Graph = {
  entities: { id: string; type: string; name: string; visibility: string; summary: string;
    fields: Record<string, unknown>; citations: { passage_id: string; page_number: number | null }[] }[];
  connections: { id: string; from_name: string; to_name: string; label: string; hidden: boolean; bidirectional: boolean }[];
  edges: { id: string; source_name: string; target_name: string; type: string }[];
  findings: { code: string; message: string }[];
  pending_candidates: number;
};

export default function GraphPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, loading, error } = useLoad(() => api<Graph>(`/adventures/${id}/graph`), [id]);
  return (
    <>
      <p><Link href="/library">← Library</Link> · <Link href={`/adventures/${id}/review`}>Review candidates</Link></p>
      <h1>Adventure graph workbench</h1>
      <p className="notice small">Approved, source-backed definitions. This view contains DM-only content (spoilers).
        Location connectivity shown here is the single source of truth future maps will read.</p>
      <Status loading={loading} error={error} />
      {data && (
        <>
          {data.pending_candidates > 0 && <p className="notice">{data.pending_candidates} candidate(s) still need review.</p>}
          {data.findings.map((f, i) => <p key={i} className="notice">{f.code}: {f.message}</p>)}
          <h2>Locations and connections</h2>
          <table>
            <thead><tr><th>From</th><th>To</th><th>Label</th><th>Flags</th></tr></thead>
            <tbody>
              {data.connections.map((c) => (
                <tr key={c.id}><td>{c.from_name}</td><td>{c.to_name}</td><td>{c.label}</td>
                  <td>{c.hidden && <span className="badge warn">hidden</span>} {c.bidirectional && <span className="badge">two-way</span>}</td></tr>
              ))}
            </tbody>
          </table>
          <h2>Entities ({data.entities.length})</h2>
          <table>
            <thead><tr><th>Type</th><th>Name</th><th>Visibility</th><th>Summary</th><th>Source</th></tr></thead>
            <tbody>
              {data.entities.map((e) => (
                <tr key={e.id}>
                  <td><span className="badge">{e.type}</span></td>
                  <td>{e.name}</td>
                  <td><span className={`badge ${e.visibility === "DM_ONLY" ? "warn" : ""}`}>{e.visibility}</span></td>
                  <td className="small">{e.summary}</td>
                  <td className="small">{e.citations.map((c) => `p.${c.page_number}`).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h2>Relationships ({data.edges.length})</h2>
          <ul>
            {data.edges.map((e) => <li key={e.id}>{e.source_name} <span className="badge">{e.type}</span> {e.target_name}</li>)}
          </ul>
        </>
      )}
    </>
  );
}
