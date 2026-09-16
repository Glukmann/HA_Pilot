import { useCallback, useEffect, useState } from "react";

import type { PilotClient } from "../api/client";
import type { QueueItem, QueuePayload } from "../api/types";

/**
 * Tracks the confirmation queue: initial queue/get, live updates from the
 * server-push "queue" event (broadcast after every successful confirm),
 * plus a refetch right after a reconnect.
 */
export function useQueue(client: PilotClient): {
  items: QueueItem[];
  loaded: boolean;
  error: string | null;
  refresh: () => void;
} {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback((payload: QueuePayload) => {
    setItems(payload.items);
    setLoaded(true);
    setError(null);
  }, []);

  const load = useCallback(() => {
    if (client.getConnectionState() !== "connected") return;
    client.request<QueuePayload>("queue/get", {}).then(apply, (err: unknown) => {
      setLoaded(true);
      setError(err instanceof Error ? err.message : String(err));
    });
  }, [client, apply]);

  useEffect(() => {
    load();
    const offQueue = client.onQueue(apply);
    const offConn = client.onConnectionChange((state) => {
      if (state === "connected") load();
    });
    return () => {
      offQueue();
      offConn();
    };
  }, [client, load, apply]);

  return { items, loaded, error, refresh: load };
}
