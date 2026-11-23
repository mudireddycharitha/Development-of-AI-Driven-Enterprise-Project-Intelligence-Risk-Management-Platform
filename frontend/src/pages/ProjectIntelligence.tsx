import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, ArrowUpRight, CheckCircle2, FileText, ListChecks, LoaderCircle, RefreshCcw, ShieldAlert, Sparkles, Target, Upload, X } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { BlockerAnalysis, BlockerItem, DocumentRecord, EvidenceReference, RiskAnalysis, RiskFinding, ScopeAnalysis, ScopeFinding } from '../types'

const emptyScope: ScopeAnalysis = {
  goals: [], objectives: [], scope: [], deliverables: [], milestones: [], responsibilities: [], dependencies: [], progress: [], findings: [], missing_information: [], source_documents: [], notes: [],
}

const emptyRisks: RiskAnalysis = { findings: [], severity_counts: { Critical: 0, High: 0, Medium: 0, Low: 0, Unrated: 0 }, missing_information: [], source_documents: [], rules_used: [], notes: [] }
const emptyBlockers: BlockerAnalysis = { blockers: [], unresolved_issues: [], pending_decisions: [], action_items: [], missing_information: [], source_documents: [], notes: [] }

interface FindingDetail {
  agent: string
  category: string
  title: string
  description: string
  severity?: string | null
  likelihood?: string | null
  owner?: string | null
  priority?: string | null
  status?: string | null
  assignee?: string | null
  dueDate?: string | null
  related: string[]
  recommendations: string[]
  evidence: EvidenceReference[]
  verification: string
}

