"use client";

import Link from "next/link";
import { FormEvent, ReactNode, use, useEffect, useRef, useState } from "react";
import { LightClock, useRealTimeClock } from "@/components/LightClock";
import { Status, useLoad } from "@/components/useLoad";
import { ActionResponse, ApiError, PlayerView, api, errorText, newIdempotencyKey, post } from "@/lib/api";

type Turn = { input: string; response?: ActionResponse; error?: string };

const CLAIM_TONE: Record<string, string> = {
  VERIFIED_SOURCE: "ok", VERIFIED_RULE: "ok", HOUSE_RULE: "warn", CAMPAIGN_FACT: "", UNKNOWN: "danger",
  INFERENCE: "warn", IMPROVISATION: "warn",
};

function TurnView({ t }: { t: Turn }) {
  const r = t.response;
  return (
    <div className="turn">
      <p className="you">&gt; {t.input}</p>
      {t.error && <p className="error" role="alert">{t.error}</p>}
      {r && (
        <>
          <div className="row small">
            <span className="badge">{r.status}</span>
            {r.replayed && <span className="badge warn">replayed</span>}
            {r.narration_source !== "model" && <span className="badge warn">narration: {r.narration_source}</span>}
          </div>
          <p>{r.narration}</p>
          {r.clarification && <p className="notice">{r.clarification}</p>}
          {r.error && <p className="notice">{r.error.message} <span className="small mono">({r.error.code})</span></p>}
          {r.dice.length > 0 && (
            <ul className="dice" aria-label="Dice rolled">
              {r.dice.map((d) => (
                <li key={d.id}>{d.actor} · {d.purpose}: {d.expression} [{d.dice.map((x) => (x.kept ? x.value : `(${x.value})`)).join(", ")}]
                  {d.modifier ? ` ${d.modifier > 0 ? "+" : ""}${d.modifier}` : ""} = <strong>{d.total}</strong></li>
              ))}
            </ul>
          )}
          <details className="claims">
            <summary>Sources and outcome ({r.claims.length})</summary>
            <p className="small muted">{r.narration_note}</p>
            <ul className="small">
              {r.claims.map((c, i) => (
                <li key={i}><span className={`badge ${CLAIM_TONE[c.claim_class] ?? ""}`}>{c.claim_class}</span>{" "}
                  <strong>{c.title}</strong>: {c.text}
                  {c.citations.length > 0 && <span className="muted"> — {c.citations.map((x) => `p.${x.page_number}`).join(", ")}</span>}</li>
              ))}
            </ul>
          </details>
        </>
      )}
    </div>
  );
}

function Sidebar({ v, clock }: { v: PlayerView; clock: ReactNode }) {
  return (
    <aside className="stack" aria-label="Status">
      <section className="card">
        <h2 style={{ marginTop: 0 }}>{v.location?.name ?? "Nowhere"}</h2>
        <p className="small">{v.location?.description}</p>
        <h3>Exits</h3>
        <ul className="small">{v.exits.map((x) => <li key={x.location_id}>{x.label ? `${x.label}: ` : ""}{x.name}</li>)}</ul>
        {v.npcs.length > 0 && <><h3>People here</h3>
          <ul className="small">{v.npcs.map((n) => <li key={n.id}>{n.name} <span className="badge">{n.disposition}</span></li>)}</ul></>}
        {v.enemies.length > 0 && <><h3>Threats</h3><ul className="small">{v.enemies.map((e) => <li key={e.id}>{e.name}</li>)}</ul></>}
      </section>
      {v.character && (
        <section className="card small">
          <h3 style={{ marginTop: 0 }}>{v.character.name}</h3>
          <div className="row">
            <span className="badge">HP {v.character.hp_current}/{v.character.hp_max}</span>
            <span className="badge">AC {v.character.armor_class}</span>
          </div>
          {clock}
          {v.conditions.map((c) => <span key={c.key} className="badge danger">{c.key}</span>)}
          <h3>Inventory</h3>
          <ul>{v.inventory.map((i) => <li key={i.item_key}>{i.name} × {i.quantity}</li>)}</ul>
        </section>
      )}
      {v.encounter && (
        <section className="card small" aria-label="Combat">
          <h3 style={{ marginTop: 0 }}>Combat · round {v.encounter.round}</h3>
          <ol>{v.encounter.participants.map((p, i) => <li key={i}>{p.name} ({p.status}){p.hp !== undefined ? ` HP ${p.hp}/${p.max_hp}` : ""}</li>)}</ol>
        </section>
      )}
      <section className="card small">
        <h3 style={{ marginTop: 0 }}>Quests</h3>
        {v.quests.length ? <ul>{v.quests.map((q) => <li key={q.id}><span className="badge">{q.status}</span> {q.name}: {q.objective}</li>)}</ul>
          : <p className="muted">None discovered.</p>}
        <h3>Discoveries</h3>
        {v.discoveries.length ? <ul>{v.discoveries.map((d) => <li key={d.id}><strong>{d.name}</strong>: {d.text}</li>)}</ul>
          : <p className="muted">Nothing yet.</p>}
      </section>
      <section className="card small">
        <h3 style={{ marginTop: 0 }}>Recent events</h3>
        <ol reversed>{v.recent_events.slice(0, 8).map((e) => <li key={e.sequence}>{e.summary}</li>)}</ol>
      </section>
    </aside>
  );
}

