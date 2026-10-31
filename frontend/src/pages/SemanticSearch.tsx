import { useEffect, useState, type FormEvent } from 'react'
import { ArrowDownWideNarrow, ArrowUpRight, Search } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import type { DocumentRecord, SearchResult } from '../types'
import { EmptyState, ErrorState, LocationText, PageHeading } from '../components/Shared'

export function SemanticSearch() {
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState(5)
  const [documentId, setDocumentId] = useState('')
  const [fileType, setFileType] = useState('')
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [results, setResults] = useState<SearchResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { apiService.documents({ page_size: 100 }).then(result => setDocuments(result.items)).catch(e => setError(errorMessage(e))) }, [])
  const formats = [...new Set(documents.map(document => document.file_type))].sort()
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!query.trim()) { setError('Enter a question or phrase to search.'); return }
    setLoading(true); setError(''); setResults(null)
    try { setResults(await apiService.search(query.trim(), topK, documentId || undefined, fileType || undefined)) }
    catch (e) { setError(errorMessage(e)) }
    finally { setLoading(false) }
  }
  return <>
    <PageHeading eyebrow="RETRIEVAL" title="Semantic search" description="Ask a natural-language question and retrieve the closest indexed text chunks with source references." />
    <section className="panel search-panel">
      <form onSubmit={event => void submit(event)}>
        <label htmlFor="semantic-query" className="field-label">What would you like to find?</label>
        <div className="query-box"><Search size={19} /><textarea id="semantic-query" value={query} onChange={event => setQuery(event.target.value)}
          placeholder="For example: What are the major project risks and their mitigation strategies?" rows={3} /></div>
        <div className="search-options"><label><span>Results</span><select value={topK} onChange={event => setTopK(Number(event.target.value))}>{[3, 5, 10, 20, 50].map(value => <option key={value} value={value}>{value} chunks</option>)}</select></label>
          <label><span>Source document</span><select value={documentId} onChange={event => setDocumentId(event.target.value)}><option value="">All documents</option>
            {documents.map(document => <option key={document.id} value={document.id}>{document.filename}</option>)}</select></label>
          <label><span>File type</span><select value={fileType} onChange={event => setFileType(event.target.value)}><option value="">All types</option>
            {formats.map(format => <option key={format} value={format}>{format.toUpperCase()}</option>)}</select></label>
          <button className="button button-primary" type="submit" disabled={loading}><Search size={16} />{loading ? 'Searching…' : 'Search workspace'}</button>
        </div>
      </form>
    </section>
    {error && <ErrorState message={error} />}
    {results && <section className="results-section"><div className="results-heading"><div><span className="eyebrow">RETRIEVAL RESULTS</span><h2>{results.length} relevant chunk{results.length !== 1 ? 's' : ''}</h2></div>
      <span className="score-explanation"><ArrowDownWideNarrow size={15} /> Cosine distance · lower is closer</span></div>
      {!results.length ? <EmptyState title="No matching indexed chunks" text="Index documents first, or try broadening your search and filters." /> :
        <div className="search-results">{results.map(result => <article className="panel search-result" key={result.chunk_id}>
          <div className="result-top"><span className="rank">#{result.rank}</span><div className="result-source"><Link to={`/documents/${result.document_id}`}>{result.filename} <ArrowUpRight size={13} /></Link>
            <span>{result.file_type.toUpperCase()} · chunk {result.chunk_index + 1} · <LocationText location={result.location} /></span></div>
            <span className="distance-score">distance {result.distance.toFixed(4)}</span></div>
          <pre className="result-text">{result.text}</pre><div className="result-footer"><code>{result.chunk_id}</code><span>Model: {result.embedding_model}</span></div>
        </article>)}</div>}
    </section>}
    {!results && !error && <div className="search-hint">Search uses the same configured sentence-transformer model as document indexing. Results show cosine distance, not a similarity percentage.</div>}
  </>
}
