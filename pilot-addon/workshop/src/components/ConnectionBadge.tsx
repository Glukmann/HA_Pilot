import type { ConnectionState } from "../api/client";

const LABELS: Record<ConnectionState, string> = {
  connected: "подключено",
  connecting: "подключение…",
  reconnecting: "переподключение…",
  disconnected: "отключено",
};

export function ConnectionBadge({ state }: { state: ConnectionState }) {
  return (
    <span className={`conn conn-${state}`}>
      <span className="conn-dot" />
      {LABELS[state]}
    </span>
  );
}
