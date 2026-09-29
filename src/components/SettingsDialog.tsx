import { Library, X } from 'lucide-react'
import { parserProfileDisplayName } from '../api/rag'
import type {
  GenerationProvider,
  HealthResponse,
  ParserProfile,
  ParserProfileCapability,
  ProviderCapability,
  RetrievalMode,
  RetrievalModeCapability,
  RoleOption,
} from '../api/rag'
import {
  MAX_ROLE_CHARS,
  customRoleId,
  defaultEvidenceTopK,
  evidenceScopePresets,
  noRole,
} from '../chat/constants'
import type { EvidenceTopK } from '../chat/constants'
import Dialog from './Dialog'
import PipelineStatus from './PipelineStatus'
import ProviderSelect from './ProviderSelect'

function retrievalModeDetail(capability: RetrievalModeCapability | undefined) {
  if (!capability?.ready) {
    return capability?.reason ?? '선택한 검색 인덱스를 확인할 수 없습니다.'
  }
  if (capability.id === 'bm25') {
    return '키워드 일치 기반 비교 기준선'
  }
  return `${capability.dimensions ?? '-'}차원 · ${
    capability.modelLoaded
      ? `${capability.device ?? 'device'} 로드됨`
      : '최초 검색 시 모델 로드'
  }`
}

