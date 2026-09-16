import type { ConfigPayload } from "../types";

// Mirrors a post-onboarding pilot.json. Secrets come pre-masked: the real
// server masks values under key markers (token/secret/password/api_key/...);
// the mock ships the same "***" look so the UI demo needs no add-on.
export const MOCK_CONFIG: ConfigPayload = {
  configured: true,
  sections: {
    channels: {
      messengers: [
        {
          name: "MAX",
          enabled: true,
          bot_token: "***",
          routing: { immediate: ["urgent_flags", "queue"], digest: "daily" },
        },
        {
          name: "Telegram",
          enabled: false,
          bot_token: "***",
          chat_id: "12345678",
        },
      ],
      homeassistant: { chat: true, voice: true },
    },
    models: {
      providers: [
        { name: "openrouter", api_key: "***", priority: 1 },
        {
          name: "local_llama",
          endpoint: "http://192.168.0.113:8080",
          priority: 2,
        },
      ],
      directions: {
        daily_supervisor: "qwen3-coder",
        chat: "gpt-4.1-mini",
        embeddings: "local",
      },
      daily_limit_rub: 10,
    },
    plugins: {
      skills: [
        { name: "infrastructure-control", version: "1.4.0", enabled: true },
        { name: "home-cinema", version: "0.9.2", enabled: true },
        { name: "budget-guard", version: "0.3.1", enabled: false },
      ],
      workspace: { skills_dir: "skills", memory_dir: "memory" },
    },
  },
};
