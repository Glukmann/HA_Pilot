import type { StatusSnapshot } from "../types";

import { MOCK_QUEUE } from "./queue";

// Base values for the mock status command. The mock client recomputes the
// volatile fields (uptime, layer last_run_ts) on every request.
export const MOCK_STATUS_BASE: Omit<StatusSnapshot, "uptime_s" | "layers"> & {
  uptime_s: number;
} = {
  status: "ok",
  vitrine_age_s: 3,
  cost_today: 1.23,
  queue_size: MOCK_QUEUE.length,
  awaiting_confirmation: true,
  runtime_version: "0.4.5",
  persona: { butler_observer: 80, politeness: 30, verbosity: 70, conservative: 40 },
  daily_budget: 10.0,
  persona_preset: "butler",
  mode: "normal",
  current_focus: "",
  flags: [],
  uptime_s: 3730,
  onboarded: false,
};
