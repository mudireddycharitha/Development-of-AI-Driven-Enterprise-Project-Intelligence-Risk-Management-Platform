import { useEffect, useState } from 'react'
import { Boxes, ChevronDown, ChevronRight } from 'lucide-react'
import { apiService, errorMessage } from '../services/api'
import type { ChunkRecord, DocumentRecord, PageResult } from '../types'
import { EmptyState, ErrorState, Loading, PageHeading, Pagination, StatusBadge } from '../components/Shared'

type EmbeddingInfo = { dimensions: number; preview: number[]; vector?: number[]; embedding_model: string; filename: string }

export function EmbeddingsExplorer() {
  const [data, setData] = useState<PageResult<ChunkRecord> | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [page, setPage] = useState(1)
  const [documentId, setDocumentId] = useState('')
  const [selected, setSelected] = useState('')
  const [embedding, setEmbedding] = useState<EmbeddingInfo | null>(null)
  const [showFull, setShowFull] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  useEffect(() => { apiService.documents({ page_size: 100 }).then(result => setDocuments(result.items)).catch(e => setError(errorMessage(e))) }, [])
  useEffect(() => {
    setLoading(true); setSelected(''); setEmbedding(null)
    apiService.chunks({ page, page_size: 20, document_id: documentId || undefined })
      .then(result => setData(result)).catch(e => setError(errorMessage(e))).finally(() => setLoading(false))
  }, [page, documentId])
  const inspect = async (chunk: ChunkRecord, full = false) => {
    setSelected(chunk.id); setShowFull(full); setError('')
    try { setEmbedding(await apiService.embedding(chunk.id, full)) }
    catch (e) { setEmbedding(null); setError(errorMessage(e)) }
  }
  const values = showFull ? embedding?.vector : embedding?.preview
  return <>
    <PageHeading eyebrow="VECTOR INDEX" title="Embeddings explorer" description="Explore actual model-generated vectors associated with indexed document chunks." />
    <div className="info-callout"><Boxes size={19} /><span>Embeddings are numerical representations of text. Chroma uses vector distance to find semantically related content, even when query wording differs from the source.</span></div>
    <section className="panel">
      <div className="filter-bar"><select aria-label="Filter embeddings by document" value={documentId} onChange={event => { setPage(1); setDocumentId(event.target.value) }}>
        <option value="">All documents</option>{documents.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}</select>
        <span className="results-count">{data?.total ?? '—'} chunks</span></div>
      {loading ? <Loading label="Loading indexed embeddings…" /> : error && !data ? <ErrorState message={error} /> : !data?.items.length ?
        <EmptyState title="No chunks to inspect" text="Upload and index supported documents to generate real embeddings." /> :
        <div className="table-wrap"><table><thead><tr><th>Source</th><th>Chunk ID</th><th>Model</th><th>Dimensions</th><th>Embedding</th><th>Status</th></tr></thead>
          <tbody>{data.items.map(chunk => <tr key={chunk.id} className={selected === chunk.id ? 'selected-row' : ''}>
            <td><strong>{chunk.filename}</strong><small><span className="format-tag">{chunk.file_type.toUpperCase()}</span></small></td>
            <td><code className="mono">{chunk.id}</code></td><td>{chunk.embedding_model ?? '—'}</td>
            <td>{chunk.embedding_dimensions ?? '—'}</td><td><button className="button button-quiet compact-button" disabled={chunk.embedding_status !== 'complete'} onClick={() => void inspect(chunk)}>
              {selected === chunk.id && embedding ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Inspect vector</button></td><td><StatusBadge status={chunk.indexing_status} /></td>
          </tr>)}</tbody></table></div>}
      {data && <Pagination page={data.page} pages={data.pages} onPage={setPage} />}
    </section>
    {error && data && <ErrorState message={error} />}
    {embedding && <section className="panel vector-panel"><div className="panel-heading"><div><h2>Vector values</h2><p>{embedding.filename} · {embedding.embedding_model}</p></div>
      <span className="dimension-pill">{embedding.dimensions} dimensions</span></div>
      <div className="vector-meta">Actual numerical embedding · {showFull ? 'full vector' : `preview of ${embedding.preview.length} values`}</div>
      <pre className="vector-values">{(values ?? []).map(value => value.toFixed(7)).join(', ')}</pre>
      <button className="button button-secondary" onClick={() => {
        const chunk = data?.items.find(item => item.id === selected)
        if (chunk) void inspect(chunk, !showFull)
      }}>{showFull ? 'Show preview only' : 'Load full vector'}</button>
    </section>}
  </>
}
