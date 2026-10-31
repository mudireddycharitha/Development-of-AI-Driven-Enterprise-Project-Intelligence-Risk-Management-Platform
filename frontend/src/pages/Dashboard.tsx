import { useEffect, useState, type ReactNode } from 'react'
import { ArrowUpRight, Blocks, CheckSquare, FileCheck2, FileText, Files, RefreshCw, ShieldAlert, TriangleAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { PlatformStats } from '../types'
import { EmptyState, ErrorState, Loading, PageHeading, StatusBadge, formatBytes, formatDate } from '../components/Shared'

export function Dashboard() {
  const [stats, setStats] = useState<PlatformStats | null>(null)
  const [agentCounts, setAgentCounts] = useState<{ deliverables: number | null; risks: number | null; blockers: number | null; actions: number | null }>({
    deliverables: null, risks: null, blockers: null, actions: null,
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const load = () => {
    setLoading(true); setError('')
    apiService.stats().then(async result => {
      setStats(result)
      if (!result.successful_documents) {
        setAgentCounts({ deliverables: 0, risks: 0, blockers: 0, actions: 0 })
        return
      }
      const documents = await apiService.documents({ page: 1, page_size: 100, status: 'indexed' })
      const ids = documents.items.map(item => item.id)
      const [scope, risks, blockers] = await Promise.allSettled([
        apiService.scopeAnalysis(ids),
        apiService.riskAnalysis(ids),
        apiService.blockerAnalysis(ids),
      ])
      setAgentCounts({
        deliverables: scope.status === 'fulfilled' ? scope.value.deliverables.length : null,
        risks: risks.status === 'fulfilled' ? risks.value.findings.length : null,
        blockers: blockers.status === 'fulfilled' ? blockers.value.blockers.length : null,
        actions: blockers.status === 'fulfilled' ? blockers.value.action_items.length : null,
      })
    }).catch(e => setError(errorMessage(e))).finally(() => setLoading(false))
  }
  useEffect(load, [])
  return <>
    <PageHeading eyebrow="PROJECT INTELLIGENCE" title="Project Intelligence Dashboard"
      description="Transform project documents into actionable insights."
      action={<button className="button button-quiet" onClick={load}><RefreshCw size={15} /> Refresh</button>} />
    {loading && <Loading />}
    {error && <ErrorState message={error} />}
    {stats && <>
      <section className="metric-grid">
        <Metric title="Documents processed" value={stats.successful_documents} icon={<Files />} detail={`${stats.indexed_chunks ?? 'Unavailable'} chunks indexed`} />
        <Metric title="Project deliverables" value={agentCounts.deliverables ?? 'Unavailable'} icon={<FileCheck2 />} detail="Extracted from indexed documents" tone="green" />
        <Metric title="Identified risks" value={agentCounts.risks ?? 'Unavailable'} icon={<ShieldAlert />} detail="Evidence-backed findings" tone="amber" />
        <Metric title="Open blockers" value={agentCounts.blockers ?? 'Unavailable'} icon={<TriangleAlert />} detail="Reported in project documents" tone="amber" />
        <Metric title="Pending action items" value={agentCounts.actions ?? 'Unavailable'} icon={<CheckSquare />} detail="Assigned actions identified" tone="blue" />
      </section>
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
