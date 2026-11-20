import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ArrowUpRight, Blocks, CheckSquare, FileCheck2, FileText, Files, HeartPulse, RefreshCw, ShieldAlert, TriangleAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { PlatformStats, ProjectInsightsResult } from '../types'
import { EmptyState, ErrorState, Loading, PageHeading, StatusBadge, formatBytes, formatDate } from '../components/Shared'

export function Dashboard() {
  const [stats, setStats] = useState<PlatformStats | null>(null)
  const [insights, setInsights] = useState<ProjectInsightsResult | null>(null)
  const [error, setError] = useState('')
  const [insightsError, setInsightsError] = useState('')
  const [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    setInsightsError('')
    const [statsResult, insightsResult] = await Promise.allSettled([
      apiService.stats(),
      apiService.projectInsights(),
    ])
    if (statsResult.status === 'fulfilled') setStats(statsResult.value)
    else {
      setStats(null)
      setError(errorMessage(statsResult.reason))
    }
    if (insightsResult.status === 'fulfilled') setInsights(insightsResult.value)
    else {
      setInsights(null)
      setInsightsError(errorMessage(insightsResult.reason))
    }
    setLoading(false)
  }, [])
  useEffect(() => {
    void load()
    const refresh = () => { if (!document.hidden) void load() }
    const interval = window.setInterval(refresh, 30_000)
    window.addEventListener('focus', refresh)
    return () => {
      window.clearInterval(interval)
      window.removeEventListener('focus', refresh)
    }
  }, [load])
  return <>
    <PageHeading eyebrow="WORKSPACE" title="Overview"
      description="A current view of your indexed project knowledge and delivery signals."
      action={<button className="button button-quiet" onClick={() => void load()} disabled={loading}><RefreshCw size={15} /> Refresh</button>} />
    {loading && <Loading />}
    {error && <ErrorState message={error} />}
    {insightsError && <ErrorState message={`Project agent insights are unavailable: ${insightsError}`} />}
    {insights && <section className="metric-grid">
      <Metric title="Documents uploaded" value={insights.metrics.total_uploaded_documents} icon={<Files />} detail="Includes retained document versions" />
      <Metric title="Documents processed" value={insights.metrics.processed_documents} icon={<FileText />} detail={`${insights.metrics.current_indexed_documents} current indexed sources`} tone="blue" />
      <Metric title="Identified risks" value={insights.metrics.total_risks} icon={<ShieldAlert />} detail="Current Risk Intelligence Agent findings" tone="amber" />
      <Metric title="Total blockers" value={insights.metrics.total_blockers} icon={<TriangleAlert />} detail={`${insights.blocker_counts.unresolved} unresolved`} tone="amber" />
      <Metric title="Action items" value={insights.metrics.total_actions} icon={<CheckSquare />} detail={`${insights.action_counts.pending + insights.action_counts.overdue} pending or overdue`} tone="blue" />
      <Metric title="Deliverables" value={insights.metrics.total_deliverables} icon={<FileCheck2 />} detail="Extracted by Scope & Deliverables Agent" tone="green" />
      <Metric title="Project health" value={insights.metrics.overall_project_health_score === null
        ? insights.project_status
        : `${Math.round(insights.metrics.overall_project_health_score)} / 100`}
        icon={<HeartPulse />} detail={insights.project_status} tone="blue" />
      <Metric title="Generated documentation" value={insights.documentation.is_current
        ? insights.documentation.total_generated_items : 'Not generated'}
        icon={<FileText />} detail="Latest generation for current indexed sources" />
      <div className="overview-insights-link"><Link className="text-link" to="/project-insights">Open Project Insights <ArrowUpRight size={14} /></Link></div>
    </section>}
    {stats && <>
      <div className="overview-grid">
        <section className="panel">
          <div className="panel-heading"><div><h2>Recent documents</h2><p>Latest uploads and processing outcomes</p></div>
            <Link className="text-link" to="/documents">All documents <ArrowUpRight size={14} /></Link></div>
          <div className="table-wrap"><table><thead><tr><th>Document</th><th>Uploaded</th><th>Status</th><th>Chunks</th></tr></thead>
            <tbody>{stats.recent_documents.map(document => <tr key={document.id}>
              <td><Link to={`/documents/${document.id}`} className="file-cell"><span className="file-icon"><FileText size={16} /></span>
                <span><strong>{document.filename}</strong><small>{document.file_type.toUpperCase()} · {formatBytes(document.file_size)}</small></span></Link></td>
              <td>{formatDate(document.uploaded_at)}</td><td><StatusBadge status={document.processing_status} /></td>
              <td>{document.indexed_chunk_count ?? '—'}/{document.chunk_count}</td></tr>)}</tbody></table>
            {!stats.recent_documents.length && <EmptyState title="Nothing uploaded yet" text="Upload documents to see extraction and indexing activity here." />}</div>
        </section>
        <section className="panel index-panel">
          <div className="panel-heading"><div><h2>Index health</h2><p>Current local processing resources</p></div><span className="subtle-icon"><Blocks size={19} /></span></div>
          <div className="health-row"><span>API service</span><StatusBadge status={stats.backend_status} /></div>
          <div className="health-row"><span>Chroma vector store</span><StatusBadge status={stats.vector_store_status} /></div>
          <div className="health-row"><span>Indexed vectors</span><strong>{stats.vector_count ?? 'Unavailable'}</strong></div>
          <div className="health-row"><span>Total text chunks</span><strong>{stats.total_chunks}</strong></div>
          <Link to="/search" className="button button-primary full-button">Search your workspace <ArrowUpRight size={15} /></Link>
        </section>
      </div>
      <section className="format-strip"><div><span className="subtle-icon"><FileText size={18} /></span><span><strong>{stats.supported_formats.length} supported formats</strong><small>Format-specific text extraction</small></span></div>
        <Link to="/settings" className="text-link">View supported formats <ArrowUpRight size={14} /></Link></section>
    </>}
  </>
}

function Metric({ title, value, icon, detail, tone = 'neutral' }: { title: string; value: number | string; icon: ReactNode; detail: string; tone?: string }) {
  return <div className="metric-card"><div className={`metric-icon tone-${tone}`}>{icon}</div><div className="metric-title">{title}</div>
    <div className="metric-value">{value}</div><div className="metric-detail">{detail}</div></div>
}
