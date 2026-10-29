"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Disclosure = { provider: string; model: string; is_remote: boolean; message: string };

/** Always-visible statement of where player and adventure content is sent. */
export function AiDisclosure() {
  const [d, setD] = useState<Disclosure | null>(null);
  useEffect(() => {
    api<Disclosure>("/system/ai-disclosure").then(setD).catch(() => setD(null));
  }, []);
  if (!d) return null;
  return (
    <span className={`badge ${d.is_remote ? "danger" : "ok"}`} title={d.message} role="status">
      AI: {d.provider} {d.is_remote ? "(REMOTE: content leaves this server)" : "(local)"}
    </span>
  );
}
