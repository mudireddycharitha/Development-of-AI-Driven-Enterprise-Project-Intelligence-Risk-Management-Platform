import { useEffect, useMemo, useState } from 'react'
import { ArrowUpRight, FileText, ListChecks, RefreshCcw, ShieldAlert, Sparkles, Target, X } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { BlockerAnalysis, BlockerItem, DocumentRecord, EvidenceReference, RiskAnalysis, RiskFinding, ScopeAnalysis, ScopeFinding } from '../types'

const emptyScope: ScopeAnalysis = {
  goals: [], objectives: [], deliverables: [], milestones: [], responsibilities: [], dependencies: [], progress: [], findings: [], missing_information: [], source_documents: [], notes: [],
}

const emptyRisks: RiskAnalysis = { findings: [], missing_information: [], source_documents: [], rules_used: [] }
const emptyBlockers: BlockerAnalysis = { blockers: [], pending_decisions: [], action_items: [], missing_information: [], source_documents: [], notes: [] }

interface FindingDetail {
  agent: string
  category: string
  title: string
  description: string
  severity?: string | null
  priority?: string | null
  status?: string | null
  assignee?: string | null
  dueDate?: string | null
  related: string[]
  recommendations: string[]
  evidence: EvidenceReference[]
  verification: string
}

