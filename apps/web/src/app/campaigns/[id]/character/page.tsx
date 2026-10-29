"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, use, useState } from "react";
import { Status, useLoad } from "@/components/useLoad";
import { Adventure, PlayerView, api, errorText, patch, post } from "@/lib/api";

const ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"] as const;

type Profile = { key: string; entries: Record<string, { status: string; value: unknown }> };
type ClassDef = { key: string; name: string; base_hit_points: number };
type ItemDef = { key: string; name: string; slots: number };

export default function CharacterPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const view = useLoad(() => api<PlayerView>(`/campaigns/${id}`), [id]);
  const profiles = useLoad(() => api<Profile[]>("/rules/profiles"));
  const adventures = useLoad(() => api<Adventure[]>("/adventures"));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const v = view.data;
  const profile = profiles.data?.find((p) => p.key === v?.campaign.rules_profile);
  const usable = (k: string) => profile?.entries[k] && profile.entries[k].status !== "UNVERIFIED";
  const classes = (usable("character.classes") ? profile!.entries["character.classes"].value : []) as ClassDef[];
  const items = (usable("items.catalog") ? profile!.entries["items.catalog"].value : []) as ItemDef[];
  const range = (usable("ability.score_range") ? profile!.entries["ability.score_range"].value : null) as
    | { min: number; max: number } | null;

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const scores = Object.fromEntries(ABILITIES.map((a) => [a, Number(f.get(a))]));
    const starting_items = items
      .map((it) => ({ item_key: it.key, quantity: Number(f.get(`item-${it.key}`) || 0) }))
      .filter((it) => it.quantity > 0);
    setBusy(true);
    setError(null);
    try {
      await post(`/campaigns/${id}/character`, { name: f.get("name"), class_key: f.get("class_key"), ability_scores: scores, starting_items });
      await view.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function attach(adventureId: string) {
    try {
      await patch(`/campaigns/${id}`, { adventure_id: adventureId });
      await view.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await post(`/campaigns/${id}/start`, {});
      router.push(`/campaigns/${id}/play`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <>
      <p><Link href="/">← Campaigns</Link>{v?.campaign.status === "ACTIVE" && <> · <Link href={`/campaigns/${id}/play`}>Play</Link></>}</p>
      <Status loading={view.loading && !v} error={view.error} />
      {error && <p className="error" role="alert">{error}</p>}
      {v && (
        <>
          <h1>{v.campaign.name}: character</h1>
          {v.character ? (
            <section className="card stack" aria-label="Character sheet">
              <h2 style={{ margin: 0 }}>{v.character.name} <span className="badge">{v.character.class_key} · level {v.character.level}</span></h2>
              <div className="row">
                <span className="badge">HP {v.character.hp_current}/{v.character.hp_max}</span>
                <span className="badge">AC {v.character.armor_class}</span>
                {v.conditions.map((c) => <span key={c.key} className="badge danger">{c.key}</span>)}
              </div>
              <table>
                <thead><tr>{ABILITIES.map((a) => <th key={a}>{a}</th>)}</tr></thead>
                <tbody><tr>{ABILITIES.map((a) => <td key={a}>{v.character!.ability_scores[a]}</td>)}</tr></tbody>
              </table>
              <h3>Inventory</h3>
              <ul>{v.inventory.map((i) => <li key={i.item_key}>{i.name} × {i.quantity}</li>)}</ul>
              <details>
                <summary className="small">Rules applied ({v.character.rule_refs.length})</summary>
                <ul className="small mono">{v.character.rule_refs.map((r) => <li key={r}>{r}</li>)}</ul>
              </details>
            </section>
          ) : (
            <form className="card stack" onSubmit={create} aria-label="Create character">
              {!classes.length && <p className="error">This campaign&apos;s rules profile has no usable character rules. Character
                creation is blocked until those rules are verified.</p>}
              <div>
                <label htmlFor="cname">Name</label>
                <input id="cname" name="name" type="text" required maxLength={80} />
              </div>
              <div>
                <label htmlFor="class_key">Class</label>
                <select id="class_key" name="class_key" required>
                  {classes.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
                </select>
              </div>
              <fieldset>
                <legend>Ability scores {range && <span className="small muted">({range.min}–{range.max})</span>}</legend>
                <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(90px, 1fr))" }}>
                  {ABILITIES.map((a) => (
                    <div key={a}>
                      <label htmlFor={a}>{a}</label>
                      <input id={a} name={a} type="number" min={range?.min} max={range?.max} defaultValue={10} required />
                    </div>
                  ))}
                </div>
              </fieldset>
              <fieldset>
                <legend>Starting items</legend>
                <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
                  {items.map((it) => (
                    <div key={it.key}>
                      <label htmlFor={`item-${it.key}`}>{it.name} <span className="small muted">({it.slots} slot)</span></label>
                      <input id={`item-${it.key}`} name={`item-${it.key}`} type="number" min={0} max={99} defaultValue={0} />
                    </div>
                  ))}
                </div>
              </fieldset>
              <p className="small muted">The server validates everything and derives HP and AC; invalid input is rejected with
                every problem listed.</p>
              <button type="submit" disabled={busy || !classes.length}>Create character</button>
            </form>
          )}
          {v.campaign.status === "SETUP" && (
            <section className="card stack" aria-label="Start campaign">
              <h2 style={{ margin: 0 }}>Start</h2>
              <div>
                <label htmlFor="adv">Adventure</label>
                <select id="adv" value={v.campaign.adventure_id ?? ""} onChange={(e) => e.target.value && attach(e.target.value)}>
                  <option value="">(choose)</option>
                  {adventures.data?.map((a) => <option key={a.id} value={a.id}>{a.title}</option>)}
                </select>
              </div>
              <button onClick={start} disabled={busy || !v.character || !v.campaign.adventure_id}>Start in the first approved location</button>
            </section>
          )}
        </>
      )}
    </>
  );
}
