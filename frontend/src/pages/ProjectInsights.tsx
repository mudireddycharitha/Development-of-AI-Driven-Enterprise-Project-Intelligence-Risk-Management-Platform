import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, AlertTriangle, CheckCircle2, Clock3, RefreshCw, ShieldAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import { EvidenceReferenceCard } from '../components/Milestone3Shared'
import { EmptyState, ErrorState, Loading, PageHeading } from '../components/Shared'
import { apiService, errorMessage } from '../services/api'
import type { ProjectInsightsResult } from '../types'

const riskLevels = ['Critical', 'High', 'Medium', 'Low', 'Unrated'] as const
const milestoneStatuses = [
  ['completed', 'Completed', 'milestone-completed'],
  ['in_progress', 'In progress', 'milestone-progress'],
  ['delayed', 'Delayed', 'milestone-delayed'],
  ['blocked', 'Blocked', 'milestone-blocked'],
  ['at_risk', 'At risk', 'milestone-at-risk'],
  ['pending', 'Pending', 'milestone-pending'],
  ['not_stated', 'Not stated', 'milestone-not-stated'],
] as const

export function ProjectInsights() {
  const [data, setData] = useState<ProjectInsightsResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const requestInFlight = useRef(false)

  const load = useCallback(async (refresh = false) => {
    if (requestInFlight.current) return
    requestInFlight.current = true
    setError('')
    refresh ? setRefreshing(true) : setLoading(true)
    try {
      setData(await apiService.projectInsights())
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      requestInFlight.current = false
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const refresh = () => { if (!document.hidden) void load(true) }
    const interval = window.setInterval(refresh, 30_000)
    window.addEventListener('focus', refresh)
    return () => {
      window.clearInterval(interval)
      window.removeEventListener('focus', refresh)
    }
  }, [load])

  const health = data?.health
  const score = health?.overall_score ?? null
  const gaugeTone = health?.classification === 'Healthy' ? 'healthy'
    : health?.classification === 'Needs Attention' ? 'attention'
      : health?.classification === 'At Risk' ? 'at-risk' : 'unknown'
  const timelineHealth = health?.dimensions.timeline_health.score ?? null
  const timelineRisk = timelineHealth === null ? null : 100 - timelineHealth

  return <div className="insights-page">
    <PageHeading eyebrow="ANALYTICS & INSIGHTS" title="Project Insights"
      description="Current health, evidence-backed risks, actions, and scope from the existing project intelligence agents."
      action={<button className="button button-quiet" onClick={() => void load(true)} disabled={refreshing}>
        <RefreshCw size={15} className={refreshing ? 'spin' : ''} /> {refreshing ? 'Refreshing…' : 'Refresh insights'}
      </button>} />
    {loading && <Loading label="Loading the latest project insights…" />}
    {error && <ErrorState message={error} />}
    {data && <>
      <section className="insights-health-hero panel">
        <div className={`m3-health-gauge ${gaugeTone}`}
          style={{ backgroundImage: score === null ? undefined : `conic-gradient(var(--gauge-color) ${score}%, var(--surface-elevated) 0)` }}>
          <div className="m3-health-gauge-center"><strong>{score === null ? '—' : Math.round(score)}</strong>
            <small>{score === null ? 'No score' : 'out of 100'}</small></div>
        </div>
        <div className="insights-health-summary">
          <div className="eyebrow">OVERALL PROJECT HEALTH</div>
          <h2>{data.project_status}</h2>
          <p>{score === null
            ? 'There is not enough evidence across all health dimensions to calculate an overall score.'
            : 'Health score from the existing Project Health Scoring Module. No separate scoring algorithm is used.'}</p>
          <div className="m3-health-meta"><span><Activity size={14} /> {health?.document_count ?? 0} current indexed document(s)</span>
            <span>Updated {new Date(data.generated_at).toLocaleString()}</span></div>
        </div>
      </section>

      <section className="insights-metric-grid" aria-label="Project metrics">
        <InsightMetric title="Documents uploaded" value={data.metrics.total_uploaded_documents}
          detail="Includes retained document versions" />
        <InsightMetric title="Documents processed" value={data.metrics.processed_documents}
          detail={`${data.metrics.current_indexed_documents} current indexed sources`} />
        <InsightMetric title="Identified risks" value={data.metrics.total_risks}
          detail="Current Risk Intelligence Agent findings" />
        <InsightMetric title="Total blockers" value={data.metrics.total_blockers}
          detail={`${data.blocker_counts.unresolved} unresolved`} />
        <InsightMetric title="Action items" value={data.metrics.total_actions}
          detail={`${data.action_counts.pending + data.action_counts.overdue} pending or overdue`} />
        <InsightMetric title="Deliverables" value={data.metrics.total_deliverables}
          detail="Extracted by Scope & Deliverables Agent" />
        <InsightMetric title="Generated documentation"
          value={data.documentation.is_current ? data.documentation.total_generated_items : 'Not generated'}
          detail="Latest generation for current indexed sources" />
      </section>

      <section className="insights-dimension-grid" aria-label="Project health dimensions">
        <Dimension title="Scope clarity" value={health?.dimensions.scope_clarity.score ?? null}
          detail={health?.dimensions.scope_clarity.explanation ?? 'Unavailable'} />
        <Dimension title="Timeline risk" value={timelineRisk}
          detail={timelineRisk === null ? health?.dimensions.timeline_health.explanation ?? 'Unavailable'
            : `Inverted from the existing timeline health score (${Math.round(timelineHealth ?? 0)} / 100).`} />
        <Dimension title="Blocker health" value={health?.dimensions.blocker_health.score ?? null}
          detail={`${health?.unresolved_blockers.length ?? 0} unresolved blocker(s); ${health?.pending_decisions.length ?? 0} pending decision(s).`} />
      </section>

      <section className="insights-summary-grid">
        <div className="panel insights-chart-panel">
          <div className="panel-heading"><div><h2><ShieldAlert size={16} /> Risk severity</h2><p>{data.risks.length} findings from the Risk Intelligence Agent</p></div></div>
          {data.risks.length === 0
            ? <EmptyState title="No risk findings" text="Risk severity distribution will appear when indexed documents contain risk evidence." />
            : <div className="insights-bars" role="img" aria-label="Risk counts by severity">
              {riskLevels.map(level => <CountBar key={level} label={level} count={data.risk_counts[level]}
                total={data.metrics.total_risks} tone={`risk-${level.toLowerCase()}`} />)}
            </div>}
        </div>
        <div className="panel insights-chart-panel">
          <div className="panel-heading"><div><h2><CheckCircle2 size={16} /> Action item status</h2><p>{data.action_counts.total} actions from Blockers &amp; Actions</p></div></div>
          {data.action_counts.total === 0
            ? <EmptyState title="No action items" text="Action status distribution will appear when the agent finds action items." />
            : <div className="insights-bars" role="img" aria-label="Action item counts by status">
              <CountBar label="Pending" count={data.action_counts.pending} total={data.action_counts.total} tone="action-pending" />
              <CountBar label="Overdue" count={data.action_counts.overdue} total={data.action_counts.total} tone="action-overdue" />
              <CountBar label="Completed" count={data.action_counts.completed} total={data.action_counts.total} tone="action-completed" />
            </div>}
        </div>
        <div className="panel insights-chart-panel">
          <div className="panel-heading"><div><h2><AlertTriangle size={16} /> Blocker status</h2>
            <p>{data.blocker_counts.total} blocker findings and {data.blocker_counts.pending_decisions} pending decisions</p></div></div>
          {data.blocker_counts.total + data.blocker_counts.pending_decisions === 0
            ? <EmptyState title="No blockers or decisions" text="Blocker distribution will appear when the agent finds supported evidence." />
            : <div className="insights-bars" role="img" aria-label="Blocker and decision counts">
              <CountBar label="Unresolved" count={data.blocker_counts.unresolved}
                total={data.blocker_counts.total + data.blocker_counts.pending_decisions} tone="blocker-unresolved" />
              <CountBar label="Resolved" count={data.blocker_counts.resolved}
                total={data.blocker_counts.total + data.blocker_counts.pending_decisions} tone="blocker-resolved" />
              <CountBar label="Pending decisions" count={data.blocker_counts.pending_decisions}
                total={data.blocker_counts.total + data.blocker_counts.pending_decisions} tone="blocker-decisions" />
            </div>}
        </div>
        <div className="panel insights-chart-panel">
          <div className="panel-heading"><div><h2><Clock3 size={16} /> Milestone status</h2>
            <p>{data.milestone_counts.total} milestones from Scope &amp; Deliverables</p></div></div>
          {data.milestone_counts.total === 0
            ? <EmptyState title="No milestones identified" text="Milestone status distribution will appear when project documents contain milestone evidence." />
            : <div className="insights-bars" role="img" aria-label="Project milestone status distribution">
              {milestoneStatuses.map(([key, label, tone]) => <CountBar key={key} label={label}
                count={data.milestone_counts[key]} total={data.milestone_counts.total} tone={tone} />)}
            </div>}
        </div>
      </section>

      <section className="insights-summary-grid">
        <section className="panel">
          <div className="panel-heading"><div><h2>Blockers identified</h2>
            <p>Findings from the Blockers &amp; Actions Agent with source references where available.</p></div></div>
          {!data.blockers.length ? <EmptyState title="No blocker findings" text="The agent did not identify blockers in the current indexed sources." />
            : <div className="insights-finding-list">{data.blockers.map((blocker, index) => <article className="risk-card" key={`${blocker.id}-${index}`}>
              <div className="insights-risk-topline"><span className="insights-category">{blocker.status}</span>
                {blocker.assignee && <span className="insights-category">Owner: {blocker.assignee}</span>}</div>
              <p>{blocker.description}</p>
              {blocker.evidence[0] && <EvidenceReferenceCard evidence={blocker.evidence[0]} />}
            </article>)}</div>}
        </section>
        <section className="panel">
          <div className="panel-heading"><div><h2>Documentation Generation Agent</h2>
            <p>Saved outputs for the complete current indexed document set.</p></div>
            <Link className="text-link" to="/documentation">Open generation</Link>
          </div>
          {!data.documentation.is_current
            ? <EmptyState title="No current generation" text="Generate documentation for the current indexed sources to show stories, register entries, actions, and decisions here." />
            : <>
              <p className="insights-documentation-meta">Generated {new Date(data.documentation.generated_at ?? data.generated_at).toLocaleString()} from {data.documentation.source_documents.length} source document(s).</p>
              <div className="insights-documentation-counts">
                <InsightMetric title="User stories" value={data.documentation.counts.user_stories} detail="Evidence-linked" />
                <InsightMetric title="Risk register" value={data.documentation.counts.risk_register} detail="Generated entries" />
                <InsightMetric title="Action items" value={data.documentation.counts.action_items} detail="Structured actions" />
                <InsightMetric title="Pending decisions" value={data.documentation.counts.pending_decisions} detail="Evidence-linked" />
              </div>
            </>}
        </section>
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2><AlertTriangle size={16} /> Extracted project risks</h2>
          <p>Source references and mitigation recommendations are shown when available.</p></div></div>
        {!data.risks.length ? <EmptyState title="No risk findings available" text="Risk information will appear after indexed project documents contain relevant evidence." />
          : <div className="insights-finding-list">{data.risks.map(risk => <article className="risk-card" key={risk.id}>
            <div className="insights-risk-topline"><span className={`risk-level risk-${risk.level?.toLowerCase() ?? 'unknown'}`}>{risk.level ?? 'Unrated'}</span>
              <span className="insights-category">{risk.category}</span></div>
            <h3>{risk.title}</h3><p>{risk.description}</p>
            <div className="insights-risk-meta"><span>Probability: {risk.probability ?? 'Not stated'}</span><span>Impact: {risk.impact ?? 'Not stated'}</span>
              {risk.source_document && <span>Source: {risk.source_document}</span>}</div>
            {risk.severity_reason && <p className="insights-mitigation"><strong>Severity rationale:</strong> {risk.severity_reason}</p>}
            {risk.mitigation && <p className="insights-mitigation"><strong>Suggested mitigation:</strong> {risk.mitigation}</p>}
            {risk.evidence[0] && <EvidenceReferenceCard evidence={risk.evidence[0]} />}
          </article>)}</div>}
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2><Clock3 size={16} /> Action items</h2>
          <p>Pending, completed, and overdue items identified from current project documents.</p></div></div>
        {!data.actions.length ? <EmptyState title="No action items available" text="Actions extracted by the existing Blockers & Actions Agent will appear here." />
          : <div className="insights-action-list">{data.actions.map((action, index) => <article className="insights-action" key={`${action.id}-${index}`}>
            <div className="insights-action-main"><span className={`insights-action-status insight-status-${action.status}`}>{action.status}</span>
              <strong>{action.description}</strong>
              <div className="insights-action-meta"><span>Owner: {action.assignee ?? 'Unassigned'}</span>
                <span>Priority: {action.priority ?? 'Not stated'}</span>
                <span>Due: {action.due_date ?? 'Not stated'}</span>
                {action.source_document && <span>Source: {action.source_document}</span>}</div>
            </div>
            {action.evidence[0] && <EvidenceReferenceCard evidence={action.evidence[0]} />}
          </article>)}</div>}
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2>Project scope summary</h2>
          <p>Objectives, deliverables, milestones, and ownership extracted by the Scope &amp; Deliverables Agent.</p></div></div>
        {!data.scope.goals.length && !data.scope.objectives.length && !data.scope.deliverables.length
          && !data.scope.milestones.length && !data.scope.responsibilities.length
          && !data.scope.dependencies.length && !data.scope.progress.length
          ? <EmptyState title="Scope information is incomplete" text={data.scope.missing_information[0] ?? 'Upload project documents to extract scope details.'} />
          : <div className="insights-scope-grid">
            <ScopeList title="Project objectives" values={[...data.scope.goals, ...data.scope.objectives]} />
            <ScopeList title="Major deliverables" values={data.scope.deliverables} />
            <ScopeList title="Project progress updates" values={data.scope.progress} />
            <ScopeList title="Milestones" values={data.scope.milestones.map(item =>
              [item.name, item.date, item.status].filter(Boolean).join(' · '))} />
            <ScopeList title="Responsibilities" values={data.scope.responsibilities.map(item =>
              [item.task, item.owner, item.role].filter(Boolean).join(' · '))} />
            <ScopeList title="Dependencies" values={data.scope.dependencies.map(item =>
              item.description ?? [item.from_task, item.to_task].filter(Boolean).join(' → '))} />
            <ScopeList title="Scope clarity" values={health?.dimensions.scope_clarity.factors ?? []} />
          </div>}
      </section>
      {data.scope.source_documents.length > 0 && <div className="insights-sources">
        Sources: {data.scope.source_documents.map((source, index) => <span key={`${source}-${index}`}>{source}</span>)}
      </div>}
      <div className="insights-footer"><Link to="/project-health">View detailed health assessment</Link></div>
    </>}
  </div>
}

function Dimension({ title, value, detail }: { title: string; value: number | null; detail: string }) {
  return <article className="panel insights-dimension">
    <div><h2>{title}</h2><strong>{value === null ? 'Insufficient data' : `${Math.round(value)} / 100`}</strong></div>
    {value !== null && <div className="m3-progress-track"><span style={{ width: `${value}%` }} /></div>}
    <p>{detail}</p>
  </article>
}

function InsightMetric({ title, value, detail }: { title: string; value: number | string; detail: string }) {
  return <article className="insights-metric-card">
    <span>{title}</span><strong>{value}</strong><small>{detail}</small>
  </article>
}

function CountBar({ label, count, total, tone }: { label: string; count: number; total: number; tone: string }) {
  const percent = total ? count / total * 100 : 0
  return <div className="insights-bar-row"><div><span>{label}</span><strong>{count}</strong></div>
    <div className="insights-bar-track"><span className={tone} style={{ width: `${percent}%` }} /></div></div>
}

function ScopeList({ title, values }: { title: string; values: string[] }) {
  const populated = values.filter(Boolean)
  return <article className="insights-scope-item"><h3>{title}</h3>
    {populated.length ? <ul>{populated.map((value, index) => <li key={`${title}-${index}`}>{value}</li>)}</ul>
      : <p className="insights-not-stated">Not identified in indexed project documents.</p>}
  </article>
}