export function ProjectIntelligence() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedView = searchParams.get('view')
  const activeView = requestedView === 'scope' || requestedView === 'risks' || requestedView === 'blockers' ? requestedView : 'all'
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [scope, setScope] = useState<ScopeAnalysis>(emptyScope)
  const [risks, setRisks] = useState<RiskAnalysis>(emptyRisks)
  const [blockers, setBlockers] = useState<BlockerAnalysis>(emptyBlockers)
  const [loading, setLoading] = useState(false)
  const [blockerRunStatus, setBlockerRunStatus] = useState<'idle' | 'running' | 'succeeded' | 'failed'>('idle')
  const [sourcesLoading, setSourcesLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedFinding, setSelectedFinding] = useState<FindingDetail | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [priorityFilter, setPriorityFilter] = useState('')

  const loadDocuments = async () => {
    setSourcesLoading(true)
    setError(null)
    try {
      const page = await apiService.documents({ page: 1, page_size: 100 })
      const items = page.items.filter(item => item.processing_status === 'indexed')
      setDocuments(items)
      setSelectedIds(items.map(item => item.id))
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

  const availableScope = useMemo(() => scope.findings.length > 0 || scope.goals.length > 0 || scope.deliverables.length > 0, [scope])
  const availableRisks = useMemo(() => risks.findings.length > 0 || risks.missing_information.length > 0, [risks])
  const availableBlockers = useMemo(() => blockers.blockers.length || blockers.pending_decisions.length || blockers.action_items.length || blockers.missing_information.length, [blockers])
  const hasBlockerFindings = blockers.blockers.length > 0 || blockers.pending_decisions.length > 0 || blockers.action_items.length > 0

  const runScopeAnalysis = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await apiService.scopeAnalysis(selectedIds.length ? selectedIds : undefined)
      setScope(result)
    } catch (fetchError) {
      setError(errorMessage(fetchError))
    } finally {
      setLoading(false)
    }
  }

  const runRiskAnalysis = async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await apiService.riskAnalysis(selectedIds.length ? selectedIds : undefined)
      setRisks(result)
    } catch (fetchError) {
      setError(errorMessage(fetchError))
    } finally {
      setLoading(false)
    }
  }

  const runBlockerAnalysis = async () => {
    setLoading(true)
    setError(null)
    setBlockerRunStatus('running')
    setBlockers(emptyBlockers)
    try {
      const result = await apiService.blockerAnalysis(selectedIds.length ? selectedIds : undefined)
      setBlockers(result)
      setBlockerRunStatus('succeeded')
    } catch (fetchError) {
      setBlockerRunStatus('failed')
      setError(`Blocker analysis failed: ${errorMessage(fetchError)}`)
    } finally {
      setLoading(false)
    }
  }

  const toggleDocument = (id: string) => {
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
    (!statusFilter || item.status.toLowerCase() === statusFilter) &&
    (!priorityFilter || (item.priority ?? '').toLowerCase() === priorityFilter),
  )

  return <div className="project-intelligence-page">
    <div className="page-heading">
      <div>
        <div className="eyebrow">MILESTONE 2</div>
        <h1>Project intelligence agents</h1>
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
      {sourcesLoading ? <div className="empty-state">Loading indexed document sources…</div> : documents.length === 0 ? <div className="empty-state">No indexed project documents are available yet. Upload one or more supported files to enable the agents.</div> : <div className="document-select-list">{documents.map(document => <label key={document.id} className="doc-select-item"><input type="checkbox" checked={selectedIds.includes(document.id)} onChange={() => toggleDocument(document.id)} /> <span>{document.filename}</span> <small>{document.file_type.toUpperCase()}</small></label>)}</div>}
    </div>

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
            <h2><Target size={16} /> Scope and deliverables</h2>
            <p>Goals, milestones, responsibilities, and source-linked dependencies.</p>
          </div>
          <button className="button button-secondary compact-button" onClick={() => void runScopeAnalysis()} disabled={loading || sourcesLoading || selectedIds.length === 0}>
            {loading ? 'Running...' : 'Run agent'}
          </button>
        </div>
        {!availableScope ? <div className="empty-state">No scope analysis has been produced yet.</div> : <div className="analysis-stack">
          <div className="analysis-section"><h3>Scope findings</h3>{scope.findings.length ? <div className="finding-list">{scope.findings.map(item => <button key={item.id} className="finding-row" onClick={() => openScopeFinding(item)}><span className="finding-row-heading"><strong>{item.title}</strong><span className="finding-category">{item.category}</span></span><span>{item.description}</span><small>{item.evidence.length ? `${item.evidence.length} source reference${item.evidence.length === 1 ? '' : 's'}` : 'Source evidence unavailable'}</small></button>)}</div> : <p>Structured finding details are unavailable for this analysis result.</p>}</div>
          {scope.missing_information.length > 0 && <div className="analysis-section"><h3>Missing information</h3><ul>{scope.missing_information.map((item, index) => <li key={`scope-missing-${index}`}>{item}</li>)}</ul></div>}
          {scope.source_documents.length > 0 && <div className="analysis-section"><h3>Source documents</h3><ul>{scope.source_documents.map((name, index) => <li key={`source-doc-${index}`}>{name}</li>)}</ul></div>}
        </div>}
      </div>}

      {(activeView === 'all' || activeView === 'risks') && <div className="panel agent-panel">
        <div className="panel-heading">
          <div>
            <h2><ShieldAlert size={16} /> Risk detection and forecast</h2>
            <p>Schedule risks, dependency gaps, delivery challenges, and documented evidence.</p>
          </div>
          <button className="button button-secondary compact-button" onClick={() => void runRiskAnalysis()} disabled={loading || sourcesLoading || selectedIds.length === 0}>
            {loading ? 'Running...' : 'Run agent'}
          </button>
        </div>
        {!availableRisks ? <div className="empty-state">No risk findings are available yet.</div> : <div className="analysis-stack">
          {risks.findings.length > 0 && <div className="risk-summary-grid">{(['Critical', 'High', 'Medium', 'Low'] as const).map(level => <div className="risk-summary-card" key={level}><span className={`risk-level risk-${level.toLowerCase()}`}>{level}</span><strong>{risks.findings.filter(item => item.level === level).length}</strong></div>)}</div>}
          {risks.findings.length ? <div className="finding-list">{risks.findings.map(item => <button key={item.id} className="finding-row risk-finding-row" onClick={() => openRiskFinding(item)}><span className="finding-row-heading"><strong>{item.title}</strong><span className={`risk-level risk-${item.level.toLowerCase()}`}>{item.level}</span></span><span>{item.description}</span>{item.affected_milestones.length > 0 && <small>Affected: {item.affected_milestones.join(', ')}</small>}<small>{item.evidence.length ? `${item.evidence.length} source reference${item.evidence.length === 1 ? '' : 's'}` : 'Source evidence unavailable'}</small></button>)}</div> : <p>No specific risks were identified.</p>}
          {risks.missing_information.length > 0 && <div className="analysis-section"><h3>Missing information</h3><ul>{risks.missing_information.map((item, index) => <li key={`risk-missing-${index}`}>{item}</li>)}</ul></div>}
          {risks.rules_used.length > 0 && <div className="analysis-section"><h3>Rules used</h3><ul>{risks.rules_used.map((item, index) => <li key={`rule-${index}`}>{item}</li>)}</ul></div>}
        </div>}
      </div>}

      {(activeView === 'all' || activeView === 'blockers') && <div className="panel agent-panel">
        <div className="panel-heading">
          <div>
            <h2><ListChecks size={16} /> Blockers and action items</h2>
            <p>Pending decisions, blockers, and action items with owners and dates when identified.</p>
          </div>
          <button className="button button-secondary compact-button" onClick={() => void runBlockerAnalysis()} disabled={loading || sourcesLoading || selectedIds.length === 0}>
            {loading ? 'Running...' : 'Run agent'}
          </button>
        </div>
        {blockerRunStatus === 'idle' ? <div className="empty-state">Run the agent to analyze the selected indexed documents.</div>
          : blockerRunStatus === 'running' ? <div className="empty-state">Analyzing selected indexed documents…</div>
          : blockerRunStatus === 'failed' ? <div className="empty-state" role="status">Analysis did not complete. Review the error above and try again.</div>
          : <div className="analysis-stack">
          {!hasBlockerFindings && <div className="empty-state" role="status">Analysis completed successfully. No blocker, pending decision, or action-item findings were identified in the selected indexed documents.</div>}
          {hasBlockerFindings && <>
            <div className="filter-bar blocker-filter-bar"><label>Status<select value={statusFilter} onChange={event => setStatusFilter(event.target.value)}><option value="">All statuses</option>{[...new Set([...blockers.blockers, ...blockers.pending_decisions, ...blockers.action_items].map(item => item.status.toLowerCase()))].map(status => <option value={status} key={status}>{status}</option>)}</select></label><label>Priority<select value={priorityFilter} onChange={event => setPriorityFilter(event.target.value)}><option value="">All priorities</option>{[...new Set([...blockers.blockers, ...blockers.pending_decisions, ...blockers.action_items].map(item => item.priority?.toLowerCase()).filter((value): value is string => Boolean(value)))].map(priority => <option value={priority} key={priority}>{priority}</option>)}</select></label></div>
            {filteredItems(blockers.blockers).length > 0 && <div className="analysis-section"><h3>Blockers</h3><div className="finding-list">{filteredItems(blockers.blockers).map(item => <button key={item.id} className="finding-row" onClick={() => openBlockerFinding('Blocker', item)}><span>{item.description}</span><small>{[item.assignee ? `Owner: ${item.assignee}` : null, item.due_date ? `Due: ${item.due_date}` : null, item.priority ? `Priority: ${item.priority}` : null, item.status].filter(Boolean).join(' · ') || (item.evidence.length ? 'Open details and evidence' : 'Source evidence unavailable')}</small></button>)}</div></div>}
            {filteredItems(blockers.pending_decisions).length > 0 && <div className="analysis-section"><h3>Pending decisions</h3><div className="finding-list">{filteredItems(blockers.pending_decisions).map(item => <button key={item.id} className="finding-row" onClick={() => openBlockerFinding('Pending decision', item)}><span>{item.description}</span><small>{[item.assignee ? `Assignee: ${item.assignee}` : null, item.due_date ? `Due: ${item.due_date}` : null, item.priority ? `Priority: ${item.priority}` : null, item.status].filter(Boolean).join(' · ') || (item.evidence.length ? 'Open details and evidence' : 'Source evidence unavailable')}</small></button>)}</div></div>}
            {filteredItems(blockers.action_items).length > 0 && <div className="analysis-section"><h3>Action items</h3><div className="finding-list">{filteredItems(blockers.action_items).map(item => <button key={item.id} className="finding-row" onClick={() => openBlockerFinding('Action item', item)}><span>{item.description}</span><small>{[item.assignee, item.due_date ? `Due: ${item.due_date}` : null, item.priority ? `Priority: ${item.priority}` : null, item.status].filter(Boolean).join(' · ') || (item.evidence.length ? 'Open details and evidence' : 'Source evidence unavailable')}</small></button>)}</div></div>}
            {filteredItems([...blockers.blockers, ...blockers.pending_decisions, ...blockers.action_items]).length === 0 && <div className="empty-state">No issues match the selected status and priority filters.</div>}
          </>}
          {blockers.missing_information.length > 0 && <div className="analysis-section"><h3>Missing information</h3><ul>{blockers.missing_information.map((item, index) => <li key={`blocker-missing-${index}`}>{item}</li>)}</ul></div>}
        </div>}
      </div>}
    </div>

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
            {selectedFinding.priority && <span className="finding-category">Priority: {selectedFinding.priority}</span>}
            {selectedFinding.status && <span className="finding-category">Status: {selectedFinding.status}</span>}
          </div>
          <section className="detail-section"><h3>Description</h3><p>{selectedFinding.description || 'Description is unavailable.'}</p></section>
          {selectedFinding.related.length > 0 && <section className="detail-section"><h3>Related project work</h3><ul>{selectedFinding.related.map((item, index) => <li key={`related-${index}`}>{item}</li>)}</ul></section>}
          {(selectedFinding.assignee || selectedFinding.dueDate) && <section className="detail-section"><h3>Assignment and timing</h3><p>{[selectedFinding.assignee ? `Assignee: ${selectedFinding.assignee}` : null, selectedFinding.dueDate ? `Due: ${selectedFinding.dueDate}` : null].filter(Boolean).join(' · ')}</p></section>}
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
