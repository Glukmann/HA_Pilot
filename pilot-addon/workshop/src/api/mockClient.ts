import type { ConnectionState, PilotClient } from "./client";
import { Emitter } from "./emitter";
import { MOCK_LOGS_RECENT, nextLiveLog } from "./fixtures/logs";
import { MOCK_PROPOSAL_POOL, MOCK_QUEUE } from "./fixtures/queue";
import { MOCK_STATUS_BASE } from "./fixtures/status";
import { MOCK_VITRINE } from "./fixtures/vitrine";
import type { LogEntry, QueueItem, QueuePayload, StatusSnapshot } from "./types";

const LATENCY_MS = 60;
/** Slow drip of new proposals so the Queue screen stays alive in mock. */
const PROPOSAL_INTERVAL_MS = 60_000;
const MAX_MOCK_QUEUE = 3;

/**
 * Fixture-backed client: same contract as WsPilotClient, but answers from
 * local data and emits synthetic live events (log stream, queue changes).
 * Selected with VITE_PILOT_API=mock so the frontend can be developed
 * without the add-on.
 */
export class MockPilotClient implements PilotClient {
  readonly mode = "mock" as const;

  private state: ConnectionState = "disconnected";
  private startedAtMs = 0;
  private logTimer: ReturnType<typeof setTimeout> | null = null;
  private logSubscribed = false;
  private queueItems: QueueItem[] = [];
  private proposalTimer: ReturnType<typeof setTimeout> | null = null;
  private proposalIndex = 0;
  private cabinetLightOn = true;
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
    this.queueItems = MOCK_QUEUE.map((item) => ({ ...item }));
    this.setState("connecting");
    setTimeout(() => {
      this.setState("connected");
      this.scheduleLog();
      this.scheduleProposal();
    }, 250);
  }

  stop(): void {
    this.clearLogTimer();
    this.clearProposalTimer();
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
        resolve({ items: this.queueItems.map((item) => ({ ...item })) } as T);
        return;
      case "queue/confirm": {
        const id = String(payload.id ?? "");
        const index = this.queueItems.findIndex((item) => item.id === id);
        if (index === -1) {
          reject(new Error("not found"));
          return;
        }
        this.queueItems.splice(index, 1);
        resolve({ ok: true } as T);
        // The real server broadcasts a fresh queue to every connection
        // after a successful confirm; mimic that (ourselves included).
        this.queueEmitter.emit({ items: this.queueItems.map((item) => ({ ...item })) });
        return;
      }
      case "vitrine/get":
        resolve(this.buildVitrine() as T);
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

  private buildVitrine() {
    // Fresh copy each poll, with one entity drifting so the 10s refresh
    // is visible in mock mode.
    if (Math.random() < 0.5) {
      this.cabinetLightOn = !this.cabinetLightOn;
    }
    return {
      fresh: MOCK_VITRINE.fresh,
      lines: MOCK_VITRINE.lines.map((line) =>
        line.startsWith("  Свет кабинета:")
          ? `  Свет кабинета: ${this.cabinetLightOn ? "on" : "off"}`
          : line,
      ),
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

  private scheduleProposal(): void {
    this.clearProposalTimer();
    this.proposalTimer = setTimeout(() => {
      if (this.state === "connected" && this.queueItems.length < MAX_MOCK_QUEUE) {
        const template = MOCK_PROPOSAL_POOL[this.proposalIndex % MOCK_PROPOSAL_POOL.length];
        this.proposalIndex += 1;
        if (!this.queueItems.some((item) => item.id === template.id)) {
          this.queueItems.push({ ...template, created_ts: Date.now() / 1000 });
          this.queueEmitter.emit({
            items: this.queueItems.map((item) => ({ ...item })),
          });
        }
      }
      this.scheduleProposal();
    }, PROPOSAL_INTERVAL_MS);
  }

  private clearLogTimer(): void {
    if (this.logTimer !== null) {
      clearTimeout(this.logTimer);
      this.logTimer = null;
    }
  }

  private clearProposalTimer(): void {
    if (this.proposalTimer !== null) {
      clearTimeout(this.proposalTimer);
      this.proposalTimer = null;
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    this.connectionEmitter.emit(state);
  }
}
