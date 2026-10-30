"use client";

import { useEffect, useState } from "react";
import { api, errorText, post } from "@/lib/api";

const STATS = ["STR", "DEX", "CON", "INT", "WIS", "CHA"];

type Choice = { param: string; allowed: string[] | null; prompt: string };
type OptionDef = {
  key: string; label: string; kind: string; choices: Choice[]; alternatives: OptionDef[];
  points: number | null; reason: string; text_ref: string;
};
type TalentTable = { dice: string; rows: { min: number; max: number; option: OptionDef }[] };
type ClassDef = { key: string; name: string; hit_die: string; features: OptionDef[]; talent_table: TalentTable | null;
  requires_deity: boolean };
type AncestryDef = { key: string; name: string; traits: OptionDef[] };
type Choices = Record<string, unknown>;
type Draft = {
  stage: string; stat_method: string; base_scores: Record<string, number> | null; rerolls_used: number;
  ancestry_key: string | null; class_key: string | null; trait_choices: Record<string, Choices>;
  hp_roll_total: number | null; talents: { roll_id: string; total: number; row_index: number; option_key: string;
    choices: Choices }[]; gold_amount: number | null; gold_unit: string | null; name: string;
  alignment: string | null; background: string | null; deity: string | null; languages: string[];
  purchases: { item_key: string; quantity: number }[];
};
type ShopItem = { key: string; name: string; category: string; cost: { amount: number; unit: string } | null;
  slots: number; quantity_per_slot: number };
type DraftView = {
  draft: Draft | null; can_reroll_stats?: boolean;
  options: { ancestries: AncestryDef[]; classes: ClassDef[]; alignments: string[];
    deities: { key: string; name: string; alignment: string }[]; backgrounds: { key: string; name: string }[];
    languages: { common: string[]; rare: string[] } | null; items?: ShopItem[] };
  preview?: { ready: boolean; issues: string[]; build?: Record<string, unknown> };
};

