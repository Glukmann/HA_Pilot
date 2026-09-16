import type { VitrinePayload } from "../types";

// Rendered in exactly the format VitrineState.lines produces (state.py):
// room headers "<Комната>:" at column 0, entity rows indented with two
// spaces as "<имя>: <значение>".
export const MOCK_VITRINE: VitrinePayload = {
  fresh: true,
  lines: [
    "Гостиная:",
    "  Тепловой насос: heat",
    "  Свет гостиной: on",
    "  Apple TV: playing",
    "  Шторы гостиной: closed",
    "Спальня:",
    "  Tower Heater: off",
    "  Haier TV: off",
    "  Свет спальни: off",
    "Кабинет:",
    "  Samsung TV: off",
    "  Свет кабинета: on",
    "Ванная (N1):",
    "  Вытяжка ванной N1 Info: unknown",
    "Кухня:",
    "  Свет кухни: off",
    "  Чайник: off",
    "Улица:",
    "  Калитка: off",
    "  Ворота: off",
  ],
};
