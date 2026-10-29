"use client";

import { useCallback, useEffect, useState } from "react";
import { errorText } from "@/lib/api";

/** Load data on mount with explicit loading and error states; `reload` refetches. */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await run());
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [run]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export function Status({ loading, error }: { loading: boolean; error: string | null }) {
  if (error) return <p className="error" role="alert">{error}</p>;
  if (loading) return <p className="muted" aria-live="polite">Loading…</p>;
  return null;
}