export default function SettingsDialog({
  open,
  onClose,
  disabled,
  roleChoice,
  onRoleChoiceChange,
  customRole,
  onCustomRoleChange,
  roleOptions,
  provider,
  onProviderChange,
  pendingProvider,
  providers,
  localModel,
  onLocalModelChange,
  frontierModel,
  onFrontierModelChange,
  onUnloadLocalModel,
  unloadingLocalModel,
  unloadError,
  parserProfile,
  onParserProfileChange,
  parserProfiles,
  retrievalMode,
  onRetrievalModeChange,
  retrievalModes,
  topK,
  onTopKChange,
  health,
  healthError,
  healthRefreshing,
  onRefreshHealth,
}: {
  open: boolean
  onClose: () => void
  disabled: boolean
  roleChoice: string
  onRoleChoiceChange: (value: string) => void
  customRole: string
  onCustomRoleChange: (value: string) => void
  roleOptions: RoleOption[]
  provider: GenerationProvider
  onProviderChange: (value: GenerationProvider) => void
  pendingProvider: GenerationProvider | null
  providers: ProviderCapability[]
  localModel: string
  onLocalModelChange: (value: string) => void
  frontierModel: string
  onFrontierModelChange: (value: string) => void
  onUnloadLocalModel: () => void
  unloadingLocalModel: boolean
  unloadError: string | null
  parserProfile: ParserProfile
  onParserProfileChange: (value: ParserProfile) => void
  parserProfiles: ParserProfileCapability[]
  retrievalMode: RetrievalMode
  onRetrievalModeChange: (value: RetrievalMode) => void
  retrievalModes: RetrievalModeCapability[]
  topK: EvidenceTopK
  onTopKChange: (value: EvidenceTopK) => void
  health: HealthResponse | null
  healthError: string | null
  healthRefreshing: boolean
  onRefreshHealth: () => void
}) {
  const parserCapability = parserProfiles.find(
    (capability) => capability.id === parserProfile,
  )
  const retrievalCapability = retrievalModes.find(
    (capability) => capability.id === retrievalMode,
  )

  return (
    <Dialog
      className="settings-dialog"
      labelledBy="settings-heading"
      onClose={onClose}
      open={open}
    >
      <div className="dialog-header">
        <div>
          <span className="eyebrow">나에게 맞는 답변</span>
          <h2 id="settings-heading">답변 설정</h2>
        </div>
        <button
          aria-label="설정 닫기"
          className="icon-button"
          onClick={onClose}
          type="button"
        >
          <X size={20} />
        </button>
      </div>
      <div className="settings-body">
        <p className="settings-intro">
          기본 설정으로 바로 질문할 수 있어요. 필요할 때 원하는 방식으로 바꿔
          보세요.
        </p>
        <div className="institution-control role-control">
          <label htmlFor="role-choice">
            내 역할 <span className="optional-label">선택 사항</span>
          </label>
          <select
            aria-label="내 역할 선택"
            id="role-choice"
            disabled={disabled}
            onChange={(event) => onRoleChoiceChange(event.target.value)}
            value={roleChoice}
          >
            <option value={noRole}>역할 없음</option>
            {roleOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
            <option value={customRoleId}>직접 입력…</option>
          </select>
          {roleChoice === customRoleId ? (
            <input
              aria-label="역할 직접 입력"
              disabled={disabled}
              maxLength={MAX_ROLE_CHARS}
              onChange={(event) => onCustomRoleChange(event.target.value)}
              placeholder="예: 부산대 대학원생, 금감원 직원"
              type="text"
              value={customRole}
            />
          ) : null}
          <span>
            {roleChoice === noRole
              ? '역할을 알려주면 그 역할에 맞는 기관 문서를 우선합니다.'
              : '역할에 맞는 기관 문서를 우선하되 다른 기관 문서도 검색합니다.'}
          </span>
        </div>

        <div className="composer-options">
          <ProviderSelect
            activeRequestProvider={disabled ? pendingProvider : null}
            disabled={disabled}
            frontierModel={frontierModel}
            model={localModel}
            onChange={onProviderChange}
            onFrontierModelChange={onFrontierModelChange}
            onModelChange={onLocalModelChange}
            onUnloadLocalModel={onUnloadLocalModel}
            providers={providers}
            unloadError={unloadError}
            unloadingLocalModel={unloadingLocalModel}
            value={provider}
          />
          <label className="parser-profile-select">
            <span>검색 파싱 버전</span>
            <select
              aria-label="검색 파싱 버전"
              disabled={disabled}
              onChange={(event) =>
                onParserProfileChange(event.target.value as ParserProfile)
              }
              value={parserProfile}
            >
              {parserProfiles.map((capability) => (
                <option
                  disabled={!capability.ready}
                  key={capability.id}
                  value={capability.id}
                >
                  {capability.label}
                </option>
              ))}
            </select>
            <small>
              {parserCapability?.ready
                ? `${parserCapability.documentCount?.toLocaleString() ?? '-'}개 문서 · ${parserCapability.chunkCount?.toLocaleString() ?? '-'}개 chunk`
                : (parserCapability?.reason ??
                  '선택한 파서 인덱스를 확인할 수 없습니다.')}
            </small>
          </label>
          <label className="retrieval-mode-select">
            <span>검색 방식</span>
            <select
              aria-label="검색 방식"
              disabled={disabled}
              onChange={(event) =>
                onRetrievalModeChange(event.target.value as RetrievalMode)
              }
              value={retrievalMode}
            >
              {retrievalModes.map((capability) => (
                <option
                  disabled={!capability.ready}
                  key={capability.id}
                  value={capability.id}
                >
                  {capability.label}
                </option>
              ))}
            </select>
            <small>{retrievalModeDetail(retrievalCapability)}</small>
          </label>
        </div>
        <fieldset className="evidence-scope">
          <legend>검색 근거 범위</legend>
          <div className="evidence-scope-options">
            {evidenceScopePresets.map((preset) => (
              <button
                aria-pressed={topK === preset.value}
                className={topK === preset.value ? 'is-active' : ''}
                disabled={disabled}
                key={preset.value}
                onClick={() => onTopKChange(preset.value)}
                title={preset.detail}
                type="button"
              >
                <strong>{preset.label}</strong>
                <span>
                  {preset.value}개
                  {preset.value === defaultEvidenceTopK ? ' · 기본' : ''}
                </span>
              </button>
            ))}
          </div>
        </fieldset>

        <details className="service-details">
          <summary>
            <Library size={16} />
            검색 서비스 연결 상태
          </summary>
          <PipelineStatus
            chunkCount={parserCapability?.chunkCount}
            error={healthError}
            health={health}
            onRefresh={onRefreshHealth}
            profileLabel={parserProfileDisplayName(parserProfile, true)}
            profileReady={parserCapability?.ready}
            refreshing={healthRefreshing}
          />
        </details>
      </div>
      <div className="dialog-footer">
        <span>변경한 설정은 다음 질문부터 적용돼요.</span>
        <button className="primary-button" onClick={onClose} type="button">
          완료
        </button>
      </div>
    </Dialog>
  )
}
