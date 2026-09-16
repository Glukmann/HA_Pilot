import { useCallback, useEffect, useState } from "react";

import type { PilotClient } from "../api/client";
import type { VitrinePayload } from "../api/types";

const POLL_MS = 10_000;

/**
 * Polls vitrine/get on an interval and refetches right after a reconnect.
 * Requests are skipped while the transport is down (they would just pile
 * up in the client's queue).
 */
export function useVitrine(client: PilotClient): {
  vitrine: VitrinePayload | null;
  error: string | null;
  refresh: () => void;
} {
  const [vitrine, setVitrine] = useState<VitrinePayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (client.getConnectionState() !== "connected") return;
    client.request<VitrinePayload>("vitrine/get", {}).then(
      (payload) => {
        setVitrine(payload);
        setError(null);
      },
      (err: unknown) => {
        setError(err instanceof Error ? err.message : String(err));
      },
    );
  }, [client]);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_MS);
    const offConn = client.onConnectionChange((state) => {
      if (state === "connected") load();
    });
    return () => {
      clearInterval(interval);
      offConn();
    };
  }, [client, load]);

  return { vitrine, error, refresh: load };
}
