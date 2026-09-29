import type { ProviderCapability } from '../api/rag'

/** 사용자가 고른 모델이 쓸 수 있으면 그것을, 아니면 서버 기본값이나 첫 사용 가능 모델을 고른다. */
export function selectProviderModel(
  capability: ProviderCapability | undefined,
  preference: string | null,
) {
  const models = capability?.models ?? []
  const preferred = preference
    ? models.find((model) => model.id === preference && model.available)
    : undefined
  if (preferred) {
    return preferred.id
  }

  const configured = capability?.defaultModel ?? capability?.model
  const configuredModel = configured
    ? models.find((model) => model.id === configured)
    : undefined
  if (configured && (!configuredModel || configuredModel.available)) {
    return configured
  }
  return models.find((model) => model.available)?.id ?? ''
}
