import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { Type } from "typebox";

// The add-on runtime is the only door into Home Assistant: vitrine for
// reading, /api/action for acting (its safety layer decides direct/queue).
const ADDON = "http://127.0.0.1:8899";

async function getText(path: string): Promise<string> {
  const res = await fetch(`${ADDON}${path}`, {
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) return `vitrine http ${res.status}`;
  return res.text();
}

export default defineToolPlugin({
  id: "pilot-home",
  name: "Pilot Home",
  description:
    "Eyes and hands of the Pilot agent in the Home Assistant home: " +
    "the vitrine snapshot, deep read-only queries (state/history/statistics) " +
    "and trust-guarded actions.",
  activation: { onStartup: true },
  tools: (tool) => [
    tool({
      name: "vitrine_get",
      description:
        "Снимок состояния дома: комнаты, устройства, температуры, флаги. " +
        "Читай ПЕРЕД любым вопросом о доме и перед предложением действий.",
      parameters: Type.Object({}),
      async execute() {
        try {
          return await getText("/api/vitrine");
        } catch (err) {
          return `vitrine unavailable: ${String(err).slice(0, 200)}`;
        }
      },
    }),
    tool({
      name: "home_find",
      description:
        "Найти entity_id по названию или части имени (поиск по снимку дома). " +
        "Используй ПЕРЕД home_state/home_history, чтобы не угадывать id.",
      parameters: Type.Object({
        q: Type.String({ description: "подстрока: 'гостиная темп', 'кондиционер'…" }),
      }),
      async execute(params) {
        try {
          return await getText(
            `/api/entity-find?q=${encodeURIComponent(params.q)}`
          );
        } catch (err) {
          return `home_find unavailable: ${String(err).slice(0, 200)}`;
        }
      },
    }),
    tool({
      name: "home_state",
      description:
        "Свежее значение ОДНОЙ сущности напрямую из HA (state, атрибуты, " +
        "last_changed). Используй, когда витрины не хватает или нужна точность.",
      parameters: Type.Object({
        entity_id: Type.String({ description: "например sensor.gostinaia_temperatura" }),
      }),
      async execute(params) {
        try {
          return await getText(
            `/api/entity-state?entity_id=${encodeURIComponent(params.entity_id)}`
          );
        } catch (err) {
          return `home_state unavailable: ${String(err).slice(0, 200)}`;
        }
      },
    }),
    tool({
      name: "home_history",
      description:
        "История изменений сущности за часы: моменты включения/выключения, " +
        "тренды. Для числовых — min/mean/max. Поймать падение температуры, " +
        "время старта кондиционера, длительность работы.",
      parameters: Type.Object({
        entity_id: Type.String(),
        hours: Type.Optional(
          Type.Number({ description: "окно в часах, 1..168, по умолчанию 24" })
        ),
      }),
      async execute(params) {
        try {
          const hours = params.hours ?? 24;
          return await getText(
            `/api/entity-history?entity_id=${encodeURIComponent(
              params.entity_id
            )}&hours=${encodeURIComponent(String(hours))}`
          );
        } catch (err) {
          return `home_history unavailable: ${String(err).slice(0, 200)}`;
        }
      },
    }),
    tool({
      name: "home_statistics",
      description:
        "Статистика recorder'а HA за период: суммы (кВт·ч, перерасход ЭЭ), " +
        "средние/min/max по часам или дням. Только для сущностей со statistics.",
      parameters: Type.Object({
        entity_id: Type.String(),
        hours: Type.Optional(Type.Number({ description: "1..168, по умолчанию 24" })),
        period: Type.Optional(
          Type.Union([Type.Literal("hour"), Type.Literal("day")], {
            description: "по умолчанию hour",
          })
        ),
      }),
      async execute(params) {
        try {
          const hours = params.hours ?? 24;
          const period = params.period ?? "hour";
          return await getText(
            `/api/entity-statistics?entity_id=${encodeURIComponent(
              params.entity_id
            )}&hours=${encodeURIComponent(String(hours))}&period=${encodeURIComponent(
              period
            )}`
          );
        } catch (err) {
          return `home_statistics unavailable: ${String(err).slice(0, 200)}`;
        }
      },
    }),
    tool({
      name: "home_action",
      description:
        "Действие в доме через контур доверия Пилота: applied — исполнено; " +
        "queued — ждёт подтверждения хозяина; refused — запрещено политикой.",
      parameters: Type.Object({
        domain: Type.String({ description: "например light, switch, climate" }),
        service: Type.String({
          description: "например turn_on, turn_off, set_temperature",
        }),
        entity_id: Type.Optional(Type.String()),
        value: Type.Optional(Type.Union([Type.Number(), Type.String()])),
      }),
      outputSchema: Type.Object(
        { status: Type.String(), detail: Type.String() },
        { additionalProperties: false }
      ),
      async execute(params) {
        try {
          const res = await fetch(`${ADDON}/api/action`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(params),
            signal: AbortSignal.timeout(15000),
          });
          const body = (await res.json().catch(() => ({}))) as {
            status?: string;
            detail?: string;
          };
          if (!res.ok) {
            return { status: "refused", detail: `addon http ${res.status}` };
          }
          return {
            status: String(body.status ?? "refused"),
            detail: String(body.detail ?? "").slice(0, 500),
          };
        } catch (err) {
          return {
            status: "refused",
            detail: `addon unreachable: ${String(err).slice(0, 200)}`,
          };
        }
      },
    }),
  ],
});
