import { useEffect, useState } from "react";

import type { ConnectionState } from "../api/client";
import type { PilotClient } from "../api/client";

/** Live mirror of the client's connection state. */
export function useConnectionState(client: PilotClient): ConnectionState {
  const [state, setState] = useState<ConnectionState>(client.getConnectionState());
  useEffect(() => client.onConnectionChange(setState), [client]);
  return state;
}
