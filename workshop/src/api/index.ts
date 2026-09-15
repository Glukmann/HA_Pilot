import type { PilotClient } from "./client";
import { MockPilotClient } from "./mockClient";
import { WsPilotClient } from "./wsClient";

/**
 * Client factory driven by VITE_PILOT_API:
 *   "mock"        -> fixture-backed client (frontend dev without the add-on);
 *   ws(s)://...   -> that URL (e.g. VITE_PILOT_API=ws://localhost:8899/ws);
 *   unset         -> same-origin /ws (in dev the Vite proxy forwards it to
 *                    the add-on on localhost:8899, WS included).
 */
export function createPilotClient(): PilotClient {
  const api = (import.meta.env.VITE_PILOT_API as string | undefined) ?? "";
  if (api === "mock") {
    return new MockPilotClient();
  }
  const url =
    api !== ""
      ? api
      : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  return new WsPilotClient(url);
}
