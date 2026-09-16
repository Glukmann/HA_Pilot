import { useCallback, useEffect, useState } from "react";

import type { PilotClient } from "../api/client";
import type { ConfigPayload } from "../api/types";

/**
 * Fetches the masked runtime config (config/get) on mount and refetches
 * after a reconnect. Requests are skipped while the transport is down.
 */
export function useConfig(client: PilotClient): {
  config: ConfigPayload | null;
  loaded: boolean;
  error: string | null;
  refresh: () => void;
} {
  const [config, setConfig] = useState<ConfigPayload | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (client.getConnectionState() !== "connected") return;
    client.request<ConfigPayload>("config/get", {}).then(
      (payload) => {
        setConfig(payload);
        setLoaded(true);
        setError(null);
      },
      (err: unknown) => {
        setLoaded(true);
        setError(err instanceof Error ? err.message : String(err));
      },
    );
  }, [client]);

  useEffect(() => {
    load();
    const offConn = client.onConnectionChange((state) => {
      if (state === "connected") load();
    });
    return () => offConn();
  }, [client, load]);

  return { config, loaded, error, refresh: load };
}
