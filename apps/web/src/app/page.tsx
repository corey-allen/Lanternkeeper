"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { Status, useLoad } from "@/components/useLoad";
import { Adventure, CampaignSummary, api, del, errorText, post } from "@/lib/api";

type Profile = { key: string; name: string; description: string; requires_house_rule_acceptance: boolean };

export default function Dashboard() {
  const campaigns = useLoad(() => api<CampaignSummary[]>("/campaigns"));
  const adventures = useLoad(() => api<Adventure[]>("/adventures"));
  const profiles = useLoad(() => api<Profile[]>("/rules/profiles"));
  const [profileKey, setProfileKey] = useState("lk-dev-house-rules");
  const [error, setError] = useState<string | null>(null);
  const profile = profiles.data?.find((p) => p.key === profileKey);

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const formEl = e.currentTarget;
    const f = new FormData(formEl);
    setError(null);
    try {
      await post("/campaigns", {
        name: f.get("name"),
        rules_profile: profileKey,
        accept_house_rules: f.get("accept") === "on",
        adventure_id: f.get("adventure") || null,
      });
      formEl.reset();
      await campaigns.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function remove(c: CampaignSummary) {
    if (!confirm(`Delete campaign "${c.name}" and its entire history? This cannot be undone.`)) return;
    try {
      await del(`/campaigns/${c.id}`);
      await campaigns.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <h1>Campaigns</h1>
      <Status loading={campaigns.loading} error={campaigns.error} />
      <div className="grid">
        {campaigns.data?.map((c) => (
          <article key={c.id} className="card stack" aria-label={c.name}>
            <h2 style={{ margin: 0 }}>{c.name}</h2>
            <div className="row small">
              <span className="badge">{c.status}</span>
              <span className="badge">v{c.version}</span>
              <span className="badge">{c.rules_profile}</span>
            </div>
            <p className="small muted">
              {c.character ? `${c.character.name} · HP ${c.character.hp_current}/${c.character.hp_max}` : "No character yet"}
              {c.location ? ` · ${c.location}` : ""}
            </p>
            {c.last_event && <p className="small">Last: {c.last_event}</p>}
            <div className="row">
              {c.status === "ACTIVE" ? (
                <Link className="button" href={`/campaigns/${c.id}/play`}>Resume</Link>
              ) : (
                <Link className="button" href={`/campaigns/${c.id}/character`}>Set up</Link>
              )}
              <Link className="button secondary" href={`/campaigns/${c.id}/character`}>Character</Link>
              <button className="danger" onClick={() => remove(c)}>Delete</button>
            </div>
          </article>
        ))}
        {campaigns.data?.length === 0 && <p className="muted">No campaigns yet.</p>}
      </div>

      <h2>New campaign</h2>
      <form className="card stack" onSubmit={create} style={{ maxWidth: 560 }}>
        <div>
          <label htmlFor="name">Name</label>
          <input id="name" name="name" type="text" required maxLength={120} />
        </div>
        <div>
          <label htmlFor="adventure">Adventure</label>
          <select id="adventure" name="adventure" defaultValue="">
            <option value="">(choose later)</option>
            {adventures.data?.map((a) => <option key={a.id} value={a.id}>{a.title}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="profile">Rules profile</label>
          <select id="profile" value={profileKey} onChange={(e) => setProfileKey(e.target.value)}>
            {profiles.data?.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
          {profile && <p className="small muted">{profile.description}</p>}
        </div>
        {profile?.requires_house_rule_acceptance && (
          <label className="check">
            <input type="checkbox" name="accept" required />
            <span>I accept that this profile consists of house rules chosen by the project, not official game
              rules. <Link href="/rules">Review them</Link>.</span>
          </label>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <button type="submit">Create campaign</button>
      </form>
    </>
  );
}
