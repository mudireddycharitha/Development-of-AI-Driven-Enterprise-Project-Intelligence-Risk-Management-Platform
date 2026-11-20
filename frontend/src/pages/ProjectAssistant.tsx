import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import { Bot, ChevronDown, MessageCircle, Send, Sparkles, Trash2, UserRound } from 'lucide-react'
import { Link } from 'react-router-dom'
import { EvidenceReferenceCard } from '../components/Milestone3Shared'
import { EmptyState, ErrorState, Loading, PageHeading } from '../components/Shared'
import { apiService, errorMessage } from '../services/api'
import type { AssistantMessage, AssistantResponse, DocumentRecord, ProjectHealthResult } from '../types'

const suggestedQuestions = [
  'Are we on track?',
  'What are our biggest project risks?',
  'Which milestones are delayed?',
  'What blockers are currently unresolved?',
  'What actions are pending?',
  'Who is responsible for the pending tasks?',
  'What are the major project dependencies?',
  'What decisions are still pending?',
  'What are the most important project deliverables?',
  'What should the project team focus on next?',
]

interface ChatEntry extends AssistantMessage {
  answer_mode?: AssistantResponse['answer_mode']
  health?: ProjectHealthResult | null
}

function createSessionId(): string {
  const key = 'project-intelligence-assistant-session'
  const saved = sessionStorage.getItem(key)
  if (saved) return saved
  const value = crypto.randomUUID()
  sessionStorage.setItem(key, value)
  return value
}

