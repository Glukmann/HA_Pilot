import type { IconName } from "../components/Icon";

export interface SectionMeta {
  path: string;
  title: string;
  icon: IconName;
}

export const SECTIONS: SectionMeta[] = [
  { path: "/channels", title: "Каналы", icon: "channels" },
  { path: "/models", title: "Модели", icon: "models" },
  { path: "/plugins", title: "Плагины", icon: "plugins" },
  { path: "/persona", title: "Персона", icon: "persona" },
  { path: "/vitrine", title: "Витрина", icon: "vitrine" },
  { path: "/admin", title: "Админ", icon: "admin" },
];

export interface StubCopy {
  headline: string;
  body: string;
}

export const STUB_COPY: Record<string, StubCopy> = {
  "/channels": {
    headline: "Каналы появятся в следующей версии мастерской.",
    body: "Здесь будут мессенджеры (MAX, Telegram), чат и голос Home Assistant и правила маршрутизации: что доставлять сразу, а что — дайджестом.",
  },
  "/models": {
    headline: "Модели появятся в следующей версии мастерской.",
    body: "Провайдеры, ключи API, выбор моделей по направлениям и дневной лимит расходов — всё в одном месте.",
  },
  "/plugins": {
    headline: "Плагины появятся в следующей версии мастерской.",
    body: "Каталог навыков с установкой и обновлениями, а также workspace агента: skills, память и журналы.",
  },
  "/persona": {
    headline: "Персона появится в следующей версии мастерской.",
    body: "Шкалы характера и пресеты («Дворецкий», «Тихий наблюдатель», «Эконом»), обучение и журнал адаптаций.",
  },
  "/vitrine": {
    headline: "Витрина появится в следующей версии мастерской.",
    body: "Состав и формат карты дома: сущности, атрибуты, пороги, правила свежести и визуальная карта связей.",
  },
};
