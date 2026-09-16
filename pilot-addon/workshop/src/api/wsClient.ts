import type { ConnectionState, PilotClient } from "./client";
import { Emitter } from "./emitter";
import type {
  AssetAckPayload,
  LogEntry,
  PromptGetPayload,
  PromptListPayload,
  QueuePayload,
  SkillGetPayload,
  SkillListPayload,
  StatusSnapshot,
} from "./types";

/** Reconnect backoff: 1s -> 2s -> 5s -> 10s -> 30s, then keep 30s. */
const RECONNECT_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000];
/** A command that never gets an answer must not wedge the queue forever. */
const REQUEST_TIMEOUT_MS = 15_000;

interface PendingRequest {
  type: string;
  payload: Record<string, unknown>;
  timer: ReturnType<typeof setTimeout> | null;
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
}

/**
 * WebSocket client for the add-on /ws endpoint.
 *
 * Protocol (pilot_addon/ws_api.py): JSON text frames {type, payload}; the
 * response to a command reuses the command's type; failures arrive as
 * {type: "error", payload: {message}} and keep the connection open. There
 * are no request ids, so requests are strictly serialized: one outstanding
 * command, responses matched by type.
 */
export class WsPilotClient implements PilotClient {
  readonly mode = "ws" as const;

  private ws: WebSocket | null = null;
  private state: ConnectionState = "disconnected";
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private pending: PendingRequest | null = null;
  private readonly queue: PendingRequest[] = [];
  private readonly connectionEmitter = new Emitter<ConnectionState>();
  private readonly logEmitter = new Emitter<LogEntry>();
  private readonly queueEmitter = new Emitter<QueuePayload>();
  private readonly statusEmitter = new Emitter<StatusSnapshot>();

  constructor(private readonly url: string) {}

  getConnectionState(): ConnectionState {
    return this.state;
  }

  onConnectionChange(listener: (state: ConnectionState) => void): () => void {
    return this.connectionEmitter.subscribe(listener);
  }

  onLog(listener: (entry: LogEntry) => void): () => void {
    return this.logEmitter.subscribe(listener);
  }

  onQueue(listener: (payload: QueuePayload) => void): () => void {
    return this.queueEmitter.subscribe(listener);
  }

  onStatus(listener: (snapshot: StatusSnapshot) => void): () => void {
    return this.statusEmitter.subscribe(listener);
  }

  listPrompts(): Promise<PromptListPayload> {
    return this.request<PromptListPayload>("prompts/list", {});
  }

  getPrompt(name: string): Promise<PromptGetPayload> {
    return this.request<PromptGetPayload>("prompts/get", { name });
  }

  setPrompt(name: string, content: string): Promise<AssetAckPayload> {
    return this.request<AssetAckPayload>("prompts/set", { name, content });
  }

  resetPrompt(name: string): Promise<AssetAckPayload> {
    return this.request<AssetAckPayload>("prompts/reset", { name });
  }

  listSkills(): Promise<SkillListPayload> {
    return this.request<SkillListPayload>("skills/list", {});
  }

  getSkill(name: string): Promise<SkillGetPayload> {
    return this.request<SkillGetPayload>("skills/get", { name });
  }

  setSkill(name: string, content: string): Promise<AssetAckPayload> {
    return this.request<AssetAckPayload>("skills/set", { name, content });
  }

  resetSkill(name: string): Promise<AssetAckPayload> {
    return this.request<AssetAckPayload>("skills/reset", { name });
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.reconnectAttempt = 0;
    this.openSocket();
  }

  stop(): void {
    this.stopped = true;
    this.clearReconnectTimer();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.close();
    }
    this.rejectPending(new Error("client stopped"));
    this.queue.length = 0;
    this.setState("disconnected");
  }

  request<T>(type: string, payload: Record<string, unknown> = {}): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.queue.push({
        type,
        payload,
        timer: null,
        resolve: resolve as (value: unknown) => void,
        reject,
      });
      this.pump();
    });
  }

  private openSocket(): void {
    this.setState(this.reconnectAttempt === 0 ? "connecting" : "reconnecting");
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.reconnectAttempt = 0;
      this.setState("connected");
      // Flush whatever queued up while the socket was down.
      this.pump();
    };
    ws.onmessage = (event: MessageEvent<string>) => {
      this.handleMessage(event.data);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.rejectPending(new Error("connection closed"));
      if (this.stopped) {
        this.setState("disconnected");
      } else {
        this.scheduleReconnect();
      }
    };
  }

  private scheduleReconnect(): void {
    const delay =
      RECONNECT_DELAYS_MS[
        Math.min(this.reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)
      ];
    this.reconnectAttempt += 1;
    this.setState("reconnecting");
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
  }

  private handleMessage(raw: string): void {
    let frame: { type?: unknown; payload?: unknown };
    try {
      frame = JSON.parse(raw) as { type?: unknown; payload?: unknown };
    } catch {
      return;
    }
    if (typeof frame !== "object" || frame === null) return;

    if (frame.type === "error") {
      const message =
        (frame.payload as { message?: string } | undefined)?.message ??
        "unknown error";
      this.rejectPending(new Error(message));
      return;
    }

    if (this.pending !== null && frame.type === this.pending.type) {
      const req = this.pending;
      this.clearRequestTimer(req);
      this.pending = null;
      req.resolve(frame.payload);
      this.pump();
      return;
    }

    // Anything else is a server-push event. A "status" broadcast can also
    // arrive while a "status" command is pending — it is consumed as the
    // command response above (same payload shape), which is harmless: the
    // requester applies the snapshot either way.
    if (frame.type === "log") {
      this.logEmitter.emit(frame.payload as LogEntry);
    } else if (frame.type === "queue") {
      this.queueEmitter.emit(frame.payload as QueuePayload);
    } else if (frame.type === "status") {
      this.statusEmitter.emit(frame.payload as StatusSnapshot);
    }
  }

  /** Send the next queued command if the socket is free. */
  private pump(): void {
    if (this.pending !== null) return;
    const ws = this.ws;
    if (ws === null || ws.readyState !== WebSocket.OPEN) return;
    const next = this.queue.shift();
    if (next === undefined) return;
    this.pending = next;
    next.timer = setTimeout(() => {
      if (this.pending === next) {
        this.pending = null;
        next.reject(new Error(`request "${next.type}" timed out`));
        this.pump();
      }
    }, REQUEST_TIMEOUT_MS);
    ws.send(JSON.stringify({ type: next.type, payload: next.payload }));
  }

  private rejectPending(err: Error): void {
    const req = this.pending;
    if (req === null) return;
    this.clearRequestTimer(req);
    this.pending = null;
    req.reject(err);
  }

  private clearRequestTimer(req: PendingRequest): void {
    if (req.timer !== null) {
      clearTimeout(req.timer);
      req.timer = null;
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    this.connectionEmitter.emit(state);
  }
}
