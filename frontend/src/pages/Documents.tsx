import { useEffect, useState } from 'react'
import { Eye, RefreshCw, RotateCcw, Search, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { DocumentPageResult, DocumentRecord } from '../types'
import { EmptyState, ErrorState, Loading, PageHeading, Pagination, StatusBadge, formatBytes, formatDate } from '../components/Shared'

export function Documents() {
  const [data, setData] = useState<DocumentPageResult | null>(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [actionId, setActionId] = useState('')
  const load = () => {
    setLoading(true); setError('')
    apiService.documents({ page, page_size: 20, search: search || undefined, status: status || undefined })
      .then(setData).catch(e => setError(errorMessage(e))).finally(() => setLoading(false))
  }
  useEffect(load, [page, search, status])
  const remove = async (document: DocumentRecord) => {
    if (!window.confirm(`Delete "${document.filename}" and its chunks and vectors?`)) return
    setActionId(document.id); setError('')
    try { await apiService.removeDocument(document.id); load() }
    catch (e) { setError(errorMessage(e)) } finally { setActionId('') }
  }
  const retry = async (document: DocumentRecord) => {
    setActionId(document.id); setError('')
    try { await apiService.reprocess(document.id); load() }
    catch (e) { setError(`${document.filename}: ${errorMessage(e)}`) } finally { setActionId('') }
  }
  return <>
    <PageHeading eyebrow="DOCUMENT LIBRARY" title="Documents" description="Browse source files, inspect extracted content, and manage processing." />
    <section className="panel">
      <div className="filter-bar"><label className="search-field"><Search size={16} /><input value={search} onChange={event => { setPage(1); setSearch(event.target.value) }} placeholder="Search filenames…" /></label>
        <select aria-label="Filter by processing status" value={status} onChange={event => { setPage(1); setStatus(event.target.value) }}>
          <option value="">All statuses</option><option value="indexed">Indexed</option><option value="failed">Failed</option><option value="extracting">Extracting</option><option value="embedding">Embedding</option>
        </select><span className="results-count">{data?.total ?? '—'} documents</span><span className="results-count">{data?.indexed_chunk_total ?? '—'} indexed chunks</span></div>
      {Boolean(data?.unassociated_indexed_chunk_count) && <div className="alert-box">
        {data?.unassociated_indexed_chunk_count} indexed chunk{data?.unassociated_indexed_chunk_count === 1 ? '' : 's'} in ChromaDB could not be associated with a document record. They are included in the indexed total.
      </div>}
      {loading ? <Loading label="Loading documents…" /> : error ? <ErrorState message={error} /> : !data?.items.length ?
        <EmptyState title="No documents found" text="Try another filter or upload a mixed-format batch." /> :
        <div className="table-wrap"><table><thead><tr><th>Filename</th><th>Format</th><th>Size</th><th>Uploaded</th><th>Status</th><th>Indexed / extracted</th><th>Actions</th></tr></thead>
          <tbody>{data.items.map(document => <tr key={document.id}>
            <td><Link to={`/documents/${document.id}`} className="file-cell"><strong>{document.filename}</strong></Link></td>
            <td><span className="format-tag">{document.file_type.toUpperCase()}</span></td><td>{formatBytes(document.file_size)}</td>
            <td>{formatDate(document.uploaded_at)}</td><td><StatusBadge status={document.processing_status} /></td>
            <td>{document.indexed_chunk_count ?? '—'}/{document.chunk_count}</td>
            <td><div className="table-actions"><Link aria-label="View document" className="icon-button" to={`/documents/${document.id}`}><Eye size={16} /></Link>
              {document.processing_status === 'failed' && <button aria-label="Retry document processing" className="icon-button" disabled={actionId === document.id} onClick={() => void retry(document)}><RotateCcw size={15} /></button>}
              <button aria-label="Delete document" className="icon-button danger-icon" disabled={actionId === document.id} onClick={() => void remove(document)}><Trash2 size={15} /></button></div></td>
          </tr>)}</tbody></table></div>}
      {data && <Pagination page={data.page} pages={data.pages} onPage={setPage} />}
    </section>
    {data?.items.length === 0 && !loading && <div className="subtle-refresh"><RefreshCw size={14} /> Results update as filters change.</div>}
  </>
}
