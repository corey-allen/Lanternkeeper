// Thin typed client for the Lanternkeeper API. All requests are same-origin (/api is proxied).

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "same-origin", cache: "no-store" });
  if (res.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = body?.error ?? {};
    throw new ApiError(res.status, err.code ?? "HTTP_ERROR", err.message ?? res.statusText, err.details ?? {});
  }
  return body as T;
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const issues = (e.details?.issues as unknown[] | undefined)?.map((i) => (typeof i === "string" ? i : JSON.stringify(i)));
    return issues?.length ? `${e.message}: ${issues.join("; ")}` : `${e.message} (${e.code})`;
  }
  return e instanceof Error ? e.message : String(e);
}

export function newIdempotencyKey(): string {
  return `act-${crypto.randomUUID()}`;
}

// ---- response types (subset of the API contract) ----------------------------------

export type Citation = { passage_id: string; document_id: string; page_number: number };
export type Claim = { claim_class: string; title: string; text: string; citations: Citation[]; source?: string | null };
export type DiceRoll = {
  id: string; actor: string; purpose: string; expression: string;
  dice: { sides: number; value: number; kept: boolean }[]; modifier: number; total: number; rule_refs: string[];
};
export type GameEvent = { sequence: number; type: string; summary: string; game_minute: number; campaign_version: number };

export type PlayerView = {
  campaign: { id: string; name: string; status: string; version: number; rules_profile: string; adventure_id: string | null };
  character: null | {
    id: string; name: string; class_key: string; level: number; ability_scores: Record<string, number>;
    hp_current: number; hp_max: number; armor_class: number; rule_refs: string[];
    ancestry_key: string | null; alignment: string | null; deity: string | null; background: string | null;
    title: string | null; xp: number; gear_slots: number | null; languages: string[]; gold_gp: number;
    pending: string[];
    features: { key: string; name: string; kind: string; uses_per: string | null; uses_remaining: number | null }[];
    modifiers: { target: string; mode: string; amount: number; from_stat: string | null; scope: Record<string, string>;
      source: string; label: string; folded: boolean }[];
  };
  inventory: { item_key: string; name: string; quantity: number; slots_each: number }[];
  conditions: { key: string; expires_at_minute: number | null }[];
  location: null | { id: string; name: string; description: string; citations: string[] };
  exits: { location_id: string; name: string; label: string }[];
  npcs: { id: string; name: string; disposition: string; description: string }[];
  enemies: { id: string; name: string; description: string }[];
  quests: { id: string; name: string; status: string; objective: string }[];
  discoveries: { id: string; type: string; name: string; text: string }[];
  clock: { minutes_elapsed: number; lights: { item_key: string; remaining_minutes: number }[]; is_lit: boolean };
  encounter: null | { round: number; participants: { name: string; side: string; status: string; hp?: number; max_hp?: number }[] };
  recent_events: GameEvent[];
  recent_dice: DiceRoll[];
};

export type ActionResponse = {
  action_id: string; status: string; replayed: boolean; campaign_version: number; intent: string;
  narration: string; narration_source: string; narration_note: string; outcome: string[]; claims: Claim[];
  dice: DiceRoll[]; events: GameEvent[]; error: null | { code: string; message: string };
  clarification: string | null; state: PlayerView;
};

export type CampaignSummary = {
  id: string; name: string; status: string; version: number; rules_profile: string; adventure_id: string | null;
  character: null | { name: string; hp_current: number; hp_max: number }; location: string | null;
  last_event: string | null; updated_at: string | null;
};

export type DocumentInfo = {
  id: string; adventure_id: string; display_name: string; sha256: string; size_bytes: number; page_count: number | null;
  document_kind: string; rights_status: string; rights_note: string; visibility: string; status: string; created_at: string;
  job: null | { id: string; status: string; attempts: number; max_attempts: number; error_code: string | null;
    error_message: string | null; stats: Record<string, number> };
  pages?: { page_number: number; method: string; chars: number }[];
  warnings?: { id: string; code: string; severity: string; message: string; page_number: number | null }[];
};

export type Adventure = {
  id: string; title: string; description: string; graph_version: number; documents: DocumentInfo[];
  entity_counts?: Record<string, number>;
};

export type Candidate = {
  id: string; type: string; name: string; data: Record<string, unknown>; status: string; edited: boolean;
  reviewer_note: string;
  evidence: { passage_id: string; page_number: number; ordinal: number; text?: string; injection_flags: string[] }[];
  warnings: { code: string; severity: string; message: string }[];
};
