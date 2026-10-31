import { useEffect, useState } from 'react'
import { Blocks, Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { ChunkPageResult, ChunkRecord, DocumentRecord } from '../types'
import { EmptyState, ErrorState, Loading, LocationText, PageHeading, Pagination, StatusBadge } from '../components/Shared'

export function ChunkExplorer() {
  const [data, setData] = useState<ChunkPageResult | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [documentId, setDocumentId] = useState('')
  const [fileType, setFileType] = useState('')
  const [selected, setSelected] = useState<ChunkRecord | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => { apiService.documents({ page_size: 100 }).then(result => setDocuments(result.items)).catch(e => setError(errorMessage(e))) }, [])
  useEffect(() => {
    setLoading(true)
    apiService.chunks({ page, page_size: 20, search: search || undefined, document_id: documentId || undefined, file_type: fileType || undefined })
      .then(result => setData(result)).catch(e => setError(errorMessage(e))).finally(() => setLoading(false))
  }, [page, search, documentId, fileType])
  return <>
    <PageHeading eyebrow="INDEX INSPECTION" title="Chunk explorer" description="Inspect the text units created by the pipeline, with source metadata and indexing status." />
    <section className="panel">
      <div className="filter-bar"><label className="search-field"><Search size={16} /><input placeholder="Search within chunk text…" value={search} onChange={event => { setPage(1); setSearch(event.target.value) }} /></label>
        <select aria-label="Filter chunks by source document" value={documentId} onChange={event => { setPage(1); setDocumentId(event.target.value) }}>
          <option value="">All documents</option>{documents.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}</select>
        <select aria-label="Filter chunks by file type" value={fileType} onChange={event => { setPage(1); setFileType(event.target.value) }}>
          <option value="">All formats</option>{[...new Set(documents.map(document => document.file_type))].sort().map(type => <option key={type} value={type}>{type.toUpperCase()}</option>)}</select>
        <span className="results-count">{data?.indexed_chunk_total ?? '—'} indexed chunks</span>
        <span className="results-count">{data?.total ?? '—'} stored matches</span></div>
      {Boolean(data?.unassociated_indexed_chunk_count) && <div className="alert-box">
        {data?.unassociated_indexed_chunk_count} indexed chunk{data?.unassociated_indexed_chunk_count === 1 ? '' : 's'} in ChromaDB could not be associated with a document record. They are included in the indexed total.
      </div>}
      {loading ? <Loading label="Loading indexed chunks…" /> : error ? <ErrorState message={error} /> : !data?.items.length ?
        <EmptyState title="No chunks found" text="Chunks appear after supported documents are extracted and indexed." /> :
        <div className="table-wrap"><table className="chunk-table"><thead><tr><th>Chunk</th><th>Source</th><th>Content preview</th><th>Source location</th><th>Index</th></tr></thead>
          <tbody>{data.items.map(chunk => <tr key={chunk.id} onClick={() => setSelected(chunk)} className="clickable-row">
            <td><strong>#{chunk.chunk_index + 1}</strong><small className="mono">{chunk.id}</small></td>
            <td><Link className="table-source" onClick={event => event.stopPropagation()} to={`/documents/${chunk.document_id}`}>{chunk.filename}</Link><small><span className="format-tag">{chunk.file_type.toUpperCase()}</span></small></td>
            <td className="preview-cell">{chunk.text}</td><td><LocationText location={chunk.location} /></td>
            <td><StatusBadge status={chunk.indexing_status} /></td>
          </tr>)}</tbody></table></div>}
      {data && <Pagination page={data.page} pages={data.pages} onPage={setPage} />}
    </section>
    {selected && <div className="modal-backdrop" role="presentation" onClick={() => setSelected(null)}>
      <section className="modal-card" role="dialog" aria-modal="true" aria-label="Chunk detail" onClick={event => event.stopPropagation()}>
        <div className="panel-heading"><div><h2><Blocks size={18} /> Chunk detail</h2><p>{selected.id}</p></div><button className="icon-button" onClick={() => setSelected(null)} aria-label="Close">×</button></div>
        <div className="detail-metadata"><span>Source <strong>{selected.filename}</strong></span><span>Format <strong>{selected.file_type.toUpperCase()}</strong></span><span>Index <strong>{selected.chunk_index + 1}</strong></span>
          <span>Approx. tokens <strong>{selected.token_count}</strong></span><span>Location <strong><LocationText location={selected.location} /></strong></span>
          <span>Embedding <StatusBadge status={selected.embedding_status} /></span><span>Indexing <StatusBadge status={selected.indexing_status} /></span></div>
        <pre className="chunk-detail-text">{selected.text}</pre>
      </section>
    </div>}
  </>
}
