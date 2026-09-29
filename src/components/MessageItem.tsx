import {
  AlertTriangle,
  Check,
  CheckCircle2,
  Copy,
  FileSearch,
  LoaderCircle,
  RotateCcw,
  ShieldCheck,
} from 'lucide-react'
import {
  parserProfileDisplayName,
  providerDisplayName,
  retrievalModeDisplayName,
} from '../api/rag'
import type { ChatRequest } from '../api/rag'
import {
  contextDeduplicationSummary,
  generationFallbackHint,
  generationModelDisplayName,
  getSupportedClaimCount,
  retrievalSummary,
  traceSummary,
  uniqueDocumentCount,
  weakAnswerHint,
} from '../chat/presentation'
import { sanjiniSrc } from '../chat/constants'
import type { Message } from '../types/chat'
import AnswerContent from './AnswerContent'

export default function MessageItem({
  message,
  copied,
  retryDisabled,
  onCopy,
  onOpenSources,
  onRetry,
}: {
  message: Message
  copied: boolean
  retryDisabled: boolean
  onCopy: (message: Message) => void
  onOpenSources: (messageId: string, source?: number) => void
  onRetry: (request: ChatRequest) => void
}) {
  const supportedClaimCount = getSupportedClaimCount(message)
  const claimCount = message.claims?.length ?? 0
  const sourceCount = message.results?.length ?? 0
  const documentCount = uniqueDocumentCount(message.results)
  const requestTrace = traceSummary(message.trace)
  const retrieval = retrievalSummary(message.retrieval)
  const deduplication = contextDeduplicationSummary(message.retrieval)
  const hasFallback = Boolean(message.generation?.fallback_reason)
  const weakAnswer = weakAnswerHint(message)
  const originalRequest = message.request

  return (
    <article className={`message-row ${message.role}`}>
      {message.role === 'assistant' && (
        <img className="avatar" src={sanjiniSrc} alt="산지니" />
      )}
      <div className="message-bubble">
        {message.role === 'assistant' && (
          <div className="answer-meta">
            {message.status === 'error' ? (
              <ShieldCheck size={16} />
            ) : (
              <CheckCircle2 size={16} />
            )}
            <span>
              {message.status === 'error'
                ? message.error?.code === 'cancelled'
                  ? '요청 취소됨'
                  : '요청 처리 실패'
                : 'PNU Docs'}
            </span>
            {message.status !== 'error' && documentCount > 0 && (
              <span className="answer-grounding">
                문서 {documentCount}개 참고
              </span>
            )}
            <span className="sr-only">
              {message.status !== 'error'
                ? `검증된 문장 ${supportedClaimCount}개`
                : ''}
            </span>
          </div>
        )}

        {message.role === 'assistant' ? (
          <AnswerContent
            content={message.content}
            sourceNumbers={(message.results ?? []).map(
              (result, index) => result.source_number ?? index + 1,
            )}
            onCitationSelect={(source) => onOpenSources(message.id, source)}
          />
        ) : (
          <p>{message.content}</p>
        )}

        {message.role === 'assistant' && message.status !== 'error' && (
          <details className="answer-details">
            <summary>답변 정보</summary>
            <div className="answer-stats">
              {message.parserProfile && (
                <span className="parser-profile-badge">
                  파서 {parserProfileDisplayName(message.parserProfile, true)}
                </span>
              )}
              {message.request?.retrieval_mode && (
                <span>
                  {retrievalModeDisplayName(message.request.retrieval_mode)}
                </span>
              )}
              {message.resolvedRole?.requested && (
                <span className="role-badge">
                  역할{' '}
                  {message.resolvedRole.id === 'general'
                    ? '일반 사용자 (매핑 안 됨)'
                    : message.resolvedRole.label}
                </span>
              )}
              <span>근거 chunk {sourceCount}개</span>
              <span>고유 문서 {documentCount}개</span>
              <span>
                검증 문장{' '}
                {claimCount ? `${supportedClaimCount}/${claimCount}` : '0개'}
              </span>
              {message.generation && (
                <span>
                  {providerDisplayName(message.generation.used)}
                  {message.generation.model
                    ? ` · ${generationModelDisplayName(message.generation.model)}`
                    : ''}
                </span>
              )}
              {typeof message.durationMs === 'number' && (
                <span>총 {(message.durationMs / 1000).toFixed(1)}초</span>
              )}
              {retrieval && <span>{retrieval}</span>}
              {deduplication && <span>{deduplication}</span>}
              {requestTrace && <span>{requestTrace}</span>}
            </div>
          </details>
        )}

        {message.role === 'assistant' && hasFallback && (
          <div className="answer-hint generation-fallback">
            <AlertTriangle size={15} />
            <span>{generationFallbackHint(message.generation)}</span>
          </div>
        )}

        {message.role === 'assistant' && weakAnswer && (
          <div className="answer-hint">
            <AlertTriangle size={15} />
            <span>{weakAnswer}</span>
          </div>
        )}

        {message.role === 'assistant' && (
          <div className="message-actions">
            <button
              onClick={(event) => {
                event.stopPropagation()
                onCopy(message)
              }}
              type="button"
            >
              {copied ? <Check size={15} /> : <Copy size={15} />}
              {copied ? '복사 완료' : '복사'}
            </button>
            {message.status !== 'error' && (
              <button
                onClick={(event) => {
                  event.stopPropagation()
                  onOpenSources(message.id)
                }}
                type="button"
              >
                <FileSearch size={15} />
                출처 {documentCount}개
              </button>
            )}
            {(message.status !== 'error' || message.error?.retryable) &&
              originalRequest && (
                <button
                  disabled={retryDisabled}
                  onClick={(event) => {
                    event.stopPropagation()
                    onRetry(originalRequest)
                  }}
                  type="button"
                >
                  <RotateCcw size={15} />
                  {message.status === 'error' ? '다시 시도' : '다시 답변'}
                </button>
              )}
          </div>
        )}
        {message.error?.requestId && (
          <small className="request-id">
            요청 ID {message.error.requestId}
          </small>
        )}
      </div>
    </article>
  )
}

export function LoadingMessage({ elapsedSeconds }: { elapsedSeconds: number }) {
  return (
    <article className="message-row assistant is-loading">
      <img className="avatar" src={sanjiniSrc} alt="" />
      <div className="message-bubble loading-bubble">
        <div className="answer-meta">
          <span>PNU Docs</span>
          <span className="loading-time">{elapsedSeconds}초</span>
        </div>
        <div className="request-progress">
          <LoaderCircle className="loading-icon" size={17} />
          <span>
            {elapsedSeconds < 20
              ? '문서를 확인하고 답변을 준비하고 있어요.'
              : '답변에 시간이 조금 더 걸리고 있어요.'}
          </span>
        </div>
        {elapsedSeconds >= 20 && (
          <p className="loading-explanation">
            잠시 기다리거나, 아래 중지 버튼을 눌러 다시 질문할 수 있어요.
          </p>
        )}
      </div>
    </article>
  )
}
