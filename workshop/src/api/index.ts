import type { PilotClient } from "./client";
import { MockPilotClient } from "./mockClient";
import { WsPilotClient } from "./wsClient";

/**
 * Client factory driven by VITE_PILOT_API:
 *   "mock"        -> fixture-backed client (frontend dev without the add-on);
 *   ws(s)://...   -> that URL (e.g. VITE_PILOT_API=ws://localhost:8899/ws);
 *   unset         -> /ws relative to the current path prefix: same-origin in
 *                    dev (the Vite proxy forwards to the add-on), and under
 *                    the Supervisor ingress the prefix
 *                    /api/hassio_ingress/<token> is kept.
 */
export function createPilotClient(): PilotClient {
  const api = (import.meta.env.VITE_PILOT_API as string | undefined) ?? "";
  if (api === "mock") {
    return new MockPilotClient();
  }
  const url =
    api !== ""
      ? api
      : `${location.protocol === "https:" ? "wss" : "ws"}://${
          location.host
        }${location.pathname.replace(/\/?$/, "/")}ws`;
  return new WsPilotClient(url);
}
