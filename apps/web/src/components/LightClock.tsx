"use client";

import { Dispatch, SetStateAction, useCallback, useEffect, useRef, useState } from "react";
import { ClockView, HeartbeatResponse, PlayerView, api, errorText, post } from "@/lib/api";

// The server measures real time from these heartbeats; the client never reports durations (ADR-0013).
const HEARTBEAT_MS = 30_000;

const TIMING_LABEL: Record<string, string> = { real_time: "Real time", rounds: "Rounds (1 hour = 10 rounds)", action_minutes: "Per action" };
const MULTIPLE_LABEL: Record<string, string> = { ride_along: "New light rides along", restart: "New light restarts the timer", independent: "Each light has its own timer" };

function mmss(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Keeps the real-time clock running while the play screen is visible and the player is active.
 * Returns `resume`, to call after an action response (the server pauses the clock while it works).
 */
export function useRealTimeClock(id: string, view: PlayerView | null, busy: boolean,
  setView: Dispatch<SetStateAction<PlayerView | null>>, reload: () => Promise<void>) {
  const lastActivity = useRef(Date.now());
  const syncedAt = useRef(Date.now());
  const [now, setNow] = useState(Date.now());
  const realTime = view?.campaign.status === "ACTIVE" && view.clock.timing === "real_time";
  const idleMs = (view?.clock.idle_pause_seconds ?? 300) * 1000;
  const active = () => document.visibilityState === "visible" && Date.now() - lastActivity.current < idleMs;

  const beat = useCallback(async (kind: "heartbeat" | "pause" = "heartbeat") => {
    try {
      const r = await post<HeartbeatResponse>(`/campaigns/${id}/clock/${kind}`);
      syncedAt.current = Date.now();
      setView((v) => (v ? { ...v, clock: r.clock, campaign: { ...v.campaign, version: r.campaign_version } } : v));
      if (r.changed) await reload(); // a light burned out: fetch the new events
    } catch {
      // A missed heartbeat only means less time is credited; the next one catches up.
    }
  }, [id, setView, reload]);

  useEffect(() => {
    if (!realTime) return;
    let wasActive = active();
    const onActivity = () => {
      lastActivity.current = Date.now();
      if (!wasActive && !busy) {
        wasActive = true;
        void beat(); // resume straight away after idling
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        void fetch(`/api/v1/campaigns/${id}/clock/pause`, { method: "POST", credentials: "same-origin", keepalive: true });
      } else {
        onActivity();
      }
    };
    const events = ["keydown", "pointerdown", "pointermove", "wheel", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, onActivity, { passive: true }));
    document.addEventListener("visibilitychange", onVisibility);
    if (!busy && active()) void beat();
    const heartbeat = window.setInterval(() => {
      wasActive = active();
      if (wasActive && !busy) void beat();
    }, HEARTBEAT_MS);
    const second = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, onActivity));
      document.removeEventListener("visibilitychange", onVisibility);
      window.clearInterval(heartbeat);
      window.clearInterval(second);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [realTime, busy, beat, id, idleMs]);

  const ticking = Boolean(realTime && view?.clock.running && !busy && typeof document !== "undefined" && active());
  const sinceSync = ticking ? Math.min((now - syncedAt.current) / 1000, HEARTBEAT_MS / 1000 + 5) : 0;
  return { resume: () => beat(), ticking, sinceSync };
}

export function LightClock({ id, clock, version, ticking, sinceSync, onState }: {
  id: string; clock: ClockView; version: number; ticking: boolean; sinceSync: number;
  onState: (v: PlayerView) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const realTime = clock.timing === "real_time";
  const lights = clock.lights.map((l) => ({ ...l, left: l.remaining_seconds - sinceSync }));
  const lit = lights.some((l) => l.left > 0);

  async function change(body: Record<string, string | number>) {
    try {
      onState(await api<PlayerView>(`/campaigns/${id}/time-settings`,
        { method: "PUT", body: JSON.stringify({ expected_version: version, ...body }) }));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <div className="stack" aria-label="Light and time">
      <div className="row">
        <span className={`badge ${lit ? "ok" : "danger"}`}>{lit ? "lit" : "total darkness"}</span>
        <span className="badge">minute {clock.minutes_elapsed + Math.floor((clock.pending_seconds + sinceSync) / 60)}</span>
        {realTime && <span className={`badge ${ticking ? "" : "warn"}`} title="Real time runs only while you are playing">
          {ticking ? "clock running" : "clock paused"}</span>}
      </div>
      {lights.map((l, i) => (
        <p key={i} className="mono" aria-live="off">{l.item_key}: {realTime ? mmss(l.left) : `${l.remaining_minutes} min`} left</p>
      ))}
      <details>
        <summary className="small">Time settings</summary>
        {clock.timing_options.length > 1 && (
          <label className="small">Light timing{" "}
            <select value={clock.timing ?? ""} onChange={(e) => change({ light_timing: e.target.value })}>
              {clock.timing_options.map((o) => <option key={o} value={o}>{TIMING_LABEL[o] ?? o}</option>)}
            </select>
          </label>
        )}
        {clock.multiple_light_options.length > 1 && (
          <label className="small">Lighting another source{" "}
            <select value={clock.multiple_lights ?? ""} onChange={(e) => change({ light_multiple: e.target.value })}>
              {clock.multiple_light_options.map((o) => <option key={o} value={o}>{MULTIPLE_LABEL[o] ?? o}</option>)}
            </select>
          </label>
        )}
        {realTime && (
          <label className="small">Pause after idle (minutes){" "}
            <select value={Math.round(clock.idle_pause_seconds / 60)}
              onChange={(e) => change({ idle_pause_seconds: Number(e.target.value) * 60 })}>
              {[1, 2, 5, 10, 15, 30].map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        )}
        <p className="small muted">{realTime
          ? "Light burns in real time while this screen is open and you are active. It pauses when you close or hide it, after the idle time above, and while the DM is thinking."
          : clock.timing === "rounds" ? "Each action or combat round takes one round of light." : "Each action costs a fixed number of game minutes."}</p>
        {error && <p className="error" role="alert">{error}</p>}
      </details>
    </div>
  );
}
