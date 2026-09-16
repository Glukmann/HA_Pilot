import type { ConnectionState, PilotClient } from "./client";
import { Emitter } from "./emitter";
import { MOCK_CONFIG } from "./fixtures/config";
import { MOCK_LOGS_RECENT, nextLiveLog } from "./fixtures/logs";
import { DEFAULT_PROMPTS, DEFAULT_SKILLS } from "./fixtures/prompts-skills";
import { MOCK_PROPOSAL_POOL, MOCK_QUEUE } from "./fixtures/queue";
import { MOCK_STATUS_BASE } from "./fixtures/status";
import { MOCK_VITRINE } from "./fixtures/vitrine";
import type {
  AssetAckPayload,
  LogEntry,
  PromptGetPayload,
  PromptListPayload,
  QueueItem,
  QueuePayload,
  SkillGetPayload,
  SkillListPayload,
  StatusSnapshot,
} from "./types";

const LATENCY_MS = 60;
/** Slow drip of new proposals so the Queue screen stays alive in mock. */
const PROPOSAL_INTERVAL_MS = 60_000;
/** Cost drift cadence, so the budget block looks alive in mock. */
const COST_DRIFT_MS = 45_000;
const MAX_MOCK_QUEUE = 3;

const SLIDERS = ["butler_observer", "politeness", "verbosity", "conservative"] as const;
const PRESETS: Record<string, Record<string, number>> = {
  // Mirrors PERSONA_PRESETS in the add-on (state.py).
  butler: { butler_observer: 80, politeness: 30, verbosity: 70, conservative: 40 },
  observer: { butler_observer: 10, politeness: 20, verbosity: 20, conservative: 70 },
  economy: { butler_observer: 40, politeness: 60, verbosity: 30, conservative: 80 },
};
const MODES = ["normal", "vacation", "guests", "sick"] as const;

/** Mirrors promptstore.py: slug names, 64 KiB content cap. */
const ASSET_NAME_RE = /^[a-z0-9_-]{1,64}$/;
const MAX_ASSET_BYTES = 64 * 1024;
const encoder = new TextEncoder();

function validateAssetName(name: unknown): string {
  if (typeof name !== "string" || !ASSET_NAME_RE.test(name)) {
    throw new Error("name must match [a-z0-9_-]{1,64}");
  }
  return name;
}

function validateAssetContent(content: unknown): string {
  if (typeof content !== "string") {
    throw new Error("content must be a string");
  }
  if (encoder.encode(content).length > MAX_ASSET_BYTES) {
    throw new Error("content exceeds 64 KiB");
  }
  return content;
}

/** Minimal `---` frontmatter parser (name/description), as promptstore.py. */
function parseFrontmatter(text: string): Record<string, string> {
  if (!text.startsWith("---")) return {};
  const end = text.indexOf("\n---", 3);
  if (end === -1) return {};
  const meta: Record<string, string> = {};
  for (const line of text.slice(3, end).trim().split("\n")) {
    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    if (key !== "") meta[key] = line.slice(idx + 1).trim();
  }
  return meta;
}

interface MockAsset {
  content: string;
  modifiedTs: number | null;
}

function seedAssets(defaults: Record<string, string>): Map<string, MockAsset> {
  return new Map(
    Object.entries(defaults).map(([name, content]) => [
      name,
      { content, modifiedTs: null },
    ]),
  );
}

const SECRET_KEY_MARKERS = ["token", "secret", "password", "apikey", "api_key", "key"];
const SECRET_MASK = "***";

function isSecretKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return SECRET_KEY_MARKERS.some((marker) => lowered.includes(marker));
}

/** Masks values under secret-looking keys, like the server (ws_api.py). */
function maskSecrets(node: unknown): unknown {
  if (Array.isArray(node)) {
    return node.map(maskSecrets);
  }
  if (node !== null && typeof node === "object") {
    return Object.fromEntries(
      Object.entries(node).map(([key, value]) => [
        key,
        isSecretKey(key) ? SECRET_MASK : maskSecrets(value),
      ]),
    );
  }
  return node;
}

/** Deep-merge of config values into one section (mirrors the server). */
function deepMerge(
  base: Record<string, unknown>,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const out = { ...base };
  for (const [key, value] of Object.entries(values)) {
    const existing = out[key];
    const bothObjects =
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      existing !== null &&
      typeof existing === "object" &&
      !Array.isArray(existing);
    out[key] = bothObjects
      ? deepMerge(existing as Record<string, unknown>, value as Record<string, unknown>)
      : value;
  }
  return out;
}

