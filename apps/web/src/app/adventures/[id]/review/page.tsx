"use client";

import Link from "next/link";
import { use, useMemo, useState } from "react";
import { Status, useLoad } from "@/components/useLoad";
import { Candidate, api, errorText, patch, post } from "@/lib/api";

const ORDER = ["LOCATION", "NPC", "MONSTER", "CLUE", "SECRET", "QUEST", "TREASURE", "HAZARD", "FACTION",
  "ENCOUNTER", "STORY_BEAT", "CONNECTION", "EDGE"];
const STATUS_TONE: Record<string, string> = { APPROVED: "ok", REJECTED: "danger", UNCERTAIN: "warn", PENDING: "" };

function CandidateCard({ c, onChange }: { c: Candidate; onChange: () => void }) {
  const [name, setName] = useState(c.name);
  const [json, setJson] = useState(JSON.stringify(c.data, null, 2));
  const [note, setNote] = useState(c.reviewer_note);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const final = c.status === "APPROVED" || c.status === "REJECTED";

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  function save() {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(json);
    } catch {
      setError("Fields must be valid JSON");
      return Promise.resolve();
    }
    return run(() => patch(`/candidates/${c.id}`, { name, data, reviewer_note: note }));
  }

  return (
    <article className="card stack" aria-label={`${c.type} ${c.name}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}><span className="badge">{c.type}</span> {c.name}</h3>
        <span className={`badge ${STATUS_TONE[c.status] ?? ""}`}>{c.status}{c.edited ? " · edited" : ""}</span>
      </div>
      {c.warnings.map((w, i) => (
        <p key={i} className={w.severity === "error" ? "error" : "notice"}>{w.code}: {w.message}</p>
      ))}
      <details open={!final}>
        <summary>Evidence ({c.evidence.length} passage{c.evidence.length === 1 ? "" : "s"})</summary>
        {c.evidence.map((e) => (
          <blockquote key={e.passage_id}>
            <span className="badge">p.{e.page_number}</span>{" "}
            {e.injection_flags.length > 0 && <span className="badge danger">possible prompt injection</span>}{" "}
            {e.text}
          </blockquote>
        ))}
      </details>
      {!final && (
        <>
          <div>
            <label htmlFor={`n-${c.id}`}>Name</label>
            <input id={`n-${c.id}`} type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label htmlFor={`d-${c.id}`}>Fields (validated by the server)</label>
            <textarea id={`d-${c.id}`} rows={Math.min(12, json.split("\n").length + 1)} value={json}
              onChange={(e) => setJson(e.target.value)} />
          </div>
          <div>
            <label htmlFor={`r-${c.id}`}>Reviewer note</label>
            <input id={`r-${c.id}`} type="text" value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </>
      )}
      {final && <pre>{JSON.stringify(c.data, null, 2)}</pre>}
      {error && <p className="error" role="alert">{error}</p>}
      {!final && (
        <div className="row">
          <button disabled={busy} onClick={() => run(() => post(`/candidates/${c.id}/approve`))}>Approve</button>
          <button className="secondary" disabled={busy} onClick={save}>Save edits</button>
          <button className="danger" disabled={busy}
            onClick={() => run(() => post(`/candidates/${c.id}/reject`, { reviewer_note: note }))}>Reject</button>
        </div>
      )}
    </article>
  );
}

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [filter, setFilter] = useState("open");
  const cands = useLoad(() => api<Candidate[]>(`/adventures/${id}/candidates`), [id]);
  const shown = useMemo(() => {
    const list = (cands.data ?? []).filter((c) =>
      filter === "all" ? true : filter === "open" ? ["PENDING", "UNCERTAIN"].includes(c.status) : c.status === filter);
    return list.sort((a, b) => ORDER.indexOf(a.type) - ORDER.indexOf(b.type));
  }, [cands.data, filter]);
  const counts = (cands.data ?? []).reduce<Record<string, number>>((m, c) => ({ ...m, [c.status]: (m[c.status] ?? 0) + 1 }), {});

  return (
    <>
      <p><Link href="/library">← Library</Link> · <Link href={`/adventures/${id}/graph`}>Graph workbench</Link></p>
      <h1>Review extracted candidates</h1>
      <p className="notice small">Nothing becomes an adventure fact until you approve it. Approve locations and other
        entities before the connections and relationships that refer to them.</p>
      <div className="row" role="group" aria-label="Filter by status">
        {["open", "APPROVED", "REJECTED", "all"].map((f) => (
          <button key={f} className={filter === f ? "" : "secondary"} aria-pressed={filter === f} onClick={() => setFilter(f)}>
            {f === "open" ? `Needs review (${(counts.PENDING ?? 0) + (counts.UNCERTAIN ?? 0)})` : `${f} (${f === "all" ? cands.data?.length ?? 0 : counts[f] ?? 0})`}
          </button>
        ))}
      </div>
      <Status loading={cands.loading && !cands.data} error={cands.error} />
      {shown.map((c) => <CandidateCard key={`${c.id}-${c.status}-${c.edited}`} c={c} onChange={cands.reload} />)}
      {cands.data && shown.length === 0 && <p className="muted">Nothing here.</p>}
    </>
  );
}