export default function PlayPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const view = useLoad(() => api<PlayerView>(`/campaigns/${id}`), [id]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const pending = useRef<{ text: string; key: string } | null>(null);
  const logEnd = useRef<HTMLDivElement>(null);
  const realTime = useRealTimeClock(id, view.data, busy, view.setData, view.reload);

  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, which React would treat as a cleanup.
    logEnd.current?.scrollIntoView({ block: "end" });
  }, [turns]);

  async function send(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || !view.data) return;
    // Reuse the idempotency key when retrying the same text after a failure, so it can never apply twice.
    const key = pending.current?.text === text ? pending.current.key : newIdempotencyKey();
    pending.current = { text, key };
    setBusy(true);
    try {
      const r = await post<ActionResponse>(`/campaigns/${id}/actions`,
        { input: text, idempotency_key: key, expected_version: view.data.campaign.version });
      setTurns((t) => [...t, { input: text, response: r }]);
      view.setData(r.state);
      void realTime.resume(); // the server paused the clock while it resolved the action
      pending.current = null;
      setInput("");
    } catch (err) {
      const conflict = err instanceof ApiError && err.code === "VERSION_CONFLICT";
      setTurns((t) => [...t, { input: text, error: conflict ? "The campaign changed elsewhere; state reloaded. Try again." : errorText(err) }]);
      if (conflict) {
        pending.current = null;
        await view.reload();
      }
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    try {
      const s = await post<{ campaign_version: number; state_hash: string }>(`/campaigns/${id}/snapshots`, { label: "manual save" });
      setSaved(`Saved checkpoint at version ${s.campaign_version} (${s.state_hash.slice(0, 10)}…)`);
    } catch (err) {
      setSaved(errorText(err));
    }
  }

  const v = view.data;
  return (
    <>
      <p><Link href="/">← Campaigns</Link> · <Link href={`/campaigns/${id}/character`}>Character</Link> ·{" "}
        <Link href={`/campaigns/${id}/console`}>DM console (spoilers)</Link></p>
      <Status loading={view.loading && !v} error={view.error} />
      {v && v.campaign.status !== "ACTIVE" && (
        <p className="notice">This campaign has not started. <Link href={`/campaigns/${id}/character`}>Set it up</Link>.</p>
      )}
      {v && v.campaign.status === "ACTIVE" && (
        <div className="play">
          <section aria-label="Adventure log">
            <h1>{v.campaign.name}</h1>
            <div className="card log" aria-live="polite">
              {turns.length === 0 && (
                <p className="muted">You are at {v.location?.name}. Every action you take is saved as soon as it resolves.
                  Try “What does this place look like?”</p>
              )}
              {turns.map((t, i) => <TurnView key={i} t={t} />)}
              <div ref={logEnd} />
            </div>
            <form onSubmit={send} className="row" style={{ marginTop: 8 }}>
              <label htmlFor="action" className="sr-only">Your action</label>
              <input id="action" type="text" value={input} onChange={(e) => setInput(e.target.value)} maxLength={2000}
                placeholder="What do you do?" autoComplete="off" style={{ flex: 1 }} disabled={busy} />
              <button type="submit" disabled={busy || !input.trim()}>{busy ? "Resolving…" : "Act"}</button>
            </form>
            <div className="row small" style={{ marginTop: 8 }}>
              <button className="secondary" onClick={save}>Save checkpoint</button>
              <a className="button secondary" href={`/api/v1/campaigns/${id}/export`} download={`campaign-${id}.json`}>Export campaign</a>
              <span className="muted">version {v.campaign.version}</span>
              {saved && <span role="status">{saved}</span>}
            </div>
          </section>
          <Sidebar v={v} clock={<LightClock id={id} clock={v.clock} version={v.campaign.version}
            ticking={realTime.ticking} sinceSync={realTime.sinceSync} onState={view.setData} />} />
        </div>
      )}
    </>
  );
}
