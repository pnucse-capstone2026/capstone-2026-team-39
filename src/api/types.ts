export const GENERATION_PROVIDERS = ['auto', 'local', 'frontier'] as const
export const PARSER_PROFILES = ['baseline', 'challenger', 'cascade'] as const
export const RETRIEVAL_MODES = [
  'bm25',
  'kure_dense',
  'kure_hybrid',
  'snowflake_dense',
  'snowflake_hybrid',
] as const

export type GenerationProvider = (typeof GENERATION_PROVIDERS)[number]
export type ConcreteProvider = Exclude<GenerationProvider, 'auto'>
export type ParserProfile = (typeof PARSER_PROFILES)[number]
export type RetrievalMode = (typeof RETRIEVAL_MODES)[number]

export type CitationLocation = {
  block_id?: string | null
  page?: number | null
  page_end?: number | null
  section_path?: string[] | null
  table_id?: string | null
  row?: number | null
  column?: number | null
}

export type ClaimCitation = CitationLocation & {
  citation_id?: string
  source_number?: number
  chunk_id?: string
  document_id?: string
  excerpt?: string
  excerpt_start?: number
  excerpt_end?: number
  excerpt_sha256?: string
  claim_sha256?: string | null
  source_title?: string | null
  source_url?: string | null
}

export type AnswerCitation = ClaimCitation & {
  claim_index?: number
  claim_text?: string
}

export type Claim = {
  text: string
  supported: boolean
  confidence?: number
  source_ids?: string[]
  source_numbers?: number[]
  citations?: ClaimCitation[]
  validation_reason?:
    | 'supported'
    | 'model_abstention'
    | 'critical_value_mismatch'
    | 'low_lexical_overlap'
    | string
  missing_critical_values?: string[]
  best_score?: number
}

export type SearchResult = {
  source_number?: number
  chunk_id: string
  doc_id?: string
  document_id?: string
  chunk_index?: number | null
  institution?: string | null
  file_name?: string | null
  source_path?: string | null
  relative_path?: string | null
  source_title?: string | null
  source_url?: string | null
  download_url?: string | null
  source_host?: string | null
  fetched_at?: string | null
  published_at?: string | null
  category?: string | null
  char_count?: number
  score?: number | null
  preview?: string
  location?: CitationLocation | null
  locations?: CitationLocation[]
  location_count?: number
  locations_truncated?: boolean
  page?: number | null
  page_start?: number | null
  page_end?: number | null
  section_path?: string[] | string | null
  table_id?: string | null
  table_ids?: string[]
  row?: number | null
  column?: number | null
  block_ids?: string[]
  metadata?: Record<string, unknown>
  scores?: Record<string, number | null | undefined>
}

export type GenerationAttempt = {
  provider?: string
  model?: string | null
  status?: string
  error?: string | null
  elapsed_ms?: number | null
  duration_ms?: number | null
}

export type GenerationInfo = {
  requested: GenerationProvider
  used: string
  model?: string | null
  fallback_reason?: string | null
  attempts?: GenerationAttempt[]
}

export type PipelineTraceStage = {
  id?: string
  label?: string
  state?: string
  status?: string
  duration_ms?: number | null
  detail?: string | null
}

export type ChatTrace = {
  request_id?: string
  stages?: PipelineTraceStage[]
}

export type ChatRequest = {
  question: string
  institution?: string
  role?: string
  top_k?: number
  provider: GenerationProvider
  model?: string
  parser_profile: ParserProfile
  retrieval_mode: RetrievalMode
}

export type ResolvedRole = {
  requested?: string | null
  id: string
  label: string
  institutions?: string[]
}

export type RoleOption = {
  id: string
  label: string
}

export type ChatResponse = {
  answer: string
  role?: ResolvedRole
  cited_answer?: string
  claims?: Claim[]
  citations?: AnswerCitation[]
  results: SearchResult[]
  generation?: Partial<GenerationInfo>
  generator?: string
  trace?: ChatTrace
  retrieval?: Record<string, unknown>
  request_id?: string
  parser_profile?: ParserProfile
  retrieval_mode?: RetrievalMode
}

export type PipelineStage = {
  id: string
  label: string
  state: string
  detail?: string
}

export type ProviderCapability = {
  id: ConcreteProvider
  label: string
  available: boolean
  state: string
  model?: string
  defaultModel?: string
  models?: ProviderModelCapability[]
  reason?: string
  runtimeState?: string
  loadedModel?: string
  unloadSupported?: boolean
  workerRunning?: boolean
}

export type ProviderModelCapability = {
  id: string
  label: string
  available: boolean
  reason?: string
}

export type ParserProfileCapability = {
  id: ParserProfile
  label: string
  ready: boolean
  chunkCount?: number
  documentCount?: number
  runId?: string
  corpusRevision?: string
  denseReady?: boolean
  defaultRetrievalMode?: RetrievalMode
  retrievalModes?: RetrievalModeCapability[]
  reason?: string
}

export type RetrievalModeCapability = {
  id: RetrievalMode
  label: string
  ready: boolean
  model?: string
  dimensions?: number
  chunkCount?: number
  modelLoaded?: boolean
  device?: string
  reason?: string
}

export type HealthResponse = {
  status?: string
  ready: boolean
  pipeline?: unknown
  providers?: unknown
  default_provider?: GenerationProvider
  chunk_count?: number
  institution_count?: number
  generation_mode?: string
  gemini_configured?: boolean
  gemini_model?: string
  default_parser_profile?: ParserProfile
  default_retrieval_mode?: RetrievalMode
  retrieval_modes?: unknown
  parser_profiles?: unknown
  roles?: unknown
  [key: string]: unknown
}

export type LocalModelUnloadResponse = {
  ok: true
  state: 'unloaded'
  loadedModel?: string
  released: boolean
}
