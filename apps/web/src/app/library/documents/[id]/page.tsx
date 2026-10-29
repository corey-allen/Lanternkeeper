"use client";

import Link from "next/link";
import { use, useEffect } from "react";
import { Status, useLoad } from "@/components/useLoad";
import { DocumentInfo, api, errorText, post } from "@/lib/api";

const tone = (s: string) => (s === "error" ? "danger" : s === "warning" ? "warn" : "ok");

export default function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const doc = useLoad(() => api<DocumentInfo>(`/documents/${id}`), [id]);
  const active = doc.data?.job && ["QUEUED", "RUNNING"].includes(doc.data.job.status);

  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => void doc.reload(), 2000);
    return () => clearInterval(t);
  }, [active, doc.reload]);

  async function reprocess() {
    try {
      await post(`/documents/${id}/reprocess`);
      await doc.reload();
    } catch (e) {
      alert(errorText(e));
    }
  }

  const d = doc.data;
  return (
    <>
      <p><Link href="/library">← Library</Link></p>
      <Status loading={doc.loading && !d} error={doc.error} />
      {d && (
        <>
          <h1>{d.display_name}</h1>
          <div className="card stack">
            <div className="row">
              <span className="badge">{d.status}</span>
              {d.job && <span className="badge">job {d.job.status} · attempt {d.job.attempts}/{d.job.max_attempts}</span>}
              <span className="badge">{d.rights_status}</span>
              <span className="badge">{d.visibility}</span>
            </div>
            {active && <p aria-live="polite">Processing… this page refreshes automatically.</p>}
            {d.job?.error_code && <p className="error">{d.job.error_code}: {d.job.error_message}</p>}
            <p className="small mono">sha256 {d.sha256}</p>
            {d.job?.stats && Object.keys(d.job.stats).length > 0 && (
              <p className="small">{Object.entries(d.job.stats).map(([k, v]) => `${v} ${k}`).join(" · ")}</p>
            )}
            <div className="row">
              <Link className="button" href={`/adventures/${d.adventure_id}/review`}>Review extracted candidates</Link>
              <button className="secondary" onClick={reprocess} disabled={!!active}>Reprocess</button>
            </div>
          </div>
          <h2>Pages</h2>
          <table>
            <thead><tr><th>Page</th><th>Extraction</th><th>Characters</th></tr></thead>
            <tbody>
              {d.pages?.map((p) => (
                <tr key={p.page_number}><td>{p.page_number}</td>
                  <td><span className={`badge ${p.method === "NONE" ? "danger" : p.method === "OCR" ? "warn" : "ok"}`}>{p.method}</span></td>
                  <td>{p.chars}</td></tr>
              ))}
            </tbody>
          </table>
          <h2>Warnings ({d.warnings?.length ?? 0})</h2>
          <ul>
            {d.warnings?.map((w) => (
              <li key={w.id}><span className={`badge ${tone(w.severity)}`}>{w.code}</span>{" "}
                {w.page_number ? `p.${w.page_number}: ` : ""}{w.message}</li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}
