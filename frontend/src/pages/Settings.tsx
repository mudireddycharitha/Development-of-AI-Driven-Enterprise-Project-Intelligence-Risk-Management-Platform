import { useEffect, useState } from 'react'
import { Activity, Database, ExternalLink, HardDrive, RefreshCw, Server } from 'lucide-react'
import { apiService, errorMessage } from '../services/api'
import { ErrorState, Loading, PageHeading, StatusBadge } from '../components/Shared'

type Health = { status?: string; database?: string; vector_store?: string; indexed_vectors?: number | null; embedding_model?: string }

export function SettingsPage() {
  const [health, setHealth] = useState<Health | null>(null)
  const [formats, setFormats] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const load = () => {
    setLoading(true); setError('')
    Promise.all([apiService.health(), apiService.formats()])
      .then(([status, supported]) => { setHealth(status as Health); setFormats(supported) })
      .catch(e => setError(errorMessage(e))).finally(() => setLoading(false))
  }
  useEffect(load, [])
  return <>
    <PageHeading eyebrow="CONFIGURATION" title="System status" description="Inspect local service health, model configuration, and available file parsers."
      action={<button className="button button-quiet" onClick={load}><RefreshCw size={15} /> Refresh status</button>} />
    {loading && <Loading label="Checking backend and vector store…" />}{error && <ErrorState message={error} />}
    {health && <>
      <div className="status-grid">
        <section className="panel status-card"><span className="status-icon"><Server size={20} /></span><div><span>API service</span><StatusBadge status={health.status} /></div><p>FastAPI running at <code>http://127.0.0.1:8000</code></p><a href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">Open Swagger UI <ExternalLink size={13} /></a></section>
        <section className="panel status-card"><span className="status-icon"><Database size={20} /></span><div><span>Metadata database</span><StatusBadge status={health.database} /></div><p>SQLite stores document, chunk, and processing metadata.</p></section>
        <section className="panel status-card"><span className="status-icon"><HardDrive size={20} /></span><div><span>Vector store</span><StatusBadge status={health.vector_store} /></div><p>{health.indexed_vectors ?? 'Unavailable'} persisted vectors in the local Chroma collection.</p></section>
      </div>
      <section className="panel model-panel"><div className="panel-heading"><div><h2><Activity size={17} /> Embedding model</h2><p>The model loads on first indexing or search request and may need an initial download.</p></div></div>
        <div className="model-name">{health.embedding_model ?? 'Not reported'}</div>
        <p className="muted-text">Configure the model with <code>EMBEDDING_MODEL</code>. The selected model is shared between chunk and query embeddings.</p>
      </section>
      <section className="panel formats-panel"><div className="panel-heading"><div><h2>Supported formats</h2><p>Extension list advertised by the parser service. Legacy .doc/.ppt require LibreOffice.</p></div><span className="dimension-pill">{Object.keys(formats).length} formats</span></div>
        <div className="format-grid">{Object.entries(formats).sort(([a], [b]) => a.localeCompare(b)).map(([extension, label]) =>
          <div className="format-item" key={extension}><code>{extension}</code><span>{label}</span></div>)}</div>
      </section>
    </>}
  </>
}
