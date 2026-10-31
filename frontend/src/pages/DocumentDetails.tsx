import { useEffect, useState } from 'react'
import { ArrowLeft, FileText, RefreshCw } from 'lucide-react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { ChunkRecord, DocumentRecord } from '../types'
import { ErrorState, Loading, PageHeading, Pagination, StatusBadge, formatBytes, formatDate } from '../components/Shared'

export function DocumentDetails() {
  const { documentId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const targetChunkId = searchParams.get('chunk')
  const [document, setDocument] = useState<DocumentRecord | null>(null)
  const [text, setText] = useState('')
  const [chunks, setChunks] = useState<ChunkRecord[]>([])
  const [page, setPage] = useState(1)
  const [pages, setPages] = useState(0)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    setLoading(true); setError('')
    Promise.all([apiService.document(documentId), apiService.documentText(documentId), apiService.documentChunks(documentId, { page, page_size: 10 })])
      .then(async ([record, extracted, chunkPage]) => {
        let visibleChunks = chunkPage.items
        if (targetChunkId) {
          const targetChunk = await apiService.chunk(targetChunkId)
          if (targetChunk.document_id !== documentId) throw new Error('The requested source chunk does not belong to this document.')
          if (!visibleChunks.some(chunk => chunk.id === targetChunk.id)) visibleChunks = [targetChunk, ...visibleChunks]
        }
        setDocument(record); setText(extracted.text); setChunks(visibleChunks); setPages(chunkPage.pages)
      })
      .catch(e => setError(errorMessage(e))).finally(() => setLoading(false))
  }, [documentId, page, targetChunkId])

  useEffect(() => {
    if (!loading && targetChunkId) {
      globalThis.document.getElementById(`chunk-${targetChunkId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [loading, targetChunkId, chunks])
  return <>
    <Link to="/documents" className="back-link"><ArrowLeft size={15} /> Back to documents</Link>
    {loading && <Loading label="Loading document details…" />}
    {error && <ErrorState message={error} />}
    {document && <>
      <PageHeading eyebrow="DOCUMENT DETAILS" title={document.filename}
        description={`${document.file_type.toUpperCase()} · ${formatBytes(document.file_size)} · Uploaded ${formatDate(document.uploaded_at)}`}
        action={<StatusBadge status={document.processing_status} />} />
      {document.extraction_error && <div className="inline-error"><strong>{document.failed_stage ?? 'Processing'} failed:</strong> {document.extraction_error}</div>}
      <div className="detail-stat-grid">
        <div className="detail-stat"><span>Processing status</span><StatusBadge status={document.processing_status} /></div>
        <div className="detail-stat"><span>Total chunks</span><strong>{document.chunk_count}</strong></div>
        <div className="detail-stat"><span>Indexed chunks</span><strong>{document.indexed_chunk_count}</strong></div>
        <div className="detail-stat"><span>File size</span><strong>{formatBytes(document.file_size)}</strong></div>
      </div>
      <section className="panel text-panel"><div className="panel-heading"><div><h2><FileText size={17} /> Extracted text</h2><p>Text persisted from the uploaded source document</p></div>
        <button className="button button-quiet" onClick={() => navigator.clipboard.writeText(text)} disabled={!text}><RefreshCw size={14} /> Copy text</button></div>
        <pre className="extracted-text">{text || 'No text has been extracted for this document.'}</pre></section>
      <section className="panel"><div className="panel-heading"><div><h2>Document chunks</h2><p>Actual chunks created by the indexing pipeline</p></div></div>
        {!chunks.length ? <div className="table-empty">No chunks are available.</div> : <div className="chunk-list">{chunks.map(chunk => <details id={`chunk-${chunk.id}`} open={chunk.id === targetChunkId} className={`chunk-card${chunk.id === targetChunkId ? ' evidence-target' : ''}`} key={chunk.id}>
          <summary><span className="chunk-id">#{chunk.chunk_index + 1}</span><code>{chunk.id}</code><span>{chunk.token_count} approx. tokens</span><StatusBadge status={chunk.indexing_status} /></summary>
          <pre>{chunk.text}</pre><div className="chunk-metadata">Embedding: {chunk.embedding_status} · {chunk.embedding_dimensions ?? '—'} dimensions · {JSON.stringify(chunk.location)}</div>
        </details>)}</div>}
        <Pagination page={page} pages={pages} onPage={setPage} />
      </section>
    </>}
  </>
}
