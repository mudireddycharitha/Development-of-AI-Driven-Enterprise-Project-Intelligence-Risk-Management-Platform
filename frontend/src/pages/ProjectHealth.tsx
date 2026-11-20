import { useEffect, useState, type ReactNode } from 'react'
import { Activity, AlertTriangle, HeartPulse, RefreshCw, ShieldAlert } from 'lucide-react'
import { EvidenceReferenceCard } from '../components/Milestone3Shared'
import { EmptyState, ErrorState, Loading, PageHeading } from '../components/Shared'
import { apiService, errorMessage } from '../services/api'
import type { ProjectHealthDimension, ProjectHealthResult } from '../types'

export function ProjectHealth() {
  const [result, setResult] = useState<ProjectHealthResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [recalculating, setRecalculating] = useState(false)
  const [error, setError] = useState('')

  const calculate = async () => {
    setRecalculating(true)
    setError('')
    try {
      setResult(await apiService.recalculateProjectHealth())
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setRecalculating(false)
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    apiService.projectHealthLatest()
      .then(async saved => {
        if (!active) return
        if (saved) setResult(saved)
        else {
          const calculated = await apiService.recalculateProjectHealth()
          if (active) setResult(calculated)
        }
      })
      .catch(requestError => { if (active) setError(errorMessage(requestError)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const overall = result?.overall_score ?? null
  const scoreColor = result?.classification === 'Healthy' ? 'healthy'
    : result?.classification === 'Needs Attention' ? 'attention'
      : result?.classification === 'At Risk' ? 'at-risk' : 'unknown'

  return <div className="m3-page">
    <PageHeading eyebrow="PROJECT INTELLIGENCE" title="Project Health"
      description="Evidence-based scope, timeline, and blocker dimensions with transparent scoring rules."
      action={<button className="button button-primary" onClick={() => void calculate()} disabled={recalculating}>
        <RefreshCw size={15} className={recalculating ? 'spin' : ''} /> {recalculating ? 'Recalculating…' : 'Recalculate health'}
      </button>} />
    {error && <ErrorState message={error} />}
    {loading && <Loading label="Loading the latest project health calculation…" />}
    {result && <>
      <section className="m3-health-hero panel">
        <div className={`m3-health-gauge ${scoreColor}`} style={{ backgroundImage: overall === null ? undefined : `conic-gradient(var(--gauge-color) ${overall}%, var(--surface-elevated) 0)` }}>
          <div className="m3-health-gauge-center">
            <strong>{overall === null ? '—' : Math.round(overall)}</strong><small>{overall === null ? 'No score' : 'out of 100'}</small>
          </div>
        </div>
        <div className="m3-health-summary">
          <div className="eyebrow">OVERALL PROJECT HEALTH</div>
          <h2>{result.classification}</h2>
          <p>{overall === null
            ? 'An overall score is withheld because at least one required dimension does not have enough evidence.'
            : 'Weighted using Scope Clarity 35%, Timeline Health 35%, and Blocker Health 30%.'}</p>
          <div className="m3-health-meta"><span><Activity size={14} /> {result.document_count} indexed document(s)</span><span>Calculated {new Date(result.calculated_at).toLocaleString()}</span></div>
          {overall !== null && <div className="m3-weighted-formula">
            {`(${result.dimensions.scope_clarity.score} × 0.35) + (${result.dimensions.timeline_health.score} × 0.35) + (${result.dimensions.blocker_health.score} × 0.30) = ${overall}`}
          </div>}
        </div>
      </section>

      <section className="m3-health-dimensions">
        {Object.entries(result.dimensions).map(([key, dimension]) => <HealthDimension key={key} dimension={dimension} weight={result.weights[key] ?? 0} />)}
      </section>

      <section className="m3-health-counts">
        <HealthCount title="Identified risks" count={result.identified_risks.length} icon={<ShieldAlert size={18} />} />
        <HealthCount title="Unresolved blockers" count={result.unresolved_blockers.length} icon={<AlertTriangle size={18} />} />
        <HealthCount title="Pending decisions" count={result.pending_decisions.length} icon={<Activity size={18} />} />
        <HealthCount title="Delayed milestones" count={result.delayed_milestones.length} icon={<HeartPulse size={18} />} />
      </section>

      <section className="panel">
        <div className="panel-heading"><div><h2>Recommended next actions</h2><p>Recommendations are derived from the current findings and scoring factors.</p></div></div>
        {result.recommended_actions.length
          ? <ol className="m3-recommendations">{result.recommended_actions.map((item, index) => <li key={`recommendation-${index}`}>{item}</li>)}</ol>
          : <EmptyState title="No recommendations available" text="Recalculate after adding more project evidence." />}
      </section>

      <section className="m3-health-details-grid">
        <HealthFindings title="Top identified risks" items={result.identified_risks.map(item => ({
          title: `${item.level} · ${item.title}`, description: item.description, evidence: item.evidence[0],
        }))} />
        <HealthFindings title="Unresolved blockers and decisions" items={[
          ...result.unresolved_blockers.map(item => ({ title: item.description, description: item.recommendation ?? '', evidence: item.evidence[0] })),
          ...result.pending_decisions.map(item => ({ title: `Decision · ${item.description}`, description: item.recommendation ?? '', evidence: item.evidence[0] })),
        ]} />
        <HealthFindings title="Delayed or overdue milestones" items={result.delayed_milestones.map(item => ({
          title: item.name ?? item.description ?? 'Milestone', description: item.description ?? '', evidence: item.evidence[0],
        }))} />
      </section>
    </>}
  </div>
}

function HealthDimension({ dimension, weight }: { dimension: ProjectHealthDimension; weight: number }) {
  return <article className="panel m3-dimension-card">
    <div className="m3-card-heading"><h2>{dimension.name}</h2><span>{Math.round(weight * 100)}% weight</span></div>
    <div className="m3-dimension-score">{dimension.score === null ? 'Insufficient Data' : `${Math.round(dimension.score)} / 100`}</div>
    {dimension.score !== null && <div className="m3-progress-track"><span style={{ width: `${dimension.score}%` }} /></div>}
    <p>{dimension.explanation}</p>
    <div className="m3-subsection"><strong>Scoring factors</strong>
      <ul>{dimension.factors.map((factor, index) => <li key={`${dimension.name}-${index}`}>{factor}</li>)}</ul>
    </div>
  </article>
}

function HealthCount({ title, count, icon }: { title: string; count: number; icon: ReactNode }) {
  return <article className="panel m3-health-count"><span>{icon}</span><small>{title}</small><strong>{count}</strong></article>
}

function HealthFindings({ title, items }: { title: string; items: Array<{ title: string; description: string; evidence?: ProjectHealthResult['identified_risks'][number]['evidence'][number] }> }) {
  return <section className="panel">
    <div className="panel-heading"><div><h2>{title}</h2><p>Source links are available where the original chunk was matched.</p></div></div>
    {!items.length ? <div className="m3-inline-empty">No supported findings in the current calculation.</div>
      : <div className="m3-finding-list">{items.map((item, index) => <article className="m3-mini-finding" key={`${title}-${index}`}>
        <strong>{item.title}</strong>{item.description && <p>{item.description}</p>}
        {item.evidence && <EvidenceReferenceCard evidence={item.evidence} />}
      </article>)}</div>}
  </section>
}
