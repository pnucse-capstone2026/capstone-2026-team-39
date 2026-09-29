import {
  Clock3,
  FileSearch,
  FileText,
  ListChecks,
  ShieldCheck,
  X,
} from 'lucide-react'
import type { SearchResult } from '../api/rag'
import { MAX_DETAIL_LOCATIONS, MAX_SOURCE_LOCATIONS } from '../chat/constants'
import {
  claimValidationLabel,
  cleanPreview,
  getSupportedClaimCount,
  resultLocationSummary,
  resultScoreLabel,
  uniqueDocumentCount,
} from '../chat/presentation'
import type { Message } from '../types/chat'
import CitationLocation from './CitationLocation'
import Dialog from './Dialog'

export type SourceTab = 'claims' | 'sources' | 'locations'

const sourceTabs: SourceTab[] = ['claims', 'sources', 'locations']

function resultFileName(result: SearchResult) {
  return (
    result.source_title ??
    result.file_name ??
    result.relative_path ??
    '제목 없는 문서'
  )
}

function EmptySources({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof ShieldCheck
  title: string
  children: string
}) {
  return (
    <div className="empty-sources">
      <Icon size={26} />
      <strong>{title}</strong>
      <p>{children}</p>
    </div>
  )
}

function LocationOverflow({ count }: { count: number }) {
  return (
    <span className="location-overflow">외 {count.toLocaleString()}개 위치</span>
  )
}

function ClaimsTab({ answer }: { answer: Message }) {
  if (!answer.claims?.length) {
    return (
      <EmptySources icon={ListChecks} title="검증 문장이 없습니다.">
        답변 문장을 분리하지 못했거나 검색 근거가 부족합니다.
      </EmptySources>
    )
  }
  return (
    <div className="claim-list">
      {answer.claims.map((claim, claimIndex) => (
        <article className="claim-card" key={`${claimIndex}-${claim.text}`}>
          <p>{claim.text}</p>
          <div className="claim-meta">
            <span className={claim.supported ? 'is-supported' : 'is-unsupported'}>
              {claimValidationLabel(claim)}
            </span>
            {(claim.source_numbers ?? []).map((sourceNumber) => (
              <span className="source-badge" key={sourceNumber}>
                [{sourceNumber}]
              </span>
            ))}
          </div>
          {claim.citations && claim.citations.length > 0 && (
            <div className="claim-locations">
              {claim.citations.map((citation, index) => (
                <CitationLocation
                  compact
                  key={`${citation.source_number ?? 'source'}-${citation.block_id ?? index}`}
                  location={citation}
                />
              ))}
            </div>
          )}
        </article>
      ))}
    </div>
  )
}

function SourcesTab({
  answer,
  highlightedSource,
}: {
  answer: Message
  highlightedSource: number | null
}) {
  if (!answer.results?.length) {
    return (
      <EmptySources icon={ShieldCheck} title="검색된 근거가 없습니다.">
        기관 범위를 넓히거나 질문 표현을 바꿔 다시 검색해 주세요.
      </EmptySources>
    )
  }
  return (
    <div className="citation-list">
      {answer.results.map((result, index) => {
        const sourceNumber = result.source_number ?? index + 1
        const locationSummary = resultLocationSummary(
          result,
          MAX_SOURCE_LOCATIONS,
        )
        const fileName = resultFileName(result)
        const sourcePath = result.relative_path ?? result.source_path ?? ''
        return (
          <article
            className={`citation-card ${highlightedSource === sourceNumber ? 'is-highlighted' : ''}`}
            id={`source-${sourceNumber}`}
            tabIndex={-1}
            key={result.chunk_id || `${fileName}-${index}`}
          >
            <div className="citation-topline">
              <span>{result.institution ?? '기관 미상'}</span>
              <strong>출처 {sourceNumber}</strong>
            </div>
            <h3>{fileName}</h3>
            {sourcePath && <p className="location">{sourcePath}</p>}
            {result.preview && <p>{cleanPreview(result.preview)}</p>}
            {locationSummary.locations.length > 0 && (
              <div className="result-locations">
                {locationSummary.locations.map((location, locationIndex) => (
                  <CitationLocation
                    compact
                    key={`${result.chunk_id}-location-${locationIndex}`}
                    location={location}
                  />
                ))}
                {locationSummary.hiddenCount > 0 && (
                  <LocationOverflow count={locationSummary.hiddenCount} />
                )}
              </div>
            )}
            <dl>
              <div>
                <dt>문단</dt>
                <dd>{result.chunk_index ?? '—'}</dd>
              </div>
              <div>
                <dt>분량</dt>
                <dd>
                  {typeof result.char_count === 'number'
                    ? `${result.char_count}자`
                    : '—'}
                </dd>
              </div>
            </dl>
            {result.source_url && (
              <a
                className="source-link"
                href={result.source_url}
                rel="noreferrer"
                target="_blank"
              >
                원문 열기
              </a>
            )}
            {result.download_url &&
              result.download_url !== result.source_url && (
                <a
                  className="source-link"
                  href={result.download_url}
                  rel="noreferrer"
                  target="_blank"
                >
                  첨부 열기
                </a>
              )}
          </article>
        )
      })}
    </div>
  )
}