/**
 * Fixture-backed client: same contract as WsPilotClient, but answers from
 * local data and emits synthetic live events (log stream, queue changes,
 * status broadcasts after setters). Selected with VITE_PILOT_API=mock so
 * the frontend can be developed without the add-on.
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
  private costTimer: ReturnType<typeof setTimeout> | null = null;
  private cabinetLightOn = true;
  private persona: Record<string, number>;
  private personaPreset: string;
  private pilotMode: string;
  private dailyBudget: number;
  private costToday: number;
  private configSections: Record<string, unknown>;
  private readonly promptStore: Map<string, MockAsset>;
  private readonly skillStore: Map<string, MockAsset>;
  private readonly connectionEmitter = new Emitter<ConnectionState>();
  private readonly logEmitter = new Emitter<LogEntry>();
  private readonly queueEmitter = new Emitter<QueuePayload>();
  private readonly statusEmitter = new Emitter<StatusSnapshot>();

  constructor() {
    this.persona = { ...MOCK_STATUS_BASE.persona };
    this.personaPreset = MOCK_STATUS_BASE.persona_preset;
    this.pilotMode = MOCK_STATUS_BASE.mode;
    this.dailyBudget = MOCK_STATUS_BASE.daily_budget;
    this.costToday = MOCK_STATUS_BASE.cost_today;
    this.configSections = JSON.parse(
      JSON.stringify(MOCK_CONFIG.sections),
    ) as Record<string, unknown>;
    this.promptStore = seedAssets(DEFAULT_PROMPTS);
    this.skillStore = seedAssets(DEFAULT_SKILLS);
  }

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

  // Editable library: system prompts and runtime skills.

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
    if (this.state !== "disconnected") return;
    this.startedAtMs = Date.now();
    this.queueItems = MOCK_QUEUE.map((item) => ({ ...item }));
    this.setState("connecting");
    setTimeout(() => {
      this.setState("connected");
      this.scheduleLog();
      this.scheduleProposal();
      this.scheduleCostDrift();
    }, 250);
  }

  stop(): void {
    this.clearLogTimer();
    this.clearProposalTimer();
    this.clearCostTimer();
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
      case "persona/set": {
        const slider = String(payload.slider ?? "");
        if (!SLIDERS.includes(slider as (typeof SLIDERS)[number])) {
          reject(new Error(`unknown slider: ${slider}`));
          return;
        }
        const value = Number(payload.value);
        if (!Number.isInteger(value)) {
          reject(new Error("value must be an integer"));
          return;
        }
        // set_persona in the add-on clamps to 0..100.
        this.persona[slider] = Math.max(0, Math.min(100, value));
        this.ackSetter(resolve);
        return;
      }
      case "preset/apply": {
        const preset = String(payload.preset ?? "");
        const values = PRESETS[preset];
        if (values === undefined) {
          reject(new Error(`unknown preset: ${preset}`));
          return;
        }
        this.persona = { ...values };
        this.personaPreset = preset;
        this.ackSetter(resolve);
        return;
      }
      case "budget/set": {
        const value = Number(payload.value);
        if (!Number.isFinite(value)) {
          reject(new Error("value must be a number"));
          return;
        }
        this.dailyBudget = value;
        this.ackSetter(resolve);
        return;
      }
      case "mode/set": {
        const mode = String(payload.mode ?? "");
        if (!MODES.includes(mode as (typeof MODES)[number])) {
          reject(new Error(`unknown mode: ${mode}`));
          return;
        }
        this.pilotMode = mode;
        this.ackSetter(resolve);
        return;
      }
      case "config/get":
        resolve({
          configured: true,
          sections: maskSecrets(
            JSON.parse(JSON.stringify(this.configSections)),
          ) as Record<string, unknown>,
        } as T);
        return;
      case "config/set": {
        const section = payload.section;
        if (typeof section !== "string" || section === "") {
          reject(new Error("section must be a non-empty string"));
          return;
        }
        const values = payload.values;
        if (values === null || typeof values !== "object" || Array.isArray(values)) {
          reject(new Error("values must be an object"));
          return;
        }
        const existing = this.configSections[section];
        const base =
          existing !== null && typeof existing === "object" && !Array.isArray(existing)
            ? (existing as Record<string, unknown>)
            : {};
        this.configSections[section] = deepMerge(base, values as Record<string, unknown>);
        resolve({ ok: true, section } as T);
        // A successful set broadcasts a fresh status (with the new
        // onboarded flag), like the real server.
        this.statusEmitter.emit(this.buildStatus());
        return;
      }
      case "prompts/list":
        resolve({ prompts: this.listAssets("prompts") } as T);
        return;
      case "prompts/get":
        try {
          resolve(this.getAsset("prompts", payload.name) as T);
        } catch (err) {
          reject(err as Error);
        }
        return;
      case "prompts/set":
        try {
          const name = validateAssetName(payload.name);
          const content = validateAssetContent(payload.content);
          this.promptStore.set(name, {
            content,
            modifiedTs: Date.now() / 1000,
          });
          resolve({ ok: true, name } as T);
          this.statusEmitter.emit(this.buildStatus());
        } catch (err) {
          reject(err as Error);
        }
        return;
      case "prompts/reset":
        try {
          resolve({ ok: true, name: this.resetAsset("prompts", payload.name) } as T);
        } catch (err) {
          reject(err as Error);
        }
        return;
      case "skills/list":
        resolve({ skills: this.listAssets("skills") } as T);
        return;
      case "skills/get":
        try {
          resolve(this.getAsset("skills", payload.name) as T);
        } catch (err) {
          reject(err as Error);
        }
        return;
      case "skills/set":
        try {
          const name = validateAssetName(payload.name);
          const content = validateAssetContent(payload.content);
          this.skillStore.set(name, {
            content,
            modifiedTs: Date.now() / 1000,
          });
          resolve({ ok: true, name } as T);
          this.statusEmitter.emit(this.buildStatus());
        } catch (err) {
          reject(err as Error);
        }
        return;
      case "skills/reset":
        try {
          resolve({ ok: true, name: this.resetAsset("skills", payload.name) } as T);
        } catch (err) {
          reject(err as Error);
        }
        return;
      default:
        reject(new Error(`unknown command: ${type}`));
    }
  }

  /** Setter ack followed by the status broadcast, like the real server. */
  private ackSetter<T>(resolve: (value: T) => void): void {
    resolve({ ok: true } as T);
    this.statusEmitter.emit(this.buildStatus());
  }

  private listAssets(kind: "prompts" | "skills"): Array<Record<string, unknown>> {
    const store = kind === "prompts" ? this.promptStore : this.skillStore;
    const defaults = kind === "prompts" ? DEFAULT_PROMPTS : DEFAULT_SKILLS;
    return [...store.keys()].sort().map((name) => {
      const asset = store.get(name);
      if (asset === undefined) return {};
      const entry: Record<string, unknown> = {
        name,
        size: encoder.encode(asset.content).length,
        modified_ts: asset.modifiedTs,
        is_default: asset.content === defaults[name],
      };
      if (kind === "skills") {
        entry.description = parseFrontmatter(asset.content).description ?? "";
      }
      return entry;
    });
  }

  private getAsset(kind: "prompts" | "skills", name: unknown): Record<string, unknown> {
    const clean = validateAssetName(name);
    const store = kind === "prompts" ? this.promptStore : this.skillStore;
    const asset = store.get(clean);
    if (asset === undefined) {
      throw new Error(`unknown ${kind === "prompts" ? "prompt" : "skill"}: ${clean}`);
    }
    const entry: Record<string, unknown> = { name: clean, content: asset.content };
    if (kind === "skills") {
      entry.description = parseFrontmatter(asset.content).description ?? "";
    }
    return entry;
  }

  private resetAsset(kind: "prompts" | "skills", name: unknown): string {
    const clean = validateAssetName(name);
    const defaults = kind === "prompts" ? DEFAULT_PROMPTS : DEFAULT_SKILLS;
    const defaultContent = defaults[clean];
    if (defaultContent === undefined) {
      throw new Error(`no bundled default for '${clean}'`);
    }
    const store = kind === "prompts" ? this.promptStore : this.skillStore;
    store.set(clean, { content: defaultContent, modifiedTs: Date.now() / 1000 });
    return clean;
  }

  private isOnboarded(): boolean {
    const supervisor = this.configSections.supervisor;
    if (
      supervisor === null ||
      typeof supervisor !== "object" ||
      Array.isArray(supervisor)
    ) {
      return false;
    }
    const section = supervisor as Record<string, unknown>;
    return Boolean(section.base_url) && Boolean(section.api_key) && Boolean(section.model);
  }

  private buildStatus(): StatusSnapshot {
    const nowS = Date.now() / 1000;
    return {
      ...MOCK_STATUS_BASE,
      persona: { ...this.persona },
      persona_preset: this.personaPreset,
      mode: this.pilotMode,
      daily_budget: this.dailyBudget,
      cost_today: Math.round(this.costToday * 10_000) / 10_000,
      queue_size: this.queueItems.length,
      awaiting_confirmation: this.queueItems.length > 0,
      onboarded: this.isOnboarded(),
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

  private scheduleCostDrift(): void {
    this.clearCostTimer();
    this.costTimer = setTimeout(() => {
      if (this.state === "connected") {
        this.costToday += 0.05 + Math.random() * 0.35;
        this.statusEmitter.emit(this.buildStatus());
      }
      this.scheduleCostDrift();
    }, COST_DRIFT_MS * (0.8 + Math.random() * 0.4));
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

  private clearCostTimer(): void {
    if (this.costTimer !== null) {
      clearTimeout(this.costTimer);
      this.costTimer = null;
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) return;
    this.state = state;
    this.connectionEmitter.emit(state);
  }
}
