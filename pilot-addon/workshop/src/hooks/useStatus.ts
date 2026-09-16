import { useCallback, useEffect, useState } from "react";

import type { PilotClient } from "../api/client";
import type { StatusSnapshot } from "../api/types";

const POLL_MS = 10_000;

/**
 * Polls the status command on an interval and refetches right after a
 * reconnect. Requests are skipped while the transport is down (they would
 * just pile up in the client's queue).
 */
export function useStatus(client: PilotClient): {
  status: StatusSnapshot | null;
  error: string | null;
  refresh: () => void;
} {
  const [status, setStatus] = useState<StatusSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (client.getConnectionState() !== "connected") return;
    client.request<StatusSnapshot>("status", {}).then(
      (snapshot) => {
        setStatus(snapshot);
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
    // Live refresh: every successful setter (persona/set, preset/apply,
    // budget/set, mode/set) makes the server broadcast a fresh snapshot.
    const offStatus = client.onStatus((snapshot) => {
      setStatus(snapshot);
      setError(null);
    });
    return () => {
      clearInterval(interval);
      offConn();
      offStatus();
    };
  }, [client, load]);

  return { status, error, refresh: load };
}
