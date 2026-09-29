import {
  ArrowDown,
  Building2,
  ChevronDown,
  Menu,
  PanelLeftOpen,
  PanelRightOpen,
  Settings2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  chat as requestChat,
  getHealth,
  getInstitutions,
  getParserProfileCapabilities,
  getProviderCapabilities,
  getRetrievalModeCapabilities,
  normalizeGeneration,
  parseRoleOptions,
  RagApiError,
  unloadLocalModel,
} from './api/rag'
import type {
  ChatRequest,
  GenerationProvider,
  HealthResponse,
  ParserProfile,
  RetrievalMode,
} from './api/rag'
import ChatComposer from './components/ChatComposer'
import ChatSidebar from './components/ChatSidebar'
import Dialog from './components/Dialog'
import MessageItem, { LoadingMessage } from './components/MessageItem'
import SettingsDialog from './components/SettingsDialog'
import SourceDialog from './components/SourceDialog'
import type { SourceTab } from './components/SourceDialog'
import { SuggestedQuestions, WelcomeHero } from './components/Welcome'
import useConversations from './hooks/useConversations'
import type { Conversation, Message } from './types/chat'
import {
  MAX_QUESTION_CHARS,
  MAX_ROLE_CHARS,
  allInstitutions,
  customRoleId,
  defaultEvidenceTopK,
  defaultInstitutions,
  fallbackRoleOptions,
  noRole,
  suggestedQuestions,
} from './chat/constants'
import type { EvidenceTopK } from './chat/constants'
import { selectProviderModel } from './chat/models'
import './App.css'

type MobilePanel = 'nav' | 'sources' | null

type SubmitQuestionOptions = {
  role?: string
  institution?: string
  provider?: GenerationProvider
  topK?: number
  model?: string | null
  parserProfile?: ParserProfile
  retrievalMode?: RetrievalMode
  appendUser?: boolean
}

function makeId() {
  return crypto.randomUUID()
}

