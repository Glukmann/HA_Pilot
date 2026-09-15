import type { ConnectionState, PilotClient } from "./client";
import { Emitter } from "./emitter";
import { MOCK_LOGS_RECENT, nextLiveLog } from "./fixtures/logs";
import { MOCK_QUEUE } from "./fixtures/queue";
import { MOCK_STATUS_BASE } from "./fixtures/status";
import { MOCK_VITRINE } from "./fixtures/vitrine";
import type { LogEntry, QueuePayload, StatusSnapshot } from "./types";

const LATENCY_MS = 60;

/**
 * Fixture-backed client: same contract as WsPilotClient, but answers from
 * local data and emits synthetic live log events. Selected with
 * VITE_PILOT_API=mock so the frontend can be developed without the add-on.
 */
export class MockPilotClient implements PilotClient {
  readonly mode = "mock" as const;

  private state: ConnectionState = "disconnected";
  private startedAtMs = 0;
  private logTimer: ReturnType<typeof setTimeout> | null = null;
  private logSubscribed = false;
  private readonly connectionEmitter = new Emitter<ConnectionState>();
  private readonly logEmitter = new Emitter<LogEntry>();
  private readonly queueEmitter = new Emitter<QueuePayload>();

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

  start(): void {
    if (this.state !== "disconnected") return;
    this.startedAtMs = Date.now();
    this.setState("connecting");
    setTimeout(() => {
      this.setState("connected");
      this.scheduleLog();
    }, 250);
  }

  stop(): void {
    this.clearLogTimer();
    this.logSubscribed = false;
    this.setState("disconnected");
  }

  request<T>(type: string, payload: Record<string, unknown> = {}): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      setTimeout(() => {
        this.dispatch<T>(type, payload, resolve, reject);
      }, LATENCY_MS + Math.random() * 120);
    });
  }

  private dispatch<T>(
    type: string,
    payload: Record<string, unknown>,
    resolve: (value: T) => void,
    reject: (err: Error) => void,
  ): void {
    switch (type) {
      case "status":
        resolve(this.buildStatus() as T);
        return;
      case "logs/recent": {
        const limit =
          typeof payload.limit === "number"
            ? Math.max(1, Math.floor(payload.limit))
            : MOCK_LOGS_RECENT.length;
        resolve(MOCK_LOGS_RECENT.slice(-limit) as T);
        return;
      }
      case "logs/subscribe":
        this.logSubscribed = true;
        resolve({ ok: true } as T);
        return;
      case "logs/unsubscribe":
        this.logSubscribed = false;
        resolve({ ok: true } as T);
        return;
      case "queue/get":
        resolve({ items: MOCK_QUEUE } as T);
        return;
      case "queue/confirm": {
        const id = String(payload.id ?? "");
        if (MOCK_QUEUE.some((item) => item.id === id)) {
          resolve({ ok: true } as T);
        } else {
          reject(new Error("not found"));
        }
        return;
      }
      case "vitrine/get":
        resolve(MOCK_VITRINE as T);
        return;
      default:
        reject(new Error(`unknown command: ${type}`));
    }
  }

  private buildStatus(): StatusSnapshot {
    const nowS = Date.now() / 1000;
    return {
      ...MOCK_STATUS_BASE,
      uptime_s:
        MOCK_STATUS_BASE.uptime_s +
        Math.floor((Date.now() - this.startedAtMs) / 1000),
      layers: {
        vitrine: { alive: true, last_run_ts: nowS - 2 },
        checker: { alive: true, last_run_ts: nowS - 27 },
        trust: { alive: true, last_run_ts: nowS - 640 },
      },
    };
  }

  private scheduleLog(): void {
    this.clearLogTimer();
    this.logTimer = setTimeout(() => {
      if (this.logSubscribed) {
        this.logEmitter.emit(nextLiveLog());
      }
      this.scheduleLog();
    }, 900 + Math.random() * 2200);
  }

  private clearLogTimer(): void {
    if (this.logTimer !== null) {
      clearTimeout(this.logTimer);
      this.logTimer = null;
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    this.connectionEmitter.emit(state);
  }
}