export function ProjectAssistant() {
  const [sessionId] = useState(createSessionId)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [indexedDocumentTotal, setIndexedDocumentTotal] = useState(0)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [messages, setMessages] = useState<ChatEntry[]>([])
  const [question, setQuestion] = useState('')
  const [loadingHistory, setLoadingHistory] = useState(true)
  const [sending, setSending] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [error, setError] = useState('')
  const scrollTarget = useRef<HTMLDivElement>(null)
  const sendingRef = useRef(false)
  const clearingRef = useRef(false)

  useEffect(() => {
    let active = true
    Promise.all([
      apiService.assistantConversation(sessionId),
      apiService.documents({ page: 1, page_size: 100, status: 'indexed' }),
    ]).then(([history, page]) => {
      if (!active) return
      setMessages(history)
      setDocuments(page.items)
      setIndexedDocumentTotal(page.total)
    }).catch(fetchError => { if (active) setError(errorMessage(fetchError)) })
      .finally(() => { if (active) setLoadingHistory(false) })
    return () => { active = false }
  }, [sessionId])

  useEffect(() => {
    scrollTarget.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, sending])

  const sendQuestion = async (value: string) => {
    const text = value.trim()
    if (!text || sendingRef.current) return
    sendingRef.current = true
    setSending(true)
    setError('')
    setQuestion('')
    try {
      const response = await apiService.askAssistant(sessionId, text, selectedIds.length ? selectedIds : undefined)
      const created = new Date(response.created_at).toISOString()
      setMessages(previous => [...previous,
        { role: 'user', content: response.question, sources: [], created_at: created },
        {
          role: 'assistant',
          content: response.answer,
          sources: response.sources,
          created_at: created,
          answer_mode: response.answer_mode,
          health: response.health,
        },
      ])
    } catch (requestError) {
      setError(errorMessage(requestError))
      setQuestion(text)
    } finally {
      sendingRef.current = false
      setSending(false)
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void sendQuestion(question)
  }

  const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void sendQuestion(question)
    }
  }

  const clearConversation = async () => {
    if (clearingRef.current || sendingRef.current) return
    clearingRef.current = true
    setClearing(true)
    setError('')
    try {
      await apiService.clearAssistantConversation(sessionId)
      setMessages([])
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      clearingRef.current = false
      setClearing(false)
    }
  }

  return <div className="m3-page">
    <PageHeading eyebrow="PROJECT INTELLIGENCE" title="Project Assistant"
      description="Ask questions about indexed project records and review the retrieved evidence behind each response."
      action={<button className="button button-quiet" onClick={() => void clearConversation()} disabled={clearing || sending || messages.length === 0}>
        <Trash2 size={14} /> {clearing ? 'Clearing…' : 'Clear conversation'}
      </button>} />
    {error && <div className="m3-assistant-error">
      <ErrorState message={error} />
      {question.trim() && <button className="button button-secondary" disabled={sending}
        onClick={() => void sendQuestion(question)}>Retry question</button>}
    </div>}

    <section className="m3-chat-panel panel">
      <div className="m3-chat-toolbar">
        <div><span className="m3-online-dot" /><strong>Evidence-grounded assistant</strong><small>Uses the shared project vector index</small></div>
        <details className="m3-document-filter">
          <summary><span>Sources: {selectedIds.length ? `${selectedIds.length} selected` : 'All indexed documents'}</span><ChevronDown size={14} /></summary>
          <div className="m3-document-filter-menu">
            {documents.length === 0 ? <p>No indexed documents available.</p> : documents.map(document =>
              <label key={document.id}><input type="checkbox" checked={selectedIds.includes(document.id)}
                onChange={() => setSelectedIds(previous => previous.includes(document.id)
                  ? previous.filter(id => id !== document.id) : [...previous, document.id])} />
                <span>{document.filename}</span></label>)}
            {indexedDocumentTotal > documents.length && <p>Specific filters list the first {documents.length} of {indexedDocumentTotal} documents; leaving this unfiltered searches all indexed documents.</p>}
            {selectedIds.length > 0 && <button className="text-link" onClick={() => setSelectedIds([])}>Use all indexed documents</button>}
          </div>
        </details>
      </div>

      <div className="m3-chat-messages">
        {loadingHistory && <Loading label="Loading conversation…" />}
        {!loadingHistory && messages.length === 0 && <div className="m3-chat-empty">
          <div className="m3-chat-empty-icon"><MessageCircle size={25} /></div>
          <h2>Ask about your project</h2>
          <p>Answers are generated from retrieved project evidence. If the configured LLM is unavailable, you’ll see a clear setup or service error instead of an unfiltered passage.</p>
          {documents.length === 0 && <EmptyState title="No indexed sources yet" text="Upload project documents to enable evidence retrieval." />}
          {documents.length > 0 && <div className="m3-suggested-questions"><strong><Sparkles size={14} /> Suggested questions</strong>
            <div>{suggestedQuestions.map(item => <button key={item} onClick={() => void sendQuestion(item)}>{item}</button>)}</div>
          </div>}
        </div>}

        {messages.map((message, index) => <article className={`m3-message ${message.role}`} key={`${message.created_at}-${index}`}>
          <div className="m3-message-avatar">{message.role === 'user' ? <UserRound size={16} /> : <Bot size={17} />}</div>
          <div className="m3-message-body">
            <div className="m3-message-heading"><strong>{message.role === 'user' ? 'You' : 'Project assistant'}</strong>
              {message.role === 'assistant' && message.answer_mode && <span className={`m3-answer-mode ${message.answer_mode}`}>
                {message.answer_mode === 'llm' ? 'LLM response'
                  : message.answer_mode === 'health_analysis' ? 'Health service analysis'
                  : message.answer_mode === 'extractive' ? 'Evidence extraction' : 'Insufficient evidence'}
              </span>}
              <time>{new Date(message.created_at).toLocaleTimeString()}</time>
            </div>
            <div className="m3-message-text"><FormattedAnswer content={message.content} sources={message.sources} /></div>
            {message.health && <HealthSnapshot health={message.health} />}
            {message.role === 'assistant' && message.sources.length > 0 && <div className="m3-message-sources">
              <strong>Retrieved evidence · {uniqueSources(message.sources).length}</strong>
              {uniqueSources(message.sources).map(source => <EvidenceReferenceCard key={source.chunk_id} evidence={{
                document_id: source.document_id,
                filename: source.filename,
                chunk_id: source.chunk_id,
                excerpt: source.excerpt,
                location: source.location,
              }} />)}
            </div>}
          </div>
        </article>)}
        {sending && <div className="m3-assistant-loading" role="status" aria-live="polite">
          <Bot size={16} /><span>Searching indexed project evidence and generating a grounded response…</span>
        </div>}
        <div ref={scrollTarget} />
      </div>

      <form className="m3-chat-input" onSubmit={submit}>
        <label className="sr-only" htmlFor="assistant-question">Ask a project question</label>
        <textarea id="assistant-question" value={question} onChange={event => setQuestion(event.target.value)}
          onKeyDown={keyDown} placeholder="Ask a question about project evidence…" maxLength={2000}
          disabled={sending || documents.length === 0} rows={2} />
        <div className="m3-chat-input-footer"><span>Enter to send · Shift+Enter for a new line · {question.length}/2000</span>
          <button type="submit" className="button button-primary" disabled={sending || !question.trim() || documents.length === 0}>
            <Send size={14} /> Send
          </button>
        </div>
      </form>
    </section>
  </div>
}

