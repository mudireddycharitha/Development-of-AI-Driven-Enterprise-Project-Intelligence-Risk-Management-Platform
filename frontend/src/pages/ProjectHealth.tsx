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

      <HealthRadar result={result} />

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

function HealthRadar({ result }: { result: ProjectHealthResult }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const timelineHealth = result.dimensions.timeline_health
  const timelineRisk = timelineHealth.available && timelineHealth.score !== null
    ? 100 - timelineHealth.score
    : null
  const items = [
    {
      label: 'Scope Clarity',
      shortLabel: 'Scope',
      score: result.dimensions.scope_clarity.available ? result.dimensions.scope_clarity.score : null,
      explanation: result.dimensions.scope_clarity.explanation,
    },
    {
      label: 'Timeline Risk',
      shortLabel: 'Timeline',
      score: timelineRisk,
      explanation: timelineHealth.available
        ? `Derived as 100 minus the existing timeline health score (${timelineHealth.score === null ? 'unavailable' : `${Math.round(timelineHealth.score)} / 100`}).`
        : timelineHealth.explanation,
    },
    {
      label: 'Blocker Health',
      shortLabel: 'Blockers',
      score: result.dimensions.blocker_health.available ? result.dimensions.blocker_health.score : null,
      explanation: result.dimensions.blocker_health.explanation,
    },
  ]

  const center = 170
  const radius = 112
  const pointFor = (index: number, value: number) => {
    const angle = -Math.PI / 2 + index * (Math.PI * 2 / 3)
    const distance = radius * Math.max(0, Math.min(100, value)) / 100
    return [center + Math.cos(angle) * distance, center + Math.sin(angle) * distance] as const
  }
  const axisPoint = (index: number) => pointFor(index, 100)
  const grid = [20, 40, 60, 80, 100].map(level => items.map((_, index) => pointFor(index, level).join(',')).join(' '))
  const completeData = items.every(item => item.score !== null)
  const polygonPoints = completeData
    ? items.map((item, index) => pointFor(index, item.score as number).join(',')).join(' ')
    : ''
  const healthDimensions = [
    { label: 'Scope Clarity', score: result.dimensions.scope_clarity.score, available: result.dimensions.scope_clarity.available },
    { label: 'Timeline Health', score: result.dimensions.timeline_health.score, available: result.dimensions.timeline_health.available },
    { label: 'Blocker Health', score: result.dimensions.blocker_health.score, available: result.dimensions.blocker_health.available },
  ].filter(item => item.available && item.score !== null)
  const strongest = healthDimensions.length ? healthDimensions.reduce((best, item) => item.score! > best.score! ? item : best) : null
  const weakest = healthDimensions.length ? healthDimensions.reduce((worst, item) => item.score! < worst.score! ? item : worst) : null

  return <section className="panel m3-radar-panel">
    <div className="panel-heading">
      <div><h2>Project Health Intelligence Radar</h2><p>Actual backend dimensions on a 0–100 scale. Timeline Risk is the inverse of Timeline Health.</p></div>
      <div className="m3-radar-legend"><span><i className="legend-area" /> Health profile</span><span><i className="legend-point" /> Exact score</span></div>
    </div>
    <div className="m3-radar-layout">
      <div className="m3-radar-chart" role="img" aria-label="Radar chart for scope clarity, timeline risk, and blocker health">
        <svg viewBox="0 0 340 340" aria-label="Project health radar chart">
          <defs>
            <linearGradient id="radarHealthGradient" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#7796c5" stopOpacity=".62" />
              <stop offset="55%" stopColor="#4d9b91" stopOpacity=".42" />
              <stop offset="100%" stopColor="#c5a46d" stopOpacity=".38" />
            </linearGradient>
            <filter id="radarGlow" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>
          {grid.map((points, index) => <polygon key={index} points={points} className="m3-radar-grid" />)}
          {[20, 40, 60, 80, 100].map(level => {
            const [x, y] = pointFor(0, level)
            return <text key={`ring-${level}`} x={x + 5} y={y + 3} className="m3-radar-ring-label">{level}</text>
          })}
          {items.map((item, index) => {
            const [x, y] = axisPoint(index)
            return <line key={item.label} x1={center} y1={center} x2={x} y2={y} className="m3-radar-axis" />
          })}
          {polygonPoints && <polygon points={polygonPoints} className="m3-radar-area" />}
          {items.map((item, index) => {
            if (item.score === null) return null
            const [x, y] = pointFor(index, item.score)
            const [labelX, labelY] = axisPoint(index)
            const labelAnchor = index === 1 ? 'middle' : 'middle'
            return <g key={item.label}>
              <circle cx={x} cy={y} r={activeIndex === index ? 7 : 5}
                className={`m3-radar-point${activeIndex === index ? ' active' : ''}`}
                tabIndex={0}
                role="button"
                aria-label={`${item.label}: ${Math.round(item.score)} out of 100`}
                onMouseEnter={() => setActiveIndex(index)}
                onMouseLeave={() => setActiveIndex(null)}
                onFocus={() => setActiveIndex(index)}
                onBlur={() => setActiveIndex(null)}>
                <title>{item.label}: {Math.round(item.score)} / 100</title>
              </circle>
              <text x={labelX} y={labelY + (index === 0 ? -12 : index === 1 ? 24 : 24)}
                textAnchor={labelAnchor} className="m3-radar-label">
                {item.shortLabel} {Math.round(item.score)}
              </text>
            </g>
          })}
        </svg>
        {activeIndex !== null && items[activeIndex] && <div className="m3-radar-tooltip" role="tooltip">
          <strong>{items[activeIndex].label}</strong>
          <span>{items[activeIndex].score === null ? 'Unavailable' : `${Math.round(items[activeIndex].score)} / 100`}</span>
          <small>{items[activeIndex].explanation}</small>
        </div>}
      </div>
      <div className="m3-radar-summary">
        <div className="m3-radar-overall">
          <span>Overall Project Health Score</span>
          <strong>{result.overall_score === null ? 'Unavailable' : `${Math.round(result.overall_score)} / 100`}</strong>
          <small>Current Status: {result.classification}</small>
        </div>
        {items.map(item => <div className="m3-radar-score-row" key={item.label}>
          <span>{item.label} Score</span>
          <strong>{item.score === null ? 'Unavailable' : `${Math.round(item.score)} / 100`}</strong>
        </div>)}
        <div className="m3-radar-insights">
          <div><span>Strongest dimension</span><strong>{strongest ? `${strongest.label} · ${Math.round(strongest.score ?? 0)} / 100` : 'Unavailable'}</strong></div>
          <div><span>Weakest dimension</span><strong>{weakest ? `${weakest.label} · ${Math.round(weakest.score ?? 0)} / 100` : 'Unavailable'}</strong></div>
        </div>
        <div className="m3-radar-health-details">
          <p><strong>Scope Clarity</strong> — {result.dimensions.scope_clarity.explanation}</p>
          <p><strong>Timeline Health</strong> — {result.dimensions.timeline_health.explanation} {result.delayed_milestones.length ? `${result.delayed_milestones.length} delayed or overdue milestone(s).` : 'No delayed milestones reported.'}</p>
          <p><strong>Blocker Health</strong> — {result.dimensions.blocker_health.explanation} {result.unresolved_blockers.length} unresolved blocker(s).</p>
        </div>
        {!completeData && <p className="m3-radar-unavailable">
          Incomplete dimension data is not plotted as a zero; unavailable scores are clearly withheld.
        </p>}
      </div>
    </div>
  </section>
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
