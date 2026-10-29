"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { Status, useLoad } from "@/components/useLoad";
import { Adventure, DocumentInfo, api, del, errorText, post } from "@/lib/api";

const statusTone = (s: string) => (s === "READY_FOR_REVIEW" ? "ok" : s === "FAILED" ? "danger" : "warn");

function UploadForm({ adventure, onDone }: { adventure: Adventure; onDone: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const data = new FormData(formEl);
    data.set("rights_acknowledged", data.get("rights_acknowledged") === "on" ? "true" : "false");
    setBusy(true);
    setError(null);
    try {
      await api<DocumentInfo>(`/adventures/${adventure.id}/documents`, { method: "POST", body: data });
      formEl.reset();
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  const id = adventure.id;
  return (
    <form onSubmit={submit} className="stack" aria-label={`Upload a PDF to ${adventure.title}`}>
      <div>
        <label htmlFor={`file-${id}`}>PDF (adventure module or zine)</label>
        <input id={`file-${id}`} name="file" type="file" accept="application/pdf,.pdf" required />
      </div>
      <div className="row">
        <div style={{ flex: 1, minWidth: 200 }}>
          <label htmlFor={`kind-${id}`}>Document kind</label>
          <select id={`kind-${id}`} name="document_kind" defaultValue="ADVENTURE_MODULE">
            <option value="ADVENTURE_MODULE">Adventure module</option>
            <option value="ZINE">Zine</option>
            <option value="OTHER">Other</option>
          </select>
        </div>
        <div style={{ flex: 1, minWidth: 200 }}>
          <label htmlFor={`rights-${id}`}>Your rights basis</label>
          <select id={`rights-${id}`} name="rights_status" defaultValue="PERSONAL_COPY" required>
            <option value="ORIGINAL_WORK">My original work</option>
            <option value="PERSONAL_COPY">A copy I own, for private use</option>
            <option value="LICENSED">Licensed to me for this use</option>
            <option value="UNKNOWN">Unsure</option>
          </select>
        </div>
      </div>
      <div>
        <label htmlFor={`note-${id}`}>Rights note (optional)</label>
        <input id={`note-${id}`} name="rights_note" type="text" maxLength={2000} />
      </div>
      <label className="check">
        <input type="checkbox" name="rights_acknowledged" required />
        <span>I have a legitimate basis to upload and process this document privately. I understand this
          acknowledgement is recorded but is not a legal determination, and that the file stays private to this
          installation.</span>
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? "Uploading…" : "Upload and process"}</button>
    </form>
  );
}

export default function Library() {
  const adventures = useLoad(() => api<Adventure[]>("/adventures"));
  const [error, setError] = useState<string | null>(null);

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const f = new FormData(formEl);
    try {
      await post("/adventures", { title: f.get("title"), description: f.get("description") ?? "" });
      formEl.reset();
      await adventures.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function removeDoc(d: DocumentInfo) {
    if (!confirm(`Delete "${d.display_name}", its extracted text, and entities that only it supports?`)) return;
    try {
      await del(`/documents/${d.id}`);
      await adventures.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <h1>Adventure library</h1>
      <p className="notice small">Uploaded documents are private to this installation. They are never exposed
        through public routes and are excluded from campaign exports.</p>
      <Status loading={adventures.loading} error={adventures.error} />
      {error && <p className="error" role="alert">{error}</p>}
      {adventures.data?.map((a) => (
        <section key={a.id} className="card stack" aria-labelledby={`adv-${a.id}`}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2 id={`adv-${a.id}`} style={{ margin: 0 }}>{a.title}</h2>
            <div className="row">
              <Link className="button secondary" href={`/adventures/${a.id}/review`}>Review candidates</Link>
              <Link className="button secondary" href={`/adventures/${a.id}/graph`}>Graph workbench</Link>
            </div>
          </div>
          <p className="small muted">
            Approved: {Object.entries(a.entity_counts ?? {}).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ") || "nothing yet"}
          </p>
          <table>
            <thead><tr><th>Document</th><th>Status</th><th>Rights</th><th></th></tr></thead>
            <tbody>
              {a.documents.map((d) => (
                <tr key={d.id}>
                  <td><Link href={`/library/documents/${d.id}`}>{d.display_name}</Link>
                    <div className="small muted">{d.page_count ?? "?"} pages · {(d.size_bytes / 1024).toFixed(0)} KB</div></td>
                  <td><span className={`badge ${statusTone(d.status)}`}>{d.status}</span>
                    {d.job && <div className="small muted">job {d.job.status}{d.job.error_code ? `: ${d.job.error_code}` : ""}</div>}</td>
                  <td className="small">{d.rights_status}<div className="muted">{d.visibility}</div></td>
                  <td><button className="danger" onClick={() => removeDoc(d)}>Delete</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <details>
            <summary>Upload a PDF</summary>
            <UploadForm adventure={a} onDone={adventures.reload} />
          </details>
        </section>
      ))}
      <h2>New adventure</h2>
      <form className="card stack" onSubmit={create} style={{ maxWidth: 560 }}>
        <div>
          <label htmlFor="title">Title</label>
          <input id="title" name="title" type="text" required maxLength={200} />
        </div>
        <div>
          <label htmlFor="description">Notes</label>
          <input id="description" name="description" type="text" maxLength={2000} />
        </div>
        <button type="submit">Create adventure</button>
      </form>
    </>
  );
}
