import type { LogEntry } from "../types";

// A realistic tail of the add-on log ring buffer: startup, vitrine pushes,
// checker runs, trust-layer activity, plus the warnings/errors an owner
// actually sees in production.
const NOW_S = Date.now() / 1000;

function entry(offsetS: number, level: string, logger: string, message: string): LogEntry {
  return { ts: NOW_S - offsetS, level, logger, message };
}

export const MOCK_LOGS_RECENT: LogEntry[] = [
  entry(742, "INFO", "pilot_addon.main", "cost guard: 0.42 RUB of 10.00 RUB today"),
  entry(735, "INFO", "pilot_addon.vitrine", "HA websocket: connected ws://192.168.0.130/api/websocket"),
  entry(728, "INFO", "pilot_addon.discovery", "discovery broadcast sent: [pilot]"),
  entry(711, "INFO", "pilot_addon.vitrine", "pushed batch: 41 entities from 12 areas"),
  entry(694, "INFO", "pilot_addon.checker", "flags evaluated: 0"),
  entry(680, "INFO", "pilot_addon.trust", "queue: no pending confirmations"),
  entry(663, "INFO", "pilot_addon.checker", "heat pump guard: living room 22.1°C, within band"),
  entry(655, "INFO", "pilot_addon.http_api", "GET /api/status 200"),
  entry(648, "WARNING", "pilot_addon.vitrine", "vitrine snapshot stale: age 74s > 60s"),
  entry(641, "INFO", "pilot_addon.vitrine", "pushed batch: 41 entities from 12 areas"),
  entry(634, "INFO", "pilot_addon.checker", "gate relay watch: no stuck relays"),
  entry(626, "WARNING", "pilot_addon.vitrine", "HA websocket: reconnecting in 5s"),
  entry(619, "ERROR", "pilot_addon.vitrine", "HA websocket: connection refused by 192.168.0.130:80"),
  entry(611, "INFO", "pilot_addon.checker", "flags evaluated: 0"),
  entry(604, "INFO", "pilot_addon.trust", "audit: appended entry kind=queue_propose"),
  entry(597, "INFO", "pilot_addon.vitrine", "pushed batch: 40 entities from 12 areas"),
  entry(589, "INFO", "pilot_addon.http_api", "GET /api/status 200"),
  entry(582, "INFO", "pilot_addon.checker", "vit run: vitrine fresh (age 2.3s, 41 entities)"),
  entry(574, "INFO", "pilot_addon.main", "cost guard: 0.38 RUB of 10.00 RUB today"),
  entry(567, "INFO", "pilot_addon.checker", "flags evaluated: 0"),
  entry(559, "INFO", "pilot_addon.trust", "queue: no pending confirmations"),
  entry(552, "INFO", "pilot_addon.vitrine", "pushed batch: 41 entities from 12 areas"),
  entry(544, "INFO", "pilot_addon.checker", "bathroom scenario: shower timer finished, extractor off"),
  entry(537, "INFO", "pilot_addon.http_api", "GET /api/vitrine 200"),
  entry(529, "INFO", "pilot_addon.checker", "flags evaluated: 0"),
  entry(522, "INFO", "pilot_addon.vitrine", "pushed batch: 41 entities from 12 areas"),
  entry(514, "WARNING", "pilot_addon.checker", "budget guard: cost_today 8.10 RUB is 81% of daily budget"),
  entry(507, "INFO", "pilot_addon.trust", "audit: appended entry kind=confirm"),
  entry(499, "INFO", "pilot_addon.checker", "flags evaluated: 1"),
  entry(492, "INFO", "pilot_addon.checker", "flag raised: living_room_temperature_deviation (0.6°C)"),
  entry(484, "INFO", "pilot_addon.vitrine", "pushed batch: 41 entities from 12 areas"),
  entry(477, "INFO", "pilot_addon.checker", "heat pump guard: living room 22.4°C, within band"),
  entry(469, "INFO", "pilot_addon.main", "cost guard: 0.41 RUB of 10.00 RUB today"),
  entry(462, "INFO", "pilot_addon.checker", "flags evaluated: 0"),
  entry(454, "INFO", "pilot_addon.vitrine", "pushed batch: 41 entities from 12 areas"),
  entry(447, "INFO", "aiohttp.access", "\"GET /api/status HTTP/1.1\" 200 412"),
];

interface LiveTemplate {
  weight: number;
  make: () => Omit<LogEntry, "ts">;
}

function info(logger: string, message: string): Omit<LogEntry, "ts"> {
  return { level: "INFO", logger, message };
}

function warn(logger: string, message: string): Omit<LogEntry, "ts"> {
  return { level: "WARNING", logger, message };
}

function error(logger: string, message: string): Omit<LogEntry, "ts"> {
  return { level: "ERROR", logger, message };
}

const LIVE_POOL: LiveTemplate[] = [
  { weight: 10, make: () => info("pilot_addon.checker", `flags evaluated: ${Math.random() < 0.85 ? 0 : 1}`) },
  { weight: 6, make: () => info("pilot_addon.checker", `vit run: vitrine fresh (age ${(1 + Math.random() * 4).toFixed(1)}s, 41 entities)`) },
  { weight: 6, make: () => info("pilot_addon.vitrine", `pushed batch: ${38 + Math.floor(Math.random() * 6)} entities from 12 areas`) },
  { weight: 4, make: () => info("pilot_addon.trust", "queue: no pending confirmations") },
  { weight: 3, make: () => info("pilot_addon.trust", "audit: appended entry kind=heartbeat") },
  { weight: 4, make: () => info("pilot_addon.http_api", `GET /api/status 200 ${250 + Math.floor(Math.random() * 300)}`) },
  { weight: 2, make: () => info("aiohttp.access", "\"GET /api/status HTTP/1.1\" 200 412") },
  { weight: 3, make: () => info("pilot_addon.main", `cost guard: ${(Math.random() * 2).toFixed(2)} RUB of 10.00 RUB today`) },
  { weight: 2, make: () => info("pilot_addon.checker", "heat pump guard: living room 22.2°C, within band") },
  { weight: 2, make: () => info("pilot_addon.checker", "gate relay watch: no stuck relays") },
  { weight: 1, make: () => info("pilot_addon.ws_api", "ws client connected") },
  { weight: 4, make: () => warn("pilot_addon.vitrine", `vitrine snapshot stale: age ${61 + Math.floor(Math.random() * 40)}s > 60s`) },
  { weight: 2, make: () => warn("pilot_addon.main", `budget guard: cost_today ${(8 + Math.random()).toFixed(2)} RUB is ${80 + Math.floor(Math.random() * 15)}% of daily budget`) },
  { weight: 1, make: () => error("pilot_addon.vitrine", "HA websocket: connection refused, will retry") },
];

/** Weighted-random next live record for the mock log feed. */
export function nextLiveLog(): LogEntry {
  const total = LIVE_POOL.reduce((sum, t) => sum + t.weight, 0);
  let roll = Math.random() * total;
  let picked = LIVE_POOL[0];
  for (const template of LIVE_POOL) {
    roll -= template.weight;
    if (roll <= 0) {
      picked = template;
      break;
    }
  }
  return { ts: Date.now() / 1000, ...picked.make() };
}
