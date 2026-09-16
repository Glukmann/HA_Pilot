import type { LogEntry, QueuePayload, StatusSnapshot } from "./types";

export type ConnectionState =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

/**
 * The single channel the workshop UI talks to. One implementation speaks
 * the add-on WS protocol; the other replays local fixtures so the frontend
 * can be developed without the add-on running.
 */
export interface PilotClient {
  readonly mode: "ws" | "mock";

  getConnectionState(): ConnectionState;
  onConnectionChange(listener: (state: ConnectionState) => void): () => void;

  /** Server-push event: a new log record (only after logs/subscribe). */
  onLog(listener: (entry: LogEntry) => void): () => void;
  /** Server-push event: queue changed after a successful queue/confirm. */
  onQueue(listener: (payload: QueuePayload) => void): () => void;
  /**
   * Server-push event: fresh status snapshot, broadcast to every
   * connection after each successful setter (persona/set, preset/apply,
   * budget/set, mode/set).
   */
  onStatus(listener: (snapshot: StatusSnapshot) => void): () => void;

  /**
   * Send a command and resolve with its response payload. The underlying
   * transport serializes requests (the protocol has no request ids), so
   * callers get a stable Promise API anyway.
   */
  request<T>(type: string, payload?: Record<string, unknown>): Promise<T>;

  start(): void;
  stop(): void;
}
