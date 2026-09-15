import { createContext, useContext } from "react";

import type { PilotClient } from "./client";

export const PilotClientContext = createContext<PilotClient | null>(null);

export function usePilotClient(): PilotClient {
  const client = useContext(PilotClientContext);
  if (client === null) {
    throw new Error("usePilotClient must be used within PilotClientContext");
  }
  return client;
}