function LocationsTab({ answer }: { answer: Message }) {
  return (
    <div className="location-list">
      {answer.results?.map((result, index) => {
        const locationSummary = resultLocationSummary(
          result,
          MAX_DETAIL_LOCATIONS,
        )
        const fileName = resultFileName(result)
        return (
          <article
            className="location-card"
            key={result.chunk_id || `${fileName}-${index}`}
          >
            <strong>
              [{result.source_number ?? index + 1}] {fileName}
            </strong>
            <span>{result.institution ?? '기관 미상'}</span>
            {(result.relative_path || result.source_path) && (
              <p>{result.relative_path ?? result.source_path}</p>
            )}
            {locationSummary.locations.length > 0 ? (
              <div className="location-details">
                {locationSummary.locations.map((location, locationIndex) => (
                  <CitationLocation
                    key={`${result.chunk_id}-detail-${locationIndex}`}
                    location={location}
                  />
                ))}
                {locationSummary.hiddenCount > 0 && (
                  <LocationOverflow count={locationSummary.hiddenCount} />
                )}
              </div>
            ) : (
              <p className="no-location">
                세부 위치 정보가 제공되지 않았습니다.
              </p>
            )}
            <dl>
              <div>
                <dt>문단</dt>
                <dd>{result.chunk_index ?? '—'}</dd>
              </div>
              <div>
                <dt>관련도</dt>
                <dd>{resultScoreLabel(result)}</dd>
              </div>
            </dl>
          </article>
        )
      })}
      {!answer.results?.length && (
        <EmptySources icon={FileText} title="표시할 원문 위치가 없습니다.">
          검색 결과가 없는 답변입니다.
        </EmptySources>
      )}
    </div>
  )
}

const tabLabels: Record<SourceTab, { label: string; icon: typeof FileText }> = {
  claims: { label: '검증', icon: ListChecks },
  sources: { label: '문서', icon: FileSearch },
  locations: { label: '위치', icon: FileText },
}

function SourceTabs({
  value,
  onChange,
}: {
  value: SourceTab
  onChange: (tab: SourceTab) => void
}) {
  return (
    <div
      className="source-tabs"
      role="tablist"
      aria-label="근거 보기 방식"
      onKeyDown={(event) => {
        const current = sourceTabs.indexOf(value)
        const next =
          event.key === 'ArrowRight'
            ? (current + 1) % 3
            : event.key === 'ArrowLeft'
              ? (current + 2) % 3
              : event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? 2
                  : -1
        if (next < 0) return
        event.preventDefault()
        onChange(sourceTabs[next])
        const buttons =
          event.currentTarget.querySelectorAll<HTMLButtonElement>(
            '[role="tab"]',
          )
        buttons[next]?.focus()
      }}
    >
      {sourceTabs.map((tab) => {
        const { label, icon: Icon } = tabLabels[tab]
        return (
          <button
            aria-selected={value === tab}
            aria-controls="source-tab-content"
            id={`source-tab-${tab}`}
            key={tab}
            tabIndex={value === tab ? 0 : -1}
            className={value === tab ? 'is-active' : ''}
            onClick={() => onChange(tab)}
            role="tab"
            type="button"
          >
            <Icon size={15} />
            {label}
          </button>
        )
      })}
    </div>
  )
}

export default function SourceDialog({
  open,
  onClose,
  answer,
  tab,
  onTabChange,
  highlightedSource,
}: {
  open: boolean
  onClose: () => void
  answer: Message | null
  tab: SourceTab
  onTabChange: (tab: SourceTab) => void
  highlightedSource: number | null
}) {
  const supportedCount = answer ? getSupportedClaimCount(answer) : 0
  const claimCount = answer?.claims?.length ?? 0
  const sourceCount = answer?.results?.length ?? 0
  const documentCount = uniqueDocumentCount(answer?.results)
  const unsupportedCount = Math.max(claimCount - supportedCount, 0)

  return (
    <Dialog
      className="source-dialog"
      labelledBy="source-heading"
      onClose={onClose}
      open={open}
    >
      <div className="source-panel">
        <div className="source-panel-header">
          <div>
            <span className="eyebrow">답변의 출처를 직접 확인하세요</span>
            <h2 id="source-heading">근거 문서</h2>
          </div>
          <button
            aria-label="근거 패널 닫기"
            className="icon-button"
            onClick={onClose}
            type="button"
          >
            <X size={19} />
          </button>
        </div>

        {answer ? (
          <>
            <div className="confidence-box">
              <ShieldCheck size={21} />
              <div>
                <strong>참고한 근거 {sourceCount}개</strong>
                <span>
                  문서 {documentCount}개 · 문장 검증 {supportedCount}/
                  {claimCount || 0}
                  {unsupportedCount > 0
                    ? `, 근거 부족 ${unsupportedCount}`
                    : ''}
                </span>
              </div>
            </div>

            <SourceTabs value={tab} onChange={onTabChange} />

            <div
              role="tabpanel"
              id="source-tab-content"
              aria-labelledby={`source-tab-${tab}`}
            >
              {tab === 'claims' && <ClaimsTab answer={answer} />}
              {tab === 'sources' && (
                <SourcesTab
                  answer={answer}
                  highlightedSource={highlightedSource}
                />
              )}
              {tab === 'locations' && <LocationsTab answer={answer} />}
            </div>
          </>
        ) : (
          <EmptySources icon={Clock3} title="아직 선택된 답변이 없습니다.">
            질문을 보내면 답변에 참고한 문서와 원문 위치를 확인할 수 있어요.
          </EmptySources>
        )}
      </div>
    </Dialog>
  )
}
