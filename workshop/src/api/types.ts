// Wire shapes mirrored from the add-on (pilot_addon/ws_api.py, state.py,
// logbuffer.py). This file is the client-side contract: keep it in sync
// with the Python source of truth.

export interface LayerStatus {
  alive: boolean;
  /** Unix seconds of the last run, or null if the layer never ran. */
  last_run_ts: number | null;
}

export interface LayersStatus {
  vitrine: LayerStatus;
  checker: LayerStatus;
  trust: LayerStatus;
}

export interface StatusSnapshot {
  status: string;
  vitrine_age_s: number | null;
  cost_today: number;
  queue_size: number;
  awaiting_confirmation: boolean;
  runtime_version: string;
  persona: Record<string, number>;
  daily_budget: number;
  persona_preset: string;
  mode: string;
  current_focus: string;
  flags: string[];
  uptime_s: number;
  layers: LayersStatus;
}

export interface LogEntry {
  /** Unix seconds (logging record time). */
  ts: number;
  level: string;
  logger: string;
  message: string;
}

export interface QueueItem {
  id: string;
  title: string;
  summary: string;
  created_ts: number;
}

export interface QueuePayload {
  items: QueueItem[];
}

export interface VitrinePayload {
  fresh: boolean;
  lines: string[];
}
