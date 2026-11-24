import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, CloudUpload, FileText, LoaderCircle, RefreshCcw, Trash2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import { formatBytes, PageHeading } from '../components/Shared'
import type { DocumentRecord } from '../types'

const categories = [
  'Meeting Notes',
  'Weekly Status Report',
  'Project Progress Update',
  'Requirements Update',
  'Risk Report',
  'Decision Record',
  'General Project Document',
] as const

type UploadOutcome = {
  document_id?: string
  filename: string
  status: string
  error?: string
  chunk_count?: number
  duplicate?: boolean
  document_version?: number
}

export function IncrementalUpload() {
  const input = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [category, setCategory] = useState<string>('Meeting Notes')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [projectName, setProjectName] = useState('')
  const [milestoneName, setMilestoneName] = useState('')
  const [busy, setBusy] = useState(false)
  const [uploadPercent, setUploadPercent] = useState(0)
  const [error, setError] = useState('')
  const [outcomes, setOutcomes] = useState<UploadOutcome[]>([])
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [retrying, setRetrying] = useState<string | null>(null)

  const loadDocuments = async () => {
    try {
      const page = await apiService.documents({ page: 1, page_size: 100 })
      setDocuments(page.items)
    } catch (requestError) {
      setError(errorMessage(requestError))
    }
  }

  useEffect(() => { void loadDocuments() }, [])

  const addFiles = (incoming: FileList | File[]) => {
    setError('')
    setFiles(previous => {
      const next = [...previous]
      for (const file of Array.from(incoming)) {
        if (!next.some(item => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) {
          next.push(file)
        }
      }
      return next
    })
  }

  const upload = async () => {
    if (!files.length || busy) return
    setBusy(true)
    setUploadPercent(0)
    setError('')
    setOutcomes([])
    try {
      const result = await apiService.incrementalUpload(files, {
        category,
        title: title.trim() || undefined,
        description: description.trim() || undefined,
        project_name: projectName.trim() || undefined,
        milestone_name: milestoneName.trim() || undefined,
      }, setUploadPercent)
      setOutcomes(result.results)
      setFiles([])
      await loadDocuments()
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setBusy(false)
      setUploadPercent(100)
    }
  }

  const retry = async (outcome: UploadOutcome) => {
    if (!outcome.document_id) return
    setRetrying(outcome.document_id)
    try {
      await apiService.reprocess(outcome.document_id)
      await loadDocuments()
      setOutcomes(current => current.map(item => item.document_id === outcome.document_id
        ? { ...item, status: 'indexed', error: undefined }
        : item))
    } catch (requestError) {
      setError(`${outcome.filename}: ${errorMessage(requestError)}`)
    } finally {
      setRetrying(null)
    }
  }

  const successful = documents.filter(item => item.processing_status === 'indexed').length
  const failed = documents.filter(item => item.processing_status === 'failed').length
  const newCount = documents.filter(item => item.change_type === 'new').length
  const updatedCount = documents.filter(item => item.change_type === 'updated').length
  const lastUpload = documents.length ? documents
    .map(item => item.uploaded_at)
    .filter(Boolean)
    .sort()
    .at(-1) : null

  return <>
    <PageHeading eyebrow="DOCUMENT INGESTION" title="Incremental Document Upload"
      description="Add current project information without reprocessing the existing knowledge base." />

    <section className="insights-metric-grid" aria-label="Incremental upload metrics">
      <Metric title="Total uploaded" value={documents.length} detail="Retained document records" />
      <Metric title="New documents" value={newCount} detail="Newly indexed sources" />
      <Metric title="Updated documents" value={updatedCount} detail="Preserved version updates" />
      <Metric title="Indexed successfully" value={successful} detail="Available for agents and search" />
      <Metric title="Failed" value={failed} detail="Retry is available" />
      <Metric title="Last upload" value={lastUpload ? new Date(lastUpload).toLocaleDateString() : 'None'} detail="Most recent upload date" />
    </section>

    <section className="panel upload-panel">
      <div className={`dropzone${dragging ? ' drag-active' : ''}`}
        onDragOver={event => { event.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={event => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files) }}>
        <span className="upload-symbol"><CloudUpload size={26} /></span>
        <h2>Add new project information</h2>
        <p>Meeting notes, weekly reports, updates, decisions, and revised plans are supported.</p>
        <button className="button button-secondary" onClick={() => input.current?.click()}><FileText size={16} /> Browse files</button>
        <input ref={input} type="file" multiple hidden onChange={event => {
          if (event.target.files) addFiles(event.target.files)
          event.target.value = ''
        }} />
      </div>

      <div className="insights-scope-grid" style={{ marginTop: 16 }}>
        <label className="field-label">Document category
          <select value={category} onChange={event => setCategory(event.target.value)}>
            {categories.map(item => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="field-label">Document title
          <input value={title} onChange={event => setTitle(event.target.value)} placeholder="Optional display title" />
        </label>
        <label className="field-label">Related project
          <input value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="Project name" />
        </label>
        <label className="field-label">Related milestone
          <input value={milestoneName} onChange={event => setMilestoneName(event.target.value)} placeholder="Milestone or phase" />
        </label>
      </div>
      <label className="field-label" style={{ display: 'block', marginTop: 12 }}>Description
        <textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="Optional context for this update" />
      </label>

      {error && <div className="inline-error">{error}</div>}
      {files.length > 0 && <div className="selection-area">
        <div className="section-toolbar"><div><strong>Selected files</strong><span>{files.length} file(s) · {formatBytes(files.reduce((sum, file) => sum + file.size, 0))}</span></div>
          <button className="button button-quiet" disabled={busy} onClick={() => setFiles([])}><Trash2 size={15} /> Clear</button></div>
        <div className="selected-list">{files.map((file, index) => <div className="selected-file" key={`${file.name}-${file.lastModified}`}>
          <span className="file-icon"><FileText size={16} /></span>
          <span className="selected-name"><strong>{file.name}</strong><small>{formatBytes(file.size)}</small></span>
          <button className="icon-button" aria-label={`Remove ${file.name}`} disabled={busy} onClick={() => setFiles(current => current.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={16} /></button>
        </div>)}</div>
        {busy && <div className="progress-wrap">
          <div className="progress-label">Uploading files <span>{uploadPercent}% uploaded</span></div>
          <div className="progress-track"><div style={{ width: `${uploadPercent}%` }} /></div>
        </div>}
        <button className="button button-primary" disabled={busy} onClick={() => void upload()}>
          {busy ? <LoaderCircle className="spin" size={16} /> : <CloudUpload size={16} />}
          {busy ? 'Processing incremental documents…' : 'Upload and process'}
        </button>
      </div>}
    </section>

    {outcomes.length > 0 && <section className="panel results-panel">
      <div className="panel-heading"><div><h2>Incremental upload results</h2><p>Only new or changed content is indexed.</p></div></div>
      <div className="upload-results">{outcomes.map((item, index) => <div className="result-row" key={`${item.filename}-${index}`}>
        <span className={`result-icon ${item.status === 'indexed' || item.status === 'updated' ? 'success' : 'failure'}`}>
          {item.status === 'indexed' || item.status === 'updated' ? <CheckCircle2 size={16} /> : '!'}
        </span>
        <div className="selected-name"><strong>{item.filename}</strong><small>
          {item.duplicate ? 'Unchanged duplicate; existing embedding reused'
            : item.status === 'updated' ? `Updated to version ${item.document_version ?? ''}; previous version retained`
              : item.status === 'indexed' ? `${item.chunk_count ?? 0} chunks indexed`
                : item.error || 'Processing failed'}
        </small></div>
        {item.status === 'failed' && <button className="button button-quiet" disabled={retrying === item.document_id} onClick={() => void retry(item)}>
          <RefreshCcw size={15} /> Retry
        </button>}
      </div>)}</div>
    </section>}

    <section className="panel">
      <div className="panel-heading"><div><h2>Document activity</h2><p>Indexed versions and retained processing history.</p></div>
        <Link className="text-link" to="/documents">View all documents</Link></div>
      {documents.length === 0 ? <p className="upload-empty-note">No documents have been uploaded.</p>
        : <div className="upload-results">{documents.map(document => <div className="result-row" key={document.id}>
          <span className="file-icon"><FileText size={16} /></span>
          <div className="selected-name"><strong>{document.document_title || document.filename}</strong>
            <small>{document.document_category || 'General Project Document'} · v{document.document_version || 1} · {document.chunk_count ?? 0} chunks · {document.change_type || 'new'}</small></div>
          <span className={`status-badge status-${document.processing_status}`}>{document.processing_status}</span>
          <Link className="text-link" to={`/documents/${document.id}`}>Details</Link>
        </div>)}</div>}
    </section>
  </>
}

function Metric({ title, value, detail }: { title: string; value: number | string; detail: string }) {
  return <article className="panel insights-dimension"><div><h2>{title}</h2><strong>{value}</strong></div><p>{detail}</p></article>
}
