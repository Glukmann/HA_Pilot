/** OpenAI-compatible provider presets for the supervisor step.

 * Choosing a preset fills base_url and model; the API key is always the
 * owner's own. Endpoints follow each platform's OpenAI-compatible API.
 */
export interface ProviderPreset {
  id: string;
  label: string;
  baseUrl: string;
  model: string;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    baseUrl: "https://api.deepseek.com/v1",
    model: "deepseek-pro",
  },
  {
    id: "openai",
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    baseUrl: "https://openrouter.ai/api/v1",
    model: "deepseek/deepseek-chat",
  },
  {
    id: "gigachat",
    label: "GigaChat (Сбер)",
    baseUrl: "https://gigachat.devices.sberbank.ru/api/v1",
    model: "GigaChat",
  },
  {
    id: "yandexgpt",
    label: "YandexGPT",
    baseUrl: "https://llm.api.cloud.yandex.net/foundationModels/v1",
    model: "yandexgpt-lite",
  },
  {
    id: "mistral",
    label: "Mistral AI",
    baseUrl: "https://api.mistral.ai/v1",
    model: "mistral-small-latest",
  },
  {
    id: "ollama",
    label: "Ollama (локально)",
    baseUrl: "http://localhost:11434/v1",
    model: "qwen3:8b",
  },
];