function FormattedAnswer({ content, sources }: { content: string; sources: AssistantMessage['sources'] }) {
  const sourceReferences = sources.slice(0, 4).map(source => {
    const location = Object.entries(source.location ?? {})
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `${key.replace('_number', '').replaceAll('_', ' ')} ${value}`)
    return [source.filename, ...location].join(', ')
  })
  const cleanedContent = content
    .replace(/<svg\b[^>]*>[\s\S]*?<\/svg\s*>/gi, '')
    .replace(/<svg\b[^>]*/gi, '')
    .replace(/<\/?(?:script|style|path|g|text|foreignObject)\b[^>]*>/gi, '')
    .replace(/<[^>]{1,300}>/g, '')
    .replace(/&lt;\s*svg\b[^&]*(?:&gt;|$)/gi, '')
    .replace(/\\([\\`*_{}\[\]()#+.!|>-])/g, '$1')
    .replace(/^\s*(?:xmlns=|viewBox=|data:image\/svg\+xml).*$/gim, '')
    .replace(/^\s*(?:svg|svg icon|svg label)\s*$/gim, '')
    .replace(/\[(S\d+)\]/gi, (_match, label: string) => {
      const source = sourceReferences[Number(label.slice(1)) - 1]
      return source ? `(${source})` : ''
    })
    .replace(/\bS\d+\b/gi, '')
    .replace(/\bIssue[- #]?\d+\b/gi, '')
    .replace(/([.!?])\1+/g, '$1')
    .replace(/([,;:])\s*\1+/g, '$1')
  const nodes: ReactNode[] = []
  let paragraph: string[] = []
  let bullets: string[] = []
  let ordered: string[] = []
  const flushParagraph = () => {
    if (!paragraph.length) return
    nodes.push(<p key={`p-${nodes.length}`}><InlineMarkdown content={paragraph.join(' ')} /></p>)
    paragraph = []
  }
  const flushList = () => {
    if (bullets.length) nodes.push(<ul key={`ul-${nodes.length}`}>{bullets.map((item, index) => <li key={index}><InlineMarkdown content={item} /></li>)}</ul>)
    if (ordered.length) nodes.push(<ol key={`ol-${nodes.length}`}>{ordered.map((item, index) => <li key={index}><InlineMarkdown content={item} /></li>)}</ol>)
    bullets = []
    ordered = []
  }
  for (const rawLine of cleanedContent.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) {
      flushParagraph()
      flushList()
      continue
    }
    const heading = line.match(/^(?:(?:[-*]\s*)?#{1,4}\s*)?\*\*([^*]+)\*\*:?\s*$/)
      ?? line.match(/^(Answer|Key findings|Evidence|Missing information)\s*:\s*$/i)
      ?? line.match(/^[-*]\s*(?:#{1,4}\s*)([^*:#].*?)\s*#*$/)
    if (heading) {
      flushParagraph()
      flushList()
      nodes.push(<h3 key={`h-${nodes.length}`}><InlineMarkdown content={heading[1]} /></h3>)
      continue
    }
    const unorderedItem = line.match(/^(?:[-*•])\s+(.+)$/)
    const orderedItem = line.match(/^\d+[.)]\s+(.+)$/)
    if (unorderedItem || orderedItem) {
      flushParagraph()
      if (unorderedItem) {
        if (ordered.length) flushList()
        bullets.push(unorderedItem[1])
      } else {
        if (bullets.length) flushList()
        ordered.push(orderedItem![1])
      }
      continue
    }
    flushList()
    paragraph.push(line)
  }
  flushParagraph()
  flushList()
  return <>{nodes}</>
}

function InlineMarkdown({ content }: { content: string }) {
  const tokens = content.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*|\[S\d+\])/g)
  return <>{tokens.map((token, index) => {
    const bold = token.match(/^\*\*([^*]+)\*\*$/)
    const italic = token.match(/^\*([^*]+)\*$/)
    const code = token.match(/^`([^`]+)`$/)
    const citation = token.match(/^\[(S\d+)\]$/)
    if (bold) return <strong key={index}>{bold[1]}</strong>
    if (italic) return <em key={index}>{italic[1]}</em>
    if (code) return <code key={index}>{code[1]}</code>
    if (citation) return <sup className="m3-citation" key={index}>{citation[1]}</sup>
    return token
  })}</>
}

function uniqueSources(sources: AssistantMessage['sources']) {
  const seen = new Set<string>()
  return sources.filter(source => {
    const excerpt = source.excerpt.replace(/\s+/g, ' ').trim().toLocaleLowerCase()
    const location = JSON.stringify(source.location ?? {})
    const key = `${source.document_id}:${location}:${excerpt}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function HealthSnapshot({ health }: { health: ProjectHealthResult }) {
  return <div className="m3-chat-health">
    <strong>Current project health: {health.classification}</strong>
    <span>{health.overall_score === null ? 'Overall score unavailable due to insufficient evidence.' : `${health.overall_score.toFixed(1)} / 100`}</span>
    <Link to="/project-health">View health breakdown</Link>
  </div>
}
