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
  "/vitrine": {
    headline: "Витрина появится в следующей версии мастерской.",
    body: "Состав и формат карты дома: сущности, атрибуты, пороги, правила свежести и визуальная карта связей.",
  },
};