/** Renders the controls for one option's open choices (recursively for alternatives/rows). */
function OptionChoices({ option, value, onChange, table }: {
  option: OptionDef; value: Choices; onChange: (v: Choices) => void; table?: TalentTable | null;
}) {
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  if (option.kind === "deferred" || option.kind === "unsupported") {
    return <p className="small muted">{option.reason || "Not supported yet"}; recorded for later.</p>;
  }
  if (option.kind === "alternatives") {
    const idx = typeof value.alternative === "number" ? value.alternative : -1;
    return (
      <fieldset className="stack">
        <legend className="small">{option.label}: choose one</legend>
        {option.alternatives.map((alt, i) => (
          <label key={alt.key} className="check">
            <input type="radio" name={`alt-${option.key}`} checked={idx === i} onChange={() => set("alternative", i)} />
            <span>{alt.label}</span>
          </label>
        ))}
        {idx >= 0 && <OptionChoices option={option.alternatives[idx]} value={value} onChange={onChange} table={table} />}
      </fieldset>
    );
  }
  if (option.kind === "choose_row" && table) {
    const row = typeof value.row === "number" ? value.row : -1;
    const rowChoices = (value.row_choices as Choices) ?? {};
    return (
      <div className="stack">
        <label className="small">Talent
          <select value={row} onChange={(e) => set("row", Number(e.target.value))}>
            <option value={-1}>(choose)</option>
            {table.rows.map((r, i) => <option key={i} value={i}>{r.min}{r.max !== r.min ? `-${r.max}` : ""}: {r.option.label}</option>)}
          </select>
        </label>
        {row >= 0 && <OptionChoices option={table.rows[row].option} value={rowChoices}
          onChange={(v) => set("row_choices", v)} table={table} />}
      </div>
    );
  }
  if (option.kind === "distribute_stat_points") {
    const dist = (value.distribute as Record<string, number>) ?? {};
    return (
      <div className="row small" role="group" aria-label={`Distribute ${option.points} points`}>
        {STATS.map((s) => (
          <label key={s}>{s}
            <input type="number" min={0} max={option.points ?? 0} value={dist[s] ?? 0} style={{ width: 64 }}
              onChange={(e) => set("distribute", { ...dist, [s]: Number(e.target.value) })} />
          </label>
        ))}
      </div>
    );
  }
  if (!option.choices.length) return null;
  return (
    <div className="row small">
      {option.choices.map((c) => (
        <label key={c.param}>{c.prompt || c.param}
          {c.allowed || c.param === "stat" ? (
            <select value={(value[c.param] as string) ?? ""} onChange={(e) => set(c.param, e.target.value || undefined)}>
              <option value="">(choose)</option>
              {(c.allowed ?? STATS).map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          ) : (
            <input type="text" value={(value[c.param] as string) ?? ""} onChange={(e) => set(c.param, e.target.value)} />
          )}
        </label>
      ))}
    </div>
  );
}

export function DraftCreator({ campaignId, onDone }: { campaignId: string; onDone: () => void }) {
  const base = `/campaigns/${campaignId}/character/draft`;
  const [view, setView] = useState<DraftView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [origin, setOrigin] = useState({ ancestry_key: "", class_key: "" });
  const [traits, setTraits] = useState<Record<string, Choices>>({});
  const [talents, setTalents] = useState<Record<number, Choices>>({});
  const [details, setDetails] = useState({ name: "", alignment: "", background: "", deity: "", languages: [] as string[] });
  const [cart, setCart] = useState<Record<string, number>>({});

  function adopt(v: DraftView) {
    setView(v);
    const d = v.draft;
    if (!d) return;
    setOrigin({ ancestry_key: d.ancestry_key ?? "", class_key: d.class_key ?? "" });
    setTraits(d.trait_choices ?? {});
    setTalents(Object.fromEntries(d.talents.map((t, i) => [i, t.choices])));
    setDetails({ name: d.name, alignment: d.alignment ?? "", background: d.background ?? "", deity: d.deity ?? "",
      languages: d.languages });
    setCart(Object.fromEntries((d.purchases ?? []).map((p) => [p.item_key, p.quantity])));
  }

  useEffect(() => {
    api<DraftView>(base).then(adopt).catch((e) => setError(errorText(e)));
  }, [base]);

  async function run(fn: () => Promise<DraftView>) {
    setBusy(true);
    setError(null);
    try {
      adopt(await fn());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const put = (path: string, body: unknown) => api<DraftView>(`${base}${path}`, { method: "PUT", body: JSON.stringify(body) });
  const saveChoices = () => put("/details", {
    ...details, alignment: details.alignment || null, background: details.background || null,
    deity: details.deity || null, trait_choices: traits, talent_choices: talents,
    purchases: Object.entries(cart).filter(([, q]) => q > 0).map(([item_key, quantity]) => ({ item_key, quantity })),
  });

  if (!view) return <p className="muted">{error ?? "Loading…"}</p>;
  const d = view.draft;
  const o = view.options;
  const klass = o.classes.find((c) => c.key === (d?.class_key || origin.class_key));
  const ancestry = o.ancestries.find((a) => a.key === (d?.ancestry_key || origin.ancestry_key));
  const allLangs = [...(o.languages?.common ?? []), ...(o.languages?.rare ?? [])];

  if (!d) {
    return (
      <section className="card stack" aria-label="Begin character creation">
        <p>Stats, hit points, talents and starting gold are rolled by the server and recorded. Each roll is final
          unless the rules allow a reroll.</p>
        {error && <p className="error" role="alert">{error}</p>}
        <button disabled={busy} onClick={() => run(() => post<DraftView>(base))}>Begin character creation</button>
      </section>
    );
  }

  return (
    <div className="stack">
      {error && <p className="error" role="alert">{error}</p>}
      <section className="card stack" aria-label="Stats">
        <h2 style={{ margin: 0 }}>1. Stats</h2>
        {d.base_scores ? (
          <table><thead><tr>{STATS.map((s) => <th key={s}>{s}</th>)}</tr></thead>
            <tbody><tr>{STATS.map((s) => <td key={s}>{d.base_scores![s]}</td>)}</tr></tbody></table>
        ) : <p className="muted">This profile uses entered scores; use the manual form instead.</p>}
        {view.can_reroll_stats && (
          <button className="secondary" disabled={busy} onClick={() => run(() => post<DraftView>(`${base}/reroll-stats`))}>
            Roll a new set (allowed for these scores)</button>
        )}
      </section>

      <section className="card stack" aria-label="Ancestry and class">
        <h2 style={{ margin: 0 }}>2. Ancestry and class</h2>
        <div className="row">
          {o.ancestries.length > 0 && (
            <label>Ancestry
              <select value={origin.ancestry_key} disabled={d.stage === "rolled"}
                onChange={(e) => setOrigin({ ...origin, ancestry_key: e.target.value })}>
                <option value="">(choose)</option>
                {o.ancestries.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}
              </select>
            </label>
          )}
          <label>Class
            <select value={origin.class_key} disabled={d.stage === "rolled"}
              onChange={(e) => setOrigin({ ...origin, class_key: e.target.value })}>
              <option value="">(choose)</option>
              {o.classes.map((c) => <option key={c.key} value={c.key}>{c.name} ({c.hit_die})</option>)}
            </select>
          </label>
        </div>
        {[...(ancestry?.traits ?? []), ...(klass?.features ?? [])].map((opt) => (
          <div key={opt.key}>
            <strong className="small">{opt.label}</strong>{opt.text_ref && <span className="small muted"> ({opt.text_ref})</span>}
            <OptionChoices option={opt} value={traits[opt.key] ?? {}} onChange={(v) => setTraits({ ...traits, [opt.key]: v })} />
          </div>
        ))}
        {d.stage !== "rolled" && (
          <button disabled={busy || !origin.class_key} onClick={() => run(async () => {
            await put("/origin", { ancestry_key: origin.ancestry_key || null, class_key: origin.class_key });
            await saveChoices();
            return post<DraftView>(`${base}/roll`);
          })}>Lock in and roll hit points and talents</button>
        )}
      </section>

      {d.stage === "rolled" && (
        <section className="card stack" aria-label="Rolls and talents">
          <h2 style={{ margin: 0 }}>3. Rolls</h2>
          <p className="small">Hit die result: <strong>{d.hp_roll_total}</strong>
            {d.gold_amount !== null && <> · Starting gold: <strong>{d.gold_amount} {d.gold_unit}</strong></>}</p>
          {d.talents.map((t, i) => {
            const row = klass?.talent_table?.rows[t.row_index];
            return row ? (
              <div key={t.roll_id} className="stack">
                <strong className="small">Talent roll {t.total}: {row.option.label}</strong>
                <OptionChoices option={row.option} value={talents[i] ?? {}} table={klass?.talent_table}
                  onChange={(v) => setTalents({ ...talents, [i]: v })} />
              </div>
            ) : null;
          })}
        </section>
      )}

      {d.stage === "rolled" && d.gold_amount !== null && (o.items ?? []).length > 0 && (
        <section className="card stack" aria-label="Starting gear">
          <h2 style={{ margin: 0 }}>Starting gear</h2>
          <p className="small">Spend up to {d.gold_amount} {d.gold_unit}. Change is made automatically. Slots and
            cost are checked when you save.</p>
          <table>
            <thead><tr><th>Item</th><th>Cost</th><th>Slots</th><th>Qty</th></tr></thead>
            <tbody>
              {(o.items ?? []).map((it) => (
                <tr key={it.key}>
                  <td>{it.name} <span className="small muted">{it.category}</span></td>
                  <td className="small">{it.cost ? `${it.cost.amount} ${it.cost.unit}` : "-"}</td>
                  <td className="small">{it.slots}{it.quantity_per_slot > 1 ? ` per ${it.quantity_per_slot}` : ""}</td>
                  <td><input type="number" min={0} max={99} value={cart[it.key] ?? 0} style={{ width: 64 }}
                    aria-label={`Quantity of ${it.name}`}
                    onChange={(e) => setCart({ ...cart, [it.key]: Number(e.target.value) })} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="card stack" aria-label="Details">
        <h2 style={{ margin: 0 }}>4. Details</h2>
        <div className="row">
          <label>Name<input type="text" value={details.name} maxLength={80}
            onChange={(e) => setDetails({ ...details, name: e.target.value })} /></label>
          {o.alignments.length > 0 && (
            <label>Alignment
              <select value={details.alignment} onChange={(e) => setDetails({ ...details, alignment: e.target.value })}>
                <option value="">(choose)</option>
                {o.alignments.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </label>
          )}
          {o.backgrounds.length > 0 && (
            <label>Background
              <select value={details.background} onChange={(e) => setDetails({ ...details, background: e.target.value })}>
                <option value="">(none)</option>
                {o.backgrounds.map((b) => <option key={b.key} value={b.key}>{b.name}</option>)}
              </select>
            </label>
          )}
          {o.deities.length > 0 && (
            <label>Deity
              <select value={details.deity} onChange={(e) => setDetails({ ...details, deity: e.target.value })}>
                <option value="">(none)</option>
                {o.deities.map((g) => <option key={g.key} value={g.key}>{g.name} ({g.alignment})</option>)}
              </select>
            </label>
          )}
        </div>
        {allLangs.length > 0 && (
          <fieldset><legend className="small">Extra languages (if your ancestry or class grants choices)</legend>
            <div className="row small">
              {allLangs.map((l) => (
                <label key={l} className="check">
                  <input type="checkbox" checked={details.languages.includes(l)} onChange={(e) => setDetails({
                    ...details, languages: e.target.checked ? [...details.languages, l] : details.languages.filter((x) => x !== l),
                  })} />{l}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <button className="secondary" disabled={busy} onClick={() => run(saveChoices)}>Save choices and check</button>
      </section>

      <section className="card stack" aria-label="Preview" aria-live="polite">
        <h2 style={{ margin: 0 }}>5. Finish</h2>
        {view.preview?.ready ? (
          <p className="small">Ready: HP {String(view.preview.build?.max_hp)}, AC {String(view.preview.build?.armor_class)},
            gear slots {String(view.preview.build?.gear_slots)}. Coins left: {Object.entries(
              (view.preview.build?.coins as Record<string, number>) ?? {}).map(([k, v]) => `${v} ${k}`).join(", ") || "none"}.</p>
        ) : (
          <ul className="small">{view.preview?.issues.map((i, k) => <li key={k}>{i}</li>)}</ul>
        )}
        <button disabled={busy || !view.preview?.ready} onClick={async () => {
          setBusy(true);
          try {
            await saveChoices();
            await post(`${base}/finalize`);
            onDone();
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}>Create character</button>
      </section>
    </div>
  );
}