function ResultList({ title, items }: { title: string; items: Array<string | null> }) {
  const values = items.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
  return <div className="agent-result-list">
    <h3>{title}</h3>
    {values.length ? <ul>{values.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul>
      : <p>Not found in the selected documents.</p>}
  </div>
}

function PopulatedResultList({ title, items }: { title: string; items: Array<string | null> }) {
  if (!items.some(item => typeof item === 'string' && item.trim().length > 0)) return null
  return <ResultList title={title} items={items} />
}

function EvidenceLinks({ evidence, onOpen }: { evidence: EvidenceReference[]; onOpen: (evidence: EvidenceReference) => void }) {
  if (!evidence.length) return <small className="evidence-unavailable">Evidence not available in the selected documents.</small>
  return <div className="inline-evidence">
    {evidence.map((reference, index) => <button key={`${reference.document_id ?? reference.filename ?? 'source'}-${reference.chunk_id ?? index}`} onClick={() => onOpen(reference)}>
      <FileText size={12} /> {reference.filename || 'Source document'}{reference.location.page_number ? ` · page ${reference.location.page_number}` : ''}{reference.location.slide_number ? ` · slide ${reference.location.slide_number}` : ''}{reference.location.sheet_name ? ` · ${reference.location.sheet_name}` : ''}{reference.location.row_number ? ` · row ${reference.location.row_number}` : ''}
    </button>)}
    {evidence[0]?.excerpt && <small>{evidence[0].excerpt}</small>}
  </div>
}

function AgentAnalysisSummary({ status, duration, completedAt, documentCount, findingCount, sourceDocuments, notes }: {
  status: string
  duration?: number
  completedAt?: string
  documentCount: number
  findingCount: number
  sourceDocuments: string[]
  notes: string[]
}) {
  return <details className="agent-analysis-summary">
    <summary>Analysis summary and processing details</summary>
    <div className="agent-summary-grid">
      <span>Status<strong>{status === 'succeeded' ? 'Completed' : status === 'running' ? 'Running' : status === 'failed' ? 'Failed' : 'Not started'}</strong></span>
      <span>Documents analyzed<strong>{documentCount}</strong></span>
      <span>Findings<strong>{findingCount}</strong></span>
      <span>Duration<strong>{duration == null ? '—' : `${(duration / 1000).toFixed(1)} sec`}</strong></span>
      <span>Completed<strong>{completedAt || '—'}</strong></span>
      <span>Source coverage<strong>{sourceDocuments.length} document{sourceDocuments.length === 1 ? '' : 's'}</strong></span>
    </div>
    {sourceDocuments.length > 0 && <p>Sources: {sourceDocuments.join(', ')}</p>}
    {notes.length > 0 && <p>{notes.join(' ')}</p>}
  </details>
}

function agentFailureMessage(error: unknown) {
  const detail = errorMessage(error)
  return /AI analysis did not complete|incomplete answer|generation service|Ollama/i.test(detail)
    ? 'The AI service could not complete this agent. Check its status and retry.'
    : detail
}

function logAgentTiming(agent: string, startedAt: number, outcome: 'completed' | 'failed') {
  console.info(`[Project Intelligence] ${agent} ${outcome} in ${(performance.now() - startedAt).toFixed(0)} ms`)
}

export function ProjectIntelligence() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedView = searchParams.get('view')
  const activeView = requestedView === 'scope' || requestedView === 'risks' || requestedView === 'blockers' ? requestedView : 'all'
  const pageTitle = activeView === 'scope' ? 'Scope & Deliverables'
    : activeView === 'risks' ? 'Risk Intelligence'
      : activeView === 'blockers' ? 'Blockers & Actions' : 'Project Intelligence'
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [scope, setScope] = useState<ScopeAnalysis>(emptyScope)
  const [risks, setRisks] = useState<RiskAnalysis>(emptyRisks)
  const [blockers, setBlockers] = useState<BlockerAnalysis>(emptyBlockers)
  const [loading, setLoading] = useState(false)
  const [blockerRunStatus, setBlockerRunStatus] = useState<'idle' | 'running' | 'succeeded' | 'failed'>('idle')
  const [scopeRunStatus, setScopeRunStatus] = useState<'idle' | 'running' | 'succeeded' | 'failed'>('idle')
  const [riskRunStatus, setRiskRunStatus] = useState<'idle' | 'running' | 'succeeded' | 'failed'>('idle')
  const [agentDurations, setAgentDurations] = useState<Record<string, number>>({})
  const [agentCompletionTimes, setAgentCompletionTimes] = useState<Record<string, string>>({})
  const [runningAll, setRunningAll] = useState(false)
  const [sourcesLoading, setSourcesLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedFinding, setSelectedFinding] = useState<FindingDetail | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [priorityFilter, setPriorityFilter] = useState('')

  const clearAgentResults = () => {
    setScope(emptyScope)
    setRisks(emptyRisks)
    setBlockers(emptyBlockers)
    setScopeRunStatus('idle')
    setRiskRunStatus('idle')
    setBlockerRunStatus('idle')
  }

  const loadDocuments = async () => {
    setSourcesLoading(true)
    setError(null)
    try {
      const page = await apiService.documents({ page: 1, page_size: 100 })
      const items = page.items.filter(item => item.processing_status === 'indexed')
      setDocuments(items)
      setSelectedIds(items.map(item => item.id))
      clearAgentResults()
    } catch (fetchError) {
      setError(errorMessage(fetchError))
    } finally {
      setSourcesLoading(false)
    }
  }

  useEffect(() => { void loadDocuments() }, [])
  useEffect(() => {
    if (!selectedFinding) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelectedFinding(null)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [selectedFinding])

  const availableScope = useMemo(() => scope.findings.length > 0 || scope.goals.length > 0 || scope.objectives.length > 0 || scope.scope.length > 0 || scope.deliverables.length > 0 || scope.milestones.length > 0 || scope.responsibilities.length > 0 || scope.dependencies.length > 0, [scope])
  const availableRisks = useMemo(() => risks.findings.length > 0 || risks.missing_information.length > 0, [risks])
  const availableBlockers = useMemo(() => blockers.blockers.length || blockers.unresolved_issues.length || blockers.pending_decisions.length || blockers.action_items.length || blockers.missing_information.length, [blockers])
  const hasBlockerFindings = blockers.blockers.length > 0 || blockers.unresolved_issues.length > 0 || blockers.pending_decisions.length > 0 || blockers.action_items.length > 0

  const recordAgentDuration = (agent: string, startedAt: number) => {
    setAgentDurations(previous => ({ ...previous, [agent]: performance.now() - startedAt }))
    setAgentCompletionTimes(previous => ({ ...previous, [agent]: new Date().toLocaleTimeString() }))
  }

  const runScopeAnalysis = async () => {
    setScope(emptyScope)
    const startedAt = performance.now()
    setLoading(true)
    setScopeRunStatus('running')
    setError(null)
    try {
      const result = await apiService.scopeAnalysis(selectedIds.length ? selectedIds : undefined)
      setScope(result)
      setScopeRunStatus('succeeded')
      recordAgentDuration('scope', startedAt)
      logAgentTiming('Scope agent', startedAt, 'completed')
    } catch (fetchError) {
      setScopeRunStatus('failed')
      recordAgentDuration('scope', startedAt)
      setError(agentFailureMessage(fetchError))
      logAgentTiming('Scope agent', startedAt, 'failed')
    } finally {
      setLoading(false)
    }
  }

  const runRiskAnalysis = async () => {
    setRisks(emptyRisks)
    const startedAt = performance.now()
    setLoading(true)
    setRiskRunStatus('running')
    setError(null)
    try {
      const result = await apiService.riskAnalysis(selectedIds.length ? selectedIds : undefined)
      setRisks(result)
      setRiskRunStatus('succeeded')
      recordAgentDuration('risks', startedAt)
      logAgentTiming('Risk agent', startedAt, 'completed')
    } catch (fetchError) {
      setRiskRunStatus('failed')
      recordAgentDuration('risks', startedAt)
      setError(agentFailureMessage(fetchError))
      logAgentTiming('Risk agent', startedAt, 'failed')
    } finally {
      setLoading(false)
    }
  }

  const runBlockerAnalysis = async () => {
    const startedAt = performance.now()
    setLoading(true)
    setError(null)
    setBlockerRunStatus('running')
    setBlockers(emptyBlockers)
    try {
      const result = await apiService.blockerAnalysis(selectedIds.length ? selectedIds : undefined)
      setBlockers(result)
      setBlockerRunStatus('succeeded')
      recordAgentDuration('blockers', startedAt)
      logAgentTiming('Blocker agent', startedAt, 'completed')
    } catch (fetchError) {
      setBlockerRunStatus('failed')
      recordAgentDuration('blockers', startedAt)
      setError(agentFailureMessage(fetchError))
      logAgentTiming('Blocker agent', startedAt, 'failed')
    } finally {
      setLoading(false)
    }
  }

  const runAllAgents = async () => {
    if (!selectedIds.length) {
      setError('Select at least one indexed document before running the agents.')
      return
    }
    setRunningAll(true)
    setLoading(true)
    const runStartedAt = performance.now()
    const scopeStartedAt = performance.now()
    const riskStartedAt = performance.now()
    const blockerStartedAt = performance.now()
    clearAgentResults()
    setError(null)
    setScopeRunStatus('running')
    setRiskRunStatus('running')
    setBlockerRunStatus('running')
    const failures: string[] = []
    try {
      await Promise.all([
        apiService.scopeAnalysis(selectedIds).then(result => {
          setScope(result)
          setScopeRunStatus('succeeded')
          recordAgentDuration('scope', scopeStartedAt)
          logAgentTiming('Scope agent', scopeStartedAt, 'completed')
        }).catch(requestError => {
          setScopeRunStatus('failed')
          recordAgentDuration('scope', scopeStartedAt)
          failures.push(`Scope agent failed: ${agentFailureMessage(requestError)}`)
          logAgentTiming('Scope agent', scopeStartedAt, 'failed')
        }),
        apiService.riskAnalysis(selectedIds).then(result => {
          setRisks(result)
          setRiskRunStatus('succeeded')
          recordAgentDuration('risks', riskStartedAt)
          logAgentTiming('Risk agent', riskStartedAt, 'completed')
        }).catch(requestError => {
          setRiskRunStatus('failed')
          recordAgentDuration('risks', riskStartedAt)
          failures.push(`Risk agent failed: ${agentFailureMessage(requestError)}`)
          logAgentTiming('Risk agent', riskStartedAt, 'failed')
        }),
        apiService.blockerAnalysis(selectedIds).then(result => {
          setBlockers(result)
          setBlockerRunStatus('succeeded')
          recordAgentDuration('blockers', blockerStartedAt)
          logAgentTiming('Blocker agent', blockerStartedAt, 'completed')
        }).catch(requestError => {
          setBlockerRunStatus('failed')
          recordAgentDuration('blockers', blockerStartedAt)
          failures.push(`Blocker agent failed: ${agentFailureMessage(requestError)}`)
          logAgentTiming('Blocker agent', blockerStartedAt, 'failed')
        }),
      ])
      if (failures.length) setError(failures.join(' '))
    } finally {
      console.info(`[Project Intelligence] Run All completed in ${(performance.now() - runStartedAt).toFixed(0)} ms`)
      setLoading(false)
      setRunningAll(false)
    }
  }

  const toggleDocument = (id: string) => {
    clearAgentResults()
    setError(null)
    setSelectedIds(previous => previous.includes(id) ? previous.filter(item => item !== id) : [...previous, id])
  }

  const openScopeFinding = (finding: ScopeFinding) => setSelectedFinding({
    agent: 'Scope and Deliverables Agent',
    category: finding.category,
    title: finding.title,
    description: finding.description,
    related: [finding.related_milestone, finding.related_task, finding.related_deliverable].filter((value): value is string => Boolean(value)),
    recommendations: [],
    evidence: finding.evidence,
    verification: finding.evidence.some(item => item.chunk_id) ? 'Source verified against an indexed chunk' : 'Source-linked; chunk reference unavailable',
  })

  const openRiskFinding = (finding: RiskFinding) => setSelectedFinding({
    agent: 'Risk Detection and Delivery Forecasting Agent',
    category: 'Risk',
    title: finding.title,
    description: finding.description,
    severity: finding.level,
    likelihood: finding.likelihood,
    owner: finding.owner,
    status: finding.inference ? 'Inferred' : 'Evidence-backed',
    related: finding.affected_milestones,
    recommendations: finding.recommendations,
    evidence: finding.evidence,
    verification: finding.inference ? 'Inference' : finding.evidence.some(item => item.chunk_id) ? 'Source verified against an indexed chunk' : 'Source-linked; chunk reference unavailable',
  })

  const openBlockerFinding = (category: string, finding: BlockerItem) => setSelectedFinding({
    agent: 'Blocker and Action Item Identification Agent',
    category,
    title: category,
    description: finding.description,
    priority: finding.priority,
    status: finding.status,
    assignee: finding.assignee,
    dueDate: finding.due_date,
    related: [finding.related_milestone, finding.related_task].filter((value): value is string => Boolean(value)),
    recommendations: finding.recommendation ? [finding.recommendation] : [],
    evidence: finding.evidence,
    verification: finding.evidence.some(item => item.chunk_id) ? 'Source verified against an indexed chunk' : 'Source-linked; chunk reference unavailable',
  })

  const openSource = (evidence: EvidenceReference) => {
    if (!evidence.document_id) return
    const chunkQuery = evidence.chunk_id ? `?chunk=${encodeURIComponent(evidence.chunk_id)}` : ''
    setSelectedFinding(null)
    navigate(`/documents/${encodeURIComponent(evidence.document_id)}${chunkQuery}`)
  }
  const filteredItems = (items: BlockerItem[]) => items.filter(item =>
    (!statusFilter || (item.status ?? '').toLowerCase() === statusFilter) &&
    (!priorityFilter || (item.priority ?? '').toLowerCase() === priorityFilter),
  )
  const renderBlockerGroup = (title: string, category: string, items: BlockerItem[]) => {
    const visibleItems = filteredItems(items)
    if (!visibleItems.length) return null
    return <section className="analysis-section" key={title}><h3>{title}</h3><div className="finding-list">{visibleItems.map(item => <article className="blocker-finding-card" key={item.id}>
      <button className="blocker-finding-main" onClick={() => openBlockerFinding(category, item)}><strong>{item.description}</strong><span>Owner: {item.assignee || 'Not specified in uploaded documents'}</span><span>Due date: {item.due_date || 'Not specified in uploaded documents'}</span><span>Status: {item.status || 'Not specified in uploaded documents'}</span><span>Priority: {item.priority || 'Not specified in uploaded documents'}</span>{item.recommendation && <span>Next step: {item.recommendation}</span>}</button>
      <EvidenceLinks evidence={item.evidence} onOpen={openSource} />
    </article>)}</div></section>
  }

  return <div className="project-intelligence-page">
    <div className="page-heading">
      <div>
        <div className="eyebrow">PROJECT INTELLIGENCE</div>
        <h1>{pageTitle}</h1>
        <p>Extract scope, identify delivery risks, and surface blockers using the indexed project documents and supporting evidence.</p>
      </div>
      <div className="heading-action">
        <button className="button button-primary" onClick={() => void loadDocuments()} disabled={sourcesLoading}>
          <RefreshCcw size={14} /> Refresh sources
        </button>
      </div>
    </div>

    {error && <div className="alert-box error-box">{error}</div>}

    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2><FileText size={16} /> Source documents</h2>
          <p>Select the indexed documents to include in each analysis run.</p>
        </div>
      </div>
      {sourcesLoading ? <div className="empty-state">Loading indexed document sources…</div> : documents.length === 0 ? <div className="empty-state">No indexed project documents are available yet. Upload one or more supported files to enable the agents.</div> : <div className="document-select-list">{documents.map(document => <label key={document.id} className="doc-select-item"><input type="checkbox" checked={selectedIds.includes(document.id)} disabled={loading} onChange={() => toggleDocument(document.id)} /> <span>{document.filename}</span> <small>{document.file_type.toUpperCase()}</small></label>)}</div>}
    </div>

    {activeView === 'all' && <section className="panel ai-agent-header">
      <div>
        <div className="eyebrow">DOCUMENT-DRIVEN ANALYSIS</div>
        <h2>AI Project Analysis Agents</h2>
        <p>{selectedIds.length} indexed document{selectedIds.length === 1 ? '' : 's'} selected for analysis</p>
      </div>
      <button className="button button-primary run-all-button" onClick={() => void runAllAgents()} disabled={runningAll || loading || sourcesLoading || selectedIds.length === 0}>
        {runningAll ? <><LoaderCircle size={15} className="spin" /> Analyzing documents…</> : <><Sparkles size={15} /> Run All Agents</>}
      </button>
    </section>}

    <nav className="analysis-tabs" aria-label="Project analysis views">
      {([
        ['all', 'All agents'],
        ['scope', 'Scope & Deliverables'],
        ['risks', 'Risk Intelligence'],
        ['blockers', 'Blockers & Actions'],
      ] as const).map(([view, label]) => <button key={view} className={`analysis-tab${activeView === view ? ' active' : ''}`} onClick={() => {
        const next = new URLSearchParams(searchParams)
        if (view === 'all') next.delete('view')
        else next.set('view', view)
        setSearchParams(next)
      }}>{label}</button>)}
    </nav>

    <div className={`agent-grid${activeView === 'all' ? '' : ' single-agent-grid'}`}>
      {(activeView === 'all' || activeView === 'scope') && <div className="panel agent-panel">
        <div className="panel-heading">
          <div>
            <h2><Target size={16} /> Scope &amp; Deliverable Extraction Agent</h2>
            <p>Extracts project goals, milestones, timelines, deliverables, and team responsibilities from uploaded project documents.</p>
          </div>
          <button className="button button-secondary compact-button" onClick={() => void runScopeAnalysis()} disabled={loading || sourcesLoading || selectedIds.length === 0}>
            {scopeRunStatus === 'running' ? <><LoaderCircle size={13} className="spin" /> Processing</> : 'Run Agent'}
          </button>
        </div>
        {!availableScope ? <div className="empty-state">No scope analysis has been produced yet.</div> : <div className="analysis-stack">
          <PopulatedResultList title="Project Goals" items={scope.goals} />
          <PopulatedResultList title="Objectives" items={scope.objectives} />
          <PopulatedResultList title="Project Scope" items={scope.scope} />
          <PopulatedResultList title="Milestones and Timeline" items={scope.milestones.map(item => item.description || item.name || item.date || null)} />
          <PopulatedResultList title="Project Deliverables" items={scope.deliverables} />
          <PopulatedResultList title="Team Responsibilities" items={scope.responsibilities.map(item => [item.task || null, item.owner ? `Owner: ${item.owner}` : null, item.status ? `Status: ${item.status}` : null].filter(Boolean).join(' · '))} />
          <PopulatedResultList title="Dependencies" items={scope.dependencies.map(item => item.description ?? null)} />
          <div className="analysis-section"><h3>Supporting Evidence and Source Documents</h3>{scope.findings.length ? <div className="source-finding-list">{scope.findings.map(item => <article className="source-finding" key={item.id}><button className="source-finding-heading" onClick={() => openScopeFinding(item)}><strong>{item.title}</strong><span>{item.description}</span></button><EvidenceLinks evidence={item.evidence} onOpen={openSource} /></article>)}</div> : <p>Source references are not available for the returned findings.</p>}</div>
        </div>}
        {scopeRunStatus !== 'idle' && <AgentAnalysisSummary status={scopeRunStatus} duration={agentDurations.scope} completedAt={agentCompletionTimes.scope} documentCount={selectedIds.length} findingCount={scope.findings.length} sourceDocuments={scope.source_documents} notes={scope.notes} />}
      </div>}

      {(activeView === 'all' || activeView === 'risks') && <div className="panel agent-panel">
        <div className="panel-heading">
          <div>
            <h2><ShieldAlert size={16} /> Risk Detection &amp; Delivery Forecasting Agent</h2>
            <p>Analyzes project information to identify potential risks, dependency gaps, schedule concerns, and delivery challenges.</p>
          </div>
          <button className="button button-secondary compact-button" onClick={() => void runRiskAnalysis()} disabled={loading || sourcesLoading || selectedIds.length === 0}>
            {riskRunStatus === 'running' ? <><LoaderCircle size={13} className="spin" /> Processing</> : 'Run Agent'}
          </button>
        </div>
        <div className="analysis-stack">
          <section className="risk-severity-summary" aria-label="Risk severity summary">
            <h3>Risk Severity Summary</h3>
            <div className="risk-summary-grid">{(['Critical', 'High', 'Medium', 'Low'] as const).map(level => <div className="risk-summary-card" key={level}><span className={`risk-level risk-${level.toLowerCase()}`}>{level}</span><strong>{risks.severity_counts[level] ?? 0}</strong></div>)}</div>
            {(risks.severity_counts.Unrated ?? 0) > 0 && <p className="unrated-summary">Unrated: <strong>{risks.severity_counts.Unrated}</strong> risk{risks.severity_counts.Unrated === 1 ? '' : 's'} have insufficient documented impact or likelihood for classification.</p>}
          </section>
          {risks.findings.length ? <div className="analysis-section"><h3>Risk Findings</h3><div className="risk-finding-list">{risks.findings.map(item => <article className="risk-finding-card" key={item.id}>
            <button className="risk-finding-main" onClick={() => openRiskFinding(item)}><span className="finding-row-heading"><strong>{item.title}</strong><span className={`risk-level risk-${item.level?.toLowerCase() ?? 'unknown'}`}>{item.level ?? 'Unrated'}</span></span>{item.category && <small>Category: {item.category}</small>}<span>{item.description}</span><div className="risk-finding-facts"><span>Impact: {item.impact || 'Not specified in uploaded documents'}</span><span>Likelihood: {item.likelihood || 'Not specified in uploaded documents'}</span>{item.owner && <span>Owner: {item.owner}</span>}{item.affected_milestones.length > 0 && <span>Related milestones: {item.affected_milestones.join(', ')}</span>}</div>{item.recommendations.length > 0 && <small>Mitigation: {item.recommendations.join(' · ')}</small>}</button><EvidenceLinks evidence={item.evidence} onOpen={openSource} />
          </article>)}</div></div> : <div className="empty-state">No specific risks were identified in the selected documents.</div>}
          {['Dependency gap', 'Schedule or delivery risk'].map(category => {
            const items = risks.findings.filter(item => item.category === category)
            return items.length ? <ResultList key={category} title={category === 'Dependency gap' ? 'Dependency Gaps' : 'Schedule Risks'} items={items.map(item => item.description)} /> : null
          })}
          <PopulatedResultList title="Delivery Challenges" items={risks.findings.filter(item => item.category !== 'Dependency gap' && item.category !== 'Schedule or delivery risk').map(item => item.description)} />
          <PopulatedResultList title="Delivery Forecast" items={risks.findings.map(item => item.forecast ?? null)} />
          <PopulatedResultList title="Recommended Mitigation Actions" items={risks.findings.flatMap(item => item.recommendations)} />
          {risks.missing_information.length > 0 && <div className="analysis-section"><h3>Additional information needed</h3><ul>{risks.missing_information.map((item, index) => <li key={`risk-missing-${index}`}>{item}</li>)}</ul></div>}
        </div>
        {riskRunStatus !== 'idle' && <AgentAnalysisSummary status={riskRunStatus} duration={agentDurations.risks} completedAt={agentCompletionTimes.risks} documentCount={selectedIds.length} findingCount={risks.findings.length} sourceDocuments={risks.source_documents} notes={risks.notes} />}
      </div>}

      {(activeView === 'all' || activeView === 'blockers') && <div className="panel agent-panel">
        <div className="panel-heading">
          <div>
            <h2><ListChecks size={16} /> Blocker &amp; Action Item Identification Agent</h2>
            <p>Identifies unresolved issues, pending decisions, project blockers, and action items requiring attention.</p>
          </div>
          <button className="button button-secondary compact-button" onClick={() => void runBlockerAnalysis()} disabled={loading || sourcesLoading || selectedIds.length === 0}>
            {blockerRunStatus === 'running' ? <><LoaderCircle size={13} className="spin" /> Processing</> : 'Run Agent'}
          </button>
        </div>
        {blockerRunStatus === 'idle' ? <div className="empty-state">Run the agent to analyze the selected indexed documents.</div>
          : blockerRunStatus === 'running' ? <div className="empty-state">Analyzing selected indexed documents…</div>
          : blockerRunStatus === 'failed' ? <div className="empty-state" role="status">Analysis did not complete. Review the error above and try again.</div>
          : <div className="analysis-stack">
          {!hasBlockerFindings && <div className="empty-state" role="status">No relevant information was found in the uploaded documents.</div>}
          {hasBlockerFindings && <>
            {renderBlockerGroup('Project Blockers', 'Blocker', blockers.blockers)}
            {renderBlockerGroup('Unresolved Issues', 'Unresolved issue', blockers.unresolved_issues)}
            {renderBlockerGroup('Pending Decisions', 'Pending decision', blockers.pending_decisions)}
            {renderBlockerGroup('Action Items', 'Action item', blockers.action_items)}
            {[...blockers.blockers, ...blockers.unresolved_issues, ...blockers.pending_decisions, ...blockers.action_items].some(item => item.recommendation) && <ResultList title="Recommended Next Steps" items={[...blockers.blockers, ...blockers.unresolved_issues, ...blockers.pending_decisions, ...blockers.action_items].map(item => item.recommendation ?? null)} />}
            <div className="analysis-section"><h3>Supporting Evidence and Source Documents</h3>{blockers.source_documents.length ? <ul>{blockers.source_documents.map(filename => <li key={filename}>{filename}</li>)}</ul> : <p>Source documents are not available.</p>}</div>
            <div className="filter-bar blocker-filter-bar"><label>Status<select value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="">All statuses</option>{[...new Set([...blockers.blockers, ...blockers.unresolved_issues, ...blockers.pending_decisions, ...blockers.action_items].map(item => item.status?.toLowerCase()).filter((value): value is string => Boolean(value)))].map(status => <option value={status} key={status}>{status}</option>)}</select></label><label>Priority<select value={priorityFilter} onChange={event => setPriorityFilter(event.target.value)}><option value="">All priorities</option>{[...new Set([...blockers.blockers, ...blockers.unresolved_issues, ...blockers.pending_decisions, ...blockers.action_items].map(item => item.priority?.toLowerCase()).filter((value): value is string => Boolean(value)))].map(priority => <option value={priority} key={priority}>{priority}</option>)}</select></label></div>
            {filteredItems([...blockers.blockers, ...blockers.unresolved_issues, ...blockers.pending_decisions, ...blockers.action_items]).length === 0 && <div className="empty-state">No issues match the selected status and priority filters.</div>}
          </>}
          {blockers.missing_information.length > 0 && <div className="analysis-section"><h3>Missing information</h3><ul>{blockers.missing_information.map((item, index) => <li key={`blocker-missing-${index}`}>{item}</li>)}</ul></div>}
        </div>}
        {blockerRunStatus !== 'idle' && <AgentAnalysisSummary status={blockerRunStatus} duration={agentDurations.blockers} completedAt={agentCompletionTimes.blockers} documentCount={selectedIds.length} findingCount={blockers.blockers.length + blockers.unresolved_issues.length + blockers.pending_decisions.length + blockers.action_items.length} sourceDocuments={blockers.source_documents} notes={blockers.notes} />}
      </div>}
    </div>

    {activeView === 'all' && <section className="panel agent-workflow-panel">
      <div className="panel-heading">
        <div>
          <h2>How the three agents work together</h2>
          <p>Uploaded evidence flows through one processing pipeline before structured intelligence is presented.</p>
        </div>
      </div>
      <div className="agent-workflow" aria-label="Agent processing workflow">
        <div className="workflow-step"><span className="workflow-icon"><Upload size={18} /></span><small>1. Upload</small><strong>Uploaded Project Documents</strong></div>
        <ArrowRight className="workflow-arrow" size={18} aria-hidden="true" />
        <div className="workflow-step"><span className="workflow-icon"><FileText size={18} /></span><small>2. Process</small><strong>Information Extraction</strong></div>
        <ArrowRight className="workflow-arrow" size={18} aria-hidden="true" />
        <div className="workflow-step workflow-agents"><span className="workflow-icon"><Sparkles size={18} /></span><small>3. Analyze</small><strong>Three Specialized AI Agents</strong></div>
        <ArrowRight className="workflow-arrow" size={18} aria-hidden="true" />
        <div className="workflow-step"><span className="workflow-icon"><CheckCircle2 size={18} /></span><small>4. Present</small><strong>Structured Project Intelligence</strong></div>
      </div>
      <p className="workflow-explanation">The document pipeline extracts and organizes relevant information, the three agents analyze it for scope, delivery risks, and blockers, and the platform presents structured scope, risks, blockers, and action items.</p>
    </section>}

    {activeView === 'all' && <div className="panel">
      <div className="panel-heading">
        <div>
          <h2><Sparkles size={16} /> Operational notes</h2>
          <p>Evidence-grounded results and findings are generated from the uploaded project documents. If information is missing, the result will clearly indicate it.</p>
        </div>
      </div>
      <div className="usage-row"><span><strong>Selected documents:</strong> {selectedIds.length || 0}</span><span><strong>Scope status:</strong> {availableScope ? 'Available' : 'Not run'}</span><span><strong>Risk status:</strong> {availableRisks ? 'Available' : 'Not run'}</span><span><strong>Blocker status:</strong> {availableBlockers ? 'Available' : 'Not run'}</span></div>
    </div>}
    {selectedFinding && <div className="finding-detail-backdrop" onMouseDown={() => setSelectedFinding(null)}>
      <aside className="finding-detail-panel" role="dialog" aria-modal="true" aria-labelledby="finding-detail-title" onMouseDown={event => event.stopPropagation()}>
        <div className="finding-detail-header">
          <div><div className="eyebrow">{selectedFinding.agent}</div><h2 id="finding-detail-title">{selectedFinding.title}</h2></div>
          <button className="button button-quiet compact-button" aria-label="Close finding details" onClick={() => setSelectedFinding(null)}><X size={16} /></button>
        </div>
        <div className="finding-detail-content">
          <div className="finding-meta-row"><span className="finding-category">{selectedFinding.category}</span>
            {selectedFinding.severity && <span className={`risk-level risk-${selectedFinding.severity.toLowerCase()}`}>{selectedFinding.severity}</span>}
            {selectedFinding.likelihood && <span className="finding-category">Likelihood: {selectedFinding.likelihood}</span>}
            {selectedFinding.priority && <span className="finding-category">Priority: {selectedFinding.priority}</span>}
            {selectedFinding.status && <span className="finding-category">Status: {selectedFinding.status}</span>}
          </div>
          <section className="detail-section"><h3>Description</h3><p>{selectedFinding.description || 'Description is unavailable.'}</p></section>
          {selectedFinding.related.length > 0 && <section className="detail-section"><h3>Related project work</h3><ul>{selectedFinding.related.map((item, index) => <li key={`related-${index}`}>{item}</li>)}</ul></section>}
          {(selectedFinding.assignee || selectedFinding.owner || selectedFinding.dueDate) && <section className="detail-section"><h3>Assignment and timing</h3><p>{[selectedFinding.assignee ? `Assignee: ${selectedFinding.assignee}` : null, selectedFinding.owner ? `Owner: ${selectedFinding.owner}` : null, selectedFinding.dueDate ? `Due: ${selectedFinding.dueDate}` : null].filter(Boolean).join(' · ')}</p></section>}
          {selectedFinding.recommendations.length > 0 && <section className="detail-section"><h3>Recommended action</h3><ul>{selectedFinding.recommendations.map((item, index) => <li key={`recommendation-${index}`}>{item}</li>)}</ul></section>}
          <section className="detail-section"><h3>Verification</h3><p>{selectedFinding.verification}</p></section>
          <section className="detail-section"><h3>Document evidence</h3>
            {selectedFinding.evidence.length === 0 ? <div className="empty-state">Evidence excerpt is unavailable for this finding.</div> : <div className="evidence-list">{selectedFinding.evidence.map((evidence, index) => <article className="evidence-card" key={`${evidence.document_id ?? evidence.filename ?? 'source'}-${evidence.chunk_id ?? index}`}>
              <div className="evidence-source"><FileText size={15} /><strong>{evidence.filename || 'Source filename unavailable'}</strong></div>
              <div className="evidence-location">{evidence.chunk_id && <span>Chunk {evidence.chunk_id}</span>}{Object.entries(evidence.location).map(([key, value]) => <span key={key}>{key.replaceAll('_', ' ')}: {value}</span>)}</div>
              {evidence.excerpt ? <blockquote><mark>{evidence.excerpt}</mark></blockquote> : <p className="evidence-unavailable">Evidence excerpt unavailable.</p>}
              {evidence.document_id ? <button className="button button-secondary compact-button" onClick={() => openSource(evidence)}><ArrowUpRight size={14} /> Open source document</button> : <span className="muted-text">Document navigation is unavailable because no source ID was returned.</span>}
            </article>)}</div>}
          </section>
        </div>
        <div className="finding-detail-footer"><button className="button button-quiet" onClick={() => setSelectedFinding(null)}>Back to findings</button></div>
      </aside>
    </div>}
  </div>
}