function App() {
  const {
    messages,
    setMessages,
    conversations,
    activeId,
    selectConversation,
    deleteConversation,
    deleted,
    restoreConversation,
    dismissDeleted,
    storageError,
  } = useConversations()
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false)
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [highlightedSource, setHighlightedSource] = useState<number | null>(
    null,
  )
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(
    null,
  )
  const [toast, setToast] = useState('')
  const [showScrollButton, setShowScrollButton] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  const stickToBottomRef = useRef(true)
  const [input, setInput] = useState('')
  const [institution, setInstitution] = useState(
    () =>
      conversations
        .find((conversation) => conversation.id === activeId)
        ?.messages.findLast((message) => message.request)?.request
        ?.institution ?? allInstitutions,
  )
  const [institutions, setInstitutions] = useState(defaultInstitutions)
  const [roleChoice, setRoleChoice] = useState(noRole)
  const [customRole, setCustomRole] = useState('')
  const [provider, setProvider] = useState<GenerationProvider>('auto')
  const [parserProfile, setParserProfile] =
    useState<ParserProfile>('cascade')
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>('bm25')
  const [localModelPreference, setLocalModelPreference] = useState<
    string | null
  >(null)
  const [frontierModelPreference, setFrontierModelPreference] = useState<
    string | null
  >(null)
  const [topK, setTopK] = useState<EvidenceTopK>(defaultEvidenceTopK)
  const [pendingProvider, setPendingProvider] =
    useState<GenerationProvider | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [isHealthRefreshing, setIsHealthRefreshing] = useState(true)
  const [isLocalModelUnloading, setIsLocalModelUnloading] = useState(false)
  const [localModelUnloadError, setLocalModelUnloadError] = useState<
    string | null
  >(null)
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(
    null,
  )
  const [mobilePanel, setMobilePanel] = useState<MobilePanel>(null)
  const [sourceTab, setSourceTab] = useState<SourceTab>('claims')
  const [isLoading, setIsLoading] = useState(false)
  const [requestElapsedSeconds, setRequestElapsedSeconds] = useState(0)
  const messageStreamRef = useRef<HTMLDivElement | null>(null)
  const activeRequestRef = useRef<AbortController | null>(null)
  const requestConversationIdRef = useRef<string | null>(null)
  const localModelUnloadRequestRef = useRef(false)
  const healthRefreshSequenceRef = useRef(0)
  const defaultProviderAppliedRef = useRef(false)
  const defaultParserProfileAppliedRef = useRef(false)
  const defaultRetrievalModeAppliedRef = useRef(false)

  const assistantMessages = useMemo(
    () => messages.filter((message) => message.role === 'assistant'),
    [messages],
  )
  const roleOptions = useMemo(() => {
    const parsed = parseRoleOptions(health?.roles).filter(
      (option) => option.id !== 'general',
    )
    return parsed.length ? parsed : fallbackRoleOptions
  }, [health])
  const activeRole =
    roleChoice === noRole
      ? undefined
      : roleChoice === customRoleId
        ? customRole.trim().slice(0, MAX_ROLE_CHARS) || undefined
        : roleChoice
  const providerCapabilities = useMemo(
    () => getProviderCapabilities(health),
    [health],
  )
  const parserProfileCapabilities = useMemo(
    () => getParserProfileCapabilities(health),
    [health],
  )
  const selectedParserProfileCapability = useMemo(
    () =>
      parserProfileCapabilities.find(
        (capability) => capability.id === parserProfile,
      ),
    [parserProfile, parserProfileCapabilities],
  )
  const selectedParserProfileReady =
    health?.ready === true &&
    selectedParserProfileCapability?.ready === true
  const retrievalModeCapabilities = useMemo(
    () => getRetrievalModeCapabilities(health, parserProfile),
    [health, parserProfile],
  )
  const selectedRetrievalModeCapability = useMemo(
    () =>
      retrievalModeCapabilities.find(
        (capability) => capability.id === retrievalMode,
      ),
    [retrievalMode, retrievalModeCapabilities],
  )
  const selectedRetrievalModeReady =
    selectedRetrievalModeCapability?.ready === true
  const localProviderCapability = useMemo(
    () =>
      providerCapabilities.find((capability) => capability.id === 'local'),
    [providerCapabilities],
  )
  const frontierProviderCapability = useMemo(
    () =>
      providerCapabilities.find(
        (capability) => capability.id === 'frontier',
      ),
    [providerCapabilities],
  )
  const selectedLocalModel = useMemo(
    () => selectProviderModel(localProviderCapability, localModelPreference),
    [localModelPreference, localProviderCapability],
  )
  const selectedFrontierModel = useMemo(
    () =>
      selectProviderModel(frontierProviderCapability, frontierModelPreference),
    [frontierModelPreference, frontierProviderCapability],
  )
  const availableSuggestedQuestions = useMemo(
    () =>
      suggestedQuestions.filter((item) =>
        institutions.includes(item.institution),
      ),
    [institutions],
  )
  const selectedAnswer = useMemo(
    () =>
      assistantMessages.find(
        (message) => message.id === selectedMessageId,
      ) ??
      assistantMessages.at(-1) ??
      null,
    [assistantMessages, selectedMessageId],
  )

  const refreshStatus = useCallback(
    async (signal?: AbortSignal) => {
      const sequence = healthRefreshSequenceRef.current + 1
      healthRefreshSequenceRef.current = sequence
      setIsHealthRefreshing(true)
      const [healthResult, institutionResult] = await Promise.allSettled([
        getHealth(signal),
        getInstitutions(parserProfile, signal),
      ])
      if (
        signal?.aborted ||
        sequence !== healthRefreshSequenceRef.current
      ) {
        return
      }

      if (healthResult.status === 'fulfilled') {
        const nextHealth = healthResult.value
        setHealth(nextHealth)
        setHealthError(
          nextHealth.ready
            ? null
            : '검색 인덱스 또는 필수 파이프라인이 준비되지 않았습니다.',
        )
        const nextCapabilities = getProviderCapabilities(nextHealth)
        setLocalModelUnloadError(null)
        if (!defaultProviderAppliedRef.current) {
          const preferred = nextHealth.default_provider ?? 'auto'
          const preferredCapability = nextCapabilities.find(
            (capability) => capability.id === preferred,
          )
          setProvider(
            preferred === 'auto' || preferredCapability?.available
              ? preferred
              : 'auto',
          )
          defaultProviderAppliedRef.current = true
        } else {
          setProvider((current) =>
            current === 'auto' ||
            nextCapabilities.some(
              (capability) =>
                capability.id === current && capability.available,
            )
              ? current
              : 'auto',
          )
        }
        const nextParserProfiles = getParserProfileCapabilities(nextHealth)
        const preferredParserProfile =
          nextHealth.default_parser_profile ?? 'cascade'
        if (!defaultParserProfileAppliedRef.current) {
          const preferredCapability = nextParserProfiles.find(
            (capability) =>
              capability.id === preferredParserProfile && capability.ready,
          )
          setParserProfile(
            preferredCapability?.id ??
              nextParserProfiles.find((capability) => capability.ready)
                ?.id ??
              preferredParserProfile,
          )
          defaultParserProfileAppliedRef.current = true
        } else {
          setParserProfile((current) =>
            nextParserProfiles.some(
              (capability) => capability.id === current && capability.ready,
            )
              ? current
              : (nextParserProfiles.find((capability) => capability.ready)
                  ?.id ?? preferredParserProfile),
          )
        }
      } else {
        setHealth(null)
        setHealthError(
          healthResult.reason instanceof Error
            ? healthResult.reason.message
            : 'API 상태를 확인할 수 없습니다.',
        )
      }

      if (
        institutionResult.status === 'fulfilled' &&
        institutionResult.value.length > 0
      ) {
        const availableInstitutions = [
          allInstitutions,
          ...institutionResult.value.filter(
            (value) => value !== allInstitutions,
          ),
        ]
        setInstitutions(availableInstitutions)
        setInstitution((current) =>
          availableInstitutions.includes(current)
            ? current
            : allInstitutions,
        )
      }
      setIsHealthRefreshing(false)
    },
    [parserProfile],
  )

  useEffect(() => {
    if (!health) {
      return
    }
    const preferred =
      selectedParserProfileCapability?.defaultRetrievalMode ??
      health?.default_retrieval_mode ??
      'bm25'
    const available = retrievalModeCapabilities.filter(
      (capability) => capability.ready,
    )
    if (!defaultRetrievalModeAppliedRef.current) {
      setRetrievalMode(
        available.some((capability) => capability.id === preferred)
          ? preferred
          : (available[0]?.id ?? 'bm25'),
      )
      defaultRetrievalModeAppliedRef.current = true
      return
    }
    setRetrievalMode((current) =>
      available.some((capability) => capability.id === current)
        ? current
        : (available.find((capability) => capability.id === 'bm25')?.id ??
          available[0]?.id ??
          'bm25'),
    )
  }, [
    health,
    health?.default_retrieval_mode,
    retrievalModeCapabilities,
    selectedParserProfileCapability?.defaultRetrievalMode,
  ])

  useEffect(() => {
    const controller = new AbortController()
    const initialTimer = window.setTimeout(
      () => void refreshStatus(controller.signal),
      0,
    )
    const timer = window.setInterval(
      () => void refreshStatus(controller.signal),
      30000,
    )
    return () => {
      controller.abort()
      window.clearTimeout(initialTimer)
      window.clearInterval(timer)
    }
  }, [refreshStatus])

  useEffect(
    () => () => {
      activeRequestRef.current?.abort()
      activeRequestRef.current = null
      healthRefreshSequenceRef.current += 1
    },
    [],
  )

  useEffect(() => {
    if (!isLoading) {
      return
    }
    const timer = window.setInterval(
      () => setRequestElapsedSeconds((current) => current + 1),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [isLoading])

  useEffect(() => {
    if (
      activeRequestRef.current &&
      requestConversationIdRef.current !== activeId
    ) {
      activeRequestRef.current.abort()
      activeRequestRef.current = null
      setIsLoading(false)
      setPendingProvider(null)
    }
  }, [activeId])

  useEffect(() => {
    const stream = messageStreamRef.current
    if (!stream) {
      return
    }

    if (!stickToBottomRef.current) return
    const frame = requestAnimationFrame(() => {
      stream.scrollTop = stream.scrollHeight
    })
    return () => cancelAnimationFrame(frame)
  }, [isLoading, messages])

  useEffect(() => {
    if (mobilePanel !== 'sources' || highlightedSource === null) return
    const frame = requestAnimationFrame(() => {
      const source = document.getElementById(`source-${highlightedSource}`)
      source?.scrollIntoView({ block: 'nearest' })
      source?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [mobilePanel, highlightedSource, selectedMessageId])

  useEffect(() => {
    if (!toast && !copiedMessageId) return
    const timer = window.setTimeout(() => {
      setToast('')
      setCopiedMessageId(null)
    }, 2600)
    return () => window.clearTimeout(timer)
  }, [toast, copiedMessageId])

  const selectedInstitution = institutions.includes(institution)
    ? institution
    : allInstitutions
  const activeInstitution =
    selectedInstitution === allInstitutions
      ? undefined
      : selectedInstitution

  // 서버가 해당 파서 프로필과 검색 방식을 지금 처리할 수 있는지 확인한다.
  function canSearchWith(profile: ParserProfile, mode: RetrievalMode) {
    return (
      health?.ready === true &&
      parserProfileCapabilities.some(
        (capability) => capability.id === profile && capability.ready,
      ) &&
      getRetrievalModeCapabilities(health, profile).some(
        (capability) => capability.id === mode && capability.ready,
      )
    )
  }

  async function submitQuestion(
    question: string,
    options: SubmitQuestionOptions = {},
  ) {
    const institutionOverride = Object.prototype.hasOwnProperty.call(
      options,
      'institution',
    )
      ? options.institution
      : activeInstitution
    const providerOverride = options.provider ?? provider
    const roleOverride = Object.prototype.hasOwnProperty.call(
      options,
      'role',
    )
      ? options.role
      : activeRole
    const topKOverride = options.topK ?? topK
    const parserProfileOverride = options.parserProfile ?? parserProfile
    const retrievalModeOverride = options.retrievalMode ?? retrievalMode
    const modelOverride = Object.prototype.hasOwnProperty.call(
      options,
      'model',
    )
      ? options.model
      : providerOverride === 'local'
        ? selectedLocalModel
        : providerOverride === 'frontier'
          ? selectedFrontierModel
          : null
    const appendUser = options.appendUser ?? true
    const trimmed = question.trim()
    if (
      !trimmed ||
      activeRequestRef.current ||
      !canSearchWith(parserProfileOverride, retrievalModeOverride)
    ) {
      return
    }
    if (trimmed.length > MAX_QUESTION_CHARS) {
      const answer: Message = {
        id: makeId(),
        role: 'assistant',
        status: 'error',
        content: `질문은 ${MAX_QUESTION_CHARS.toLocaleString()}자 이내로 입력해 주세요.`,
        results: [],
        error: {
          code: 'question_too_long',
          retryable: false,
        },
      }
      setMessages((current) => [...current, answer])
      setSelectedMessageId(answer.id)
      return
    }

    const normalizedModel = modelOverride?.trim()
    const request: ChatRequest = {
      question: trimmed,
      institution: institutionOverride,
      ...(roleOverride ? { role: roleOverride } : {}),
      provider: providerOverride,
      top_k: topKOverride,
      parser_profile: parserProfileOverride,
      retrieval_mode: retrievalModeOverride,
      ...((providerOverride === 'local' ||
        providerOverride === 'frontier') &&
      normalizedModel
        ? { model: normalizedModel }
        : {}),
    }

    let conversationId = activeId
    if (appendUser) {
      const userMessage: Message = {
        id: makeId(),
        role: 'user',
        content: trimmed,
      }
      conversationId = setMessages((current) => [...current, userMessage])
    }
    const requestStartedAt = window.performance.now()
    setInput('')
    stickToBottomRef.current = true
    setShowScrollButton(false)
    setRequestElapsedSeconds(0)
    setIsLoading(true)
    if (providerOverride === 'local' || providerOverride === 'auto') {
      setLocalModelUnloadError(null)
    }
    setPendingProvider(providerOverride)

    const controller = new AbortController()
    activeRequestRef.current = controller
    requestConversationIdRef.current = conversationId

    try {
      const data = await requestChat(request, controller.signal)
      if (activeRequestRef.current !== controller) return
      const answer: Message = {
        id: makeId(),
        role: 'assistant',
        status: 'search',
        content: data.cited_answer ?? data.answer,
        claims: data.claims ?? [],
        results: data.results,
        resolvedRole: data.role,
        generation: normalizeGeneration(data, providerOverride),
        trace: data.trace,
        retrieval: data.retrieval,
        parserProfile: data.parser_profile ?? parserProfileOverride,
        request,
        durationMs: window.performance.now() - requestStartedAt,
      }
      setMessages((current) => [...current, answer], conversationId)
      setSelectedMessageId(answer.id)
      setSourceTab(answer.claims?.length ? 'claims' : 'sources')
    } catch (error) {
      if (activeRequestRef.current !== controller) return
      const apiError =
        error instanceof RagApiError
          ? error
          : new RagApiError(
              '요청을 처리하지 못했습니다. 다시 시도해 주세요.',
              {
                code: 'unknown_error',
                retryable: true,
              },
            )
      const answer: Message = {
        id: makeId(),
        role: 'assistant',
        status: 'error',
        content: apiError.message,
        results: [],
        error: {
          code: apiError.code,
          retryable: apiError.retryable,
          requestId: apiError.requestId,
        },
        request,
        parserProfile: parserProfileOverride,
        durationMs: window.performance.now() - requestStartedAt,
      }
      setMessages((current) => [...current, answer], conversationId)
      setSelectedMessageId(answer.id)
      setSourceTab('sources')
    } finally {
      if (activeRequestRef.current === controller) {
        activeRequestRef.current = null
        setIsLoading(false)
        setPendingProvider(null)
        if (providerOverride === 'local' || providerOverride === 'auto') {
          void refreshStatus()
        }
      }
    }
  }

  async function handleLocalModelUnload() {
    if (
      isLoading ||
      localModelUnloadRequestRef.current ||
      isLocalModelUnloading
    ) {
      return
    }
    localModelUnloadRequestRef.current = true
    setIsLocalModelUnloading(true)
    setLocalModelUnloadError(null)
    try {
      await unloadLocalModel()
      await refreshStatus()
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : '로컬 모델을 메모리에서 내리지 못했습니다.'
      await refreshStatus()
      setLocalModelUnloadError(message)
    } finally {
      localModelUnloadRequestRef.current = false
      setIsLocalModelUnloading(false)
    }
  }

  const cancelRequest = () => {
    activeRequestRef.current?.abort()
  }

  function openSources(messageId: string, source: number | null = null) {
    setSelectedMessageId(messageId)
    setHighlightedSource(source)
    setSourceTab('sources')
    setMobilePanel('sources')
  }

  function changeConversation(id: string | null) {
    if (activeRequestRef.current) return
    selectConversation(id)
    resetConversationView(
      conversations.find((conversation) => conversation.id === id),
    )
  }

  function resetConversationView(conversation: Conversation | undefined) {
    if (conversation) {
      const lastRequest = conversation.messages.findLast(
        (message) => message.request,
      )?.request
      const savedInstitution = lastRequest?.institution ?? allInstitutions
      setInstitution(
        institutions.includes(savedInstitution)
          ? savedInstitution
          : allInstitutions,
      )
    }
    setInput('')
    setSelectedMessageId(null)
    setMobilePanel(null)
    stickToBottomRef.current = true
    setShowScrollButton(false)
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  function openSettings() {
    setMobilePanel(null)
    setIsSettingsOpen(true)
  }

  async function copyAnswer(message: Message) {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopiedMessageId(message.id)
    } catch {
      setToast('복사하지 못했어요. 답변을 선택해서 복사해 주세요.')
    }
  }


  const ready = selectedParserProfileReady && selectedRetrievalModeReady
  const sidebarProps = {
    conversations,
    activeId,
    disabled: isLoading,
    ready,
    checking: isHealthRefreshing && !health,
    onNew: () => changeConversation(null),
    onSelect: changeConversation,
    onDelete: (id: string) => {
      if (activeRequestRef.current) return
      deleteConversation(id)
      setMobilePanel(null)
      if (id === activeId) {
        setSelectedMessageId(null)
        setInput('')
      }
    },
    onSettings: openSettings,
  }

  return (
    <main
      className={`app-shell ${isSidebarCollapsed ? 'sidebar-collapsed' : ''}`}
    >
      <a
        className="skip-link"
        href="#question-input-area"
        onClick={(event) => {
          event.preventDefault()
          inputRef.current?.focus()
        }}
      >
        질문 입력으로 건너뛰기
      </a>
      <div className="sidebar-desktop">
        <ChatSidebar
          {...sidebarProps}
          onClose={() => setIsSidebarCollapsed(true)}
        />
      </div>
      <Dialog
        className="navigation-dialog"
        labelledBy="mobile-nav-heading"
        onClose={() => setMobilePanel(null)}
        open={mobilePanel === 'nav'}
      >
        <h2 className="sr-only" id="mobile-nav-heading">
          대화 목록
        </h2>
        <ChatSidebar
          {...sidebarProps}
          mobile
          onClose={() => setMobilePanel(null)}
        />
      </Dialog>
      <section className="chat-column">
        <header className="chat-header">
          <div className="header-title-group">
            <button
              aria-label="대화 목록 열기"
              className="icon-button mobile-only"
              onClick={() => setMobilePanel('nav')}
              type="button"
            >
              <Menu size={20} />
            </button>
            {isSidebarCollapsed && (
              <button
                aria-label="사이드바 펼치기"
                className="icon-button desktop-only"
                onClick={() => setIsSidebarCollapsed(false)}
                title="사이드바 펼치기"
                type="button"
              >
                <PanelLeftOpen size={20} />
              </button>
            )}
            <h1>공문서 도우미</h1>
            <button
              aria-label="답변 모델 설정"
              className="model-trigger"
              onClick={openSettings}
              type="button"
            >
              {provider === 'auto'
                ? '자동 선택'
                : provider === 'local'
                  ? '로컬 모델'
                  : 'Gemini'}
              <ChevronDown size={13} />
            </button>
          </div>
          <div className="header-actions">
            <button
              aria-label="근거 문서 열기"
              className="sources-trigger"
              disabled={!selectedAnswer}
              onClick={() =>
                selectedAnswer && openSources(selectedAnswer.id)
              }
              type="button"
            >
              <PanelRightOpen size={17} />
              <span>근거 문서</span>
            </button>
            <button
              aria-label="설정 열기"
              className="icon-button"
              onClick={openSettings}
              title="설정"
              type="button"
            >
              <Settings2 size={18} />
            </button>
          </div>
        </header>
        <div
          className={`chat-workspace ${messages.length ? 'has-messages' : 'is-empty'}`}
        >
          {messages.length === 0 && (
            <WelcomeHero />
          )}
          {messages.length > 0 && (
            <div
              role="log"
              aria-label="대화 내용"
              aria-relevant="additions"
              aria-busy={isLoading}
              className="message-stream"
              onScroll={(event) => {
                const stream = event.currentTarget
                const nearBottom =
                  stream.scrollHeight -
                    stream.scrollTop -
                    stream.clientHeight <
                  100
                stickToBottomRef.current = nearBottom
                setShowScrollButton(!nearBottom)
              }}
              ref={messageStreamRef}
            >
              <div className="message-list">
                {messages.map((message) => (
                  <MessageItem
                    copied={copiedMessageId === message.id}
                    key={message.id}
                    message={message}
                    onCopy={(target) => void copyAnswer(target)}
                    onOpenSources={openSources}
                    onRetry={(request) =>
                      void submitQuestion(request.question, {
                        institution: request.institution,
                        role: request.role,
                        provider: request.provider,
                        topK: request.top_k ?? defaultEvidenceTopK,
                        model: request.model ?? null,
                        parserProfile: request.parser_profile,
                        retrievalMode: request.retrieval_mode,
                        appendUser: false,
                      })
                    }
                    retryDisabled={
                      isLoading ||
                      !message.request ||
                      !canSearchWith(
                        message.request.parser_profile,
                        message.request.retrieval_mode,
                      )
                    }
                  />
                ))}

                {isLoading && (
                  <LoadingMessage elapsedSeconds={requestElapsedSeconds} />
                )}
              </div>
            </div>
          )}
          <div className="composer-dock" id="question-input-area">
            {showScrollButton && messages.length > 0 && (
              <button
                aria-label="최신 답변으로 이동"
                className="scroll-bottom-button"
                onClick={() => {
                  const stream = messageStreamRef.current!
                  stream.scrollTo({
                    top: stream.scrollHeight,
                    behavior: window.matchMedia(
                      '(prefers-reduced-motion: reduce)',
                    ).matches
                      ? 'instant'
                      : 'smooth',
                  })
                  stickToBottomRef.current = true
                  setShowScrollButton(false)
                }}
                type="button"
              >
                <ArrowDown size={17} />
              </button>
            )}
            <ChatComposer
              value={input}
              onChange={setInput}
              onSubmit={() => void submitQuestion(input)}
              onCancel={cancelRequest}
              onSettings={openSettings}
              onRefresh={() => void refreshStatus()}
              inputRef={inputRef}
              loading={isLoading}
              ready={ready}
              checking={isHealthRefreshing && !health}
              institution={selectedInstitution}
              institutions={institutions}
              onInstitutionChange={setInstitution}
              maxLength={MAX_QUESTION_CHARS}
              hasMessages={messages.length > 0}
            />
          </div>
          {messages.length === 0 && (
            <SuggestedQuestions
              questions={availableSuggestedQuestions}
              onSelect={(item) => {
                setInstitution(item.institution)
                setInput(item.question)
                inputRef.current?.focus()
              }}
            />
          )}
        </div>
        {messages.length === 0 && (
          <footer className="workspace-footer">
            <Building2 size={13} />
            부산대학교와 공공기관의 문서를 한곳에서
          </footer>
        )}
        {storageError && (
          <div className="storage-warning" role="status">
            이 브라우저에 대화를 저장할 수 없어요. 필요한 답변은 복사해
            보관해 주세요.
          </div>
        )}
      </section>
      <SettingsDialog
        customRole={customRole}
        disabled={isLoading}
        frontierModel={selectedFrontierModel}
        health={health}
        healthError={healthError}
        healthRefreshing={isHealthRefreshing}
        localModel={selectedLocalModel}
        onClose={() => setIsSettingsOpen(false)}
        onCustomRoleChange={setCustomRole}
        onFrontierModelChange={setFrontierModelPreference}
        onLocalModelChange={setLocalModelPreference}
        onParserProfileChange={setParserProfile}
        onProviderChange={setProvider}
        onRefreshHealth={() => void refreshStatus()}
        onRetrievalModeChange={setRetrievalMode}
        onRoleChoiceChange={setRoleChoice}
        onTopKChange={setTopK}
        onUnloadLocalModel={() => void handleLocalModelUnload()}
        open={isSettingsOpen}
        parserProfile={parserProfile}
        parserProfiles={parserProfileCapabilities}
        pendingProvider={pendingProvider}
        provider={provider}
        providers={providerCapabilities}
        retrievalMode={retrievalMode}
        retrievalModes={retrievalModeCapabilities}
        roleChoice={roleChoice}
        roleOptions={roleOptions}
        topK={topK}
        unloadError={localModelUnloadError}
        unloadingLocalModel={isLocalModelUnloading}
      />
      <SourceDialog
        answer={selectedAnswer}
        highlightedSource={highlightedSource}
        onClose={() => setMobilePanel(null)}
        onTabChange={setSourceTab}
        open={mobilePanel === 'sources'}
        tab={sourceTab}
      />

      {(toast || deleted) && (
        <div className="toast" role="status">
          <span>{toast || '대화를 삭제했어요.'}</span>
          {deleted && !toast && (
            <button
              disabled={isLoading}
              onClick={() => {
                restoreConversation()
                resetConversationView(deleted)
              }}
              type="button"
            >
              되돌리기
            </button>
          )}
          <button
            aria-label="알림 닫기"
            onClick={() => {
              setToast('')
              dismissDeleted()
            }}
            type="button"
          >
            <X size={14} />
          </button>
        </div>
      )}
    </main>
  )
}

export default App
