import { useEffect, useRef, useState } from 'react'
import { Check, CloudUpload, FilePlus2, FileText, LoaderCircle, RotateCcw, Trash2, Upload } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { apiService, errorMessage } from '../services/api'
import { formatBytes, PageHeading } from '../components/Shared'

type UploadOutcome = { document_id?: string; filename: string; status: string; error?: string; chunk_count?: number; duplicate?: boolean }

export function UploadDocuments() {
  const input = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState(0)
  const [error, setError] = useState('')
  const [outcomes, setOutcomes] = useState<UploadOutcome[]>([])
  const [retrying, setRetrying] = useState<string | null>(null)
  const [supportedFormats, setSupportedFormats] = useState<string[]>([])
  const retryFiles = useRef(new Map<string, File>())

  useEffect(() => {
    apiService.formats().then(formats => setSupportedFormats(Object.keys(formats).map(format => format.replace(/^\./, '').toUpperCase()).sort()))
      .catch(e => setError(`Could not load supported file formats: ${errorMessage(e)}`))
  }, [])

  const addFiles = (incoming: FileList | File[]) => {
    setError('')
    const next = [...files]
    for (const file of Array.from(incoming)) {
      if (!next.some(existing => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified)) next.push(file)
    }
    if (next.length > 25) setError('A batch can contain no more than 25 files.')
    else setFiles(next)
  }
  const upload = async (batch = files) => {
    if (!batch.length || busy) return
    batch.forEach(file => retryFiles.current.set(file.name, file))
    setBusy(true); setProgress(0); setError(''); setOutcomes([])
    try {
      const result = await apiService.upload(batch, setProgress)
      setOutcomes(result.results)
      setFiles(current => current.filter(file => !batch.includes(file)))
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false); setProgress(100) }
  }
  const retry = async (outcome: UploadOutcome) => {
    if (outcome.document_id) {
      setRetrying(outcome.filename); setError('')
      try {
        await apiService.reprocess(outcome.document_id)
        setOutcomes(current => current.map(item => item.filename === outcome.filename ? { ...item, status: 'indexed', error: undefined } : item))
      } catch (e) { setError(`${outcome.filename}: ${errorMessage(e)}`) }
      finally { setRetrying(null) }
    } else {
      const file = retryFiles.current.get(outcome.filename)
      if (file) void upload([file])
      else setError(`The original file ${outcome.filename} is no longer selected. Add it again to retry.`)
    }
  }
  const successful = outcomes.filter(item => item.status === 'indexed').length
  const failed = outcomes.filter(item => item.status === 'failed').length
  return <>
    <PageHeading eyebrow="DOCUMENT INGESTION" title="Upload documents"
      description="Add a mixed-format batch. Each file is processed independently and indexed for semantic search." />
    <section className="panel upload-panel">
      <div className={`dropzone${dragging ? ' drag-active' : ''}`}
        onDragOver={event => { event.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={event => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files) }}>
        <span className="upload-symbol"><CloudUpload size={26} /></span>
        <h2>Drop files to add them to your workspace</h2>
        <p>Select files that the document-processing backend supports.</p>
        <button className="button button-secondary" onClick={() => input.current?.click()}><FilePlus2 size={16} /> Browse files</button>
        <input ref={input} type="file" multiple hidden onChange={event => { if (event.target.files) addFiles(event.target.files); event.target.value = '' }} />
        <div className="upload-limits">Maximum 25 files per batch · 50 MB per file · files remain on this device</div>
      </div>
      {supportedFormats.length > 0 && <div className="upload-format-list"><strong>Supported formats</strong><div>{supportedFormats.map(format => <span className="format-tag" key={format}>{format}</span>)}</div></div>}
      {error && <div className="inline-error">{error}</div>}
      {files.length > 0 && <div className="selection-area">
        <div className="section-toolbar"><div><strong>Selected files</strong><span>{files.length} file{files.length !== 1 ? 's' : ''} · {formatBytes(files.reduce((sum, file) => sum + file.size, 0))}</span></div>
          <button className="button button-quiet" disabled={busy} onClick={() => setFiles([])}><Trash2 size={15} /> Clear selection</button></div>
        <div className="selected-list">{files.map((file, index) => <div className="selected-file" key={`${file.name}-${file.lastModified}-${index}`}>
          <span className="file-icon"><FileText size={16} /></span><span className="selected-name"><strong>{file.name}</strong><small>{file.name.split('.').pop()?.toUpperCase()} · {formatBytes(file.size)}</small></span>
          <button aria-label={`Remove ${file.name}`} className="icon-button" disabled={busy} onClick={() => setFiles(current => current.filter((_, i) => i !== index))}><Trash2 size={16} /></button>
        </div>)}</div>
        {busy && <div className="progress-wrap"><div className="progress-label">Uploading and processing files <span>{progress}% uploaded</span></div><div className="progress-track"><div style={{ width: `${progress}%` }} /></div></div>}
        <div className="upload-actions"><button className="button button-primary" disabled={busy || !files.length} onClick={() => void upload()}>
          {busy ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}{busy ? 'Processing batch…' : `Upload ${files.length} file${files.length !== 1 ? 's' : ''}`}
        </button></div>
      </div>}
    </section>
    {outcomes.length > 0 && <section className="panel results-panel">
      <div className="panel-heading"><div><h2>Batch results</h2><p>{successful} indexed · {failed} failed</p></div>
        <button className="button button-quiet" onClick={() => navigate('/documents')}>View documents</button></div>
      <div className="upload-results">{outcomes.map((item, index) => <div className="result-row" key={`${item.filename}-${index}`}>
        <span className={`result-icon ${item.status === 'indexed' ? 'success' : 'failure'}`}>{item.status === 'indexed' ? <Check size={16} /> : <span>!</span>}</span>
        <div className="selected-name"><strong>{item.filename}</strong><small>{item.status === 'indexed'
          ? item.duplicate ? `Already uploaded; existing document reused · ${item.chunk_count ?? 0} chunks indexed` : `${item.chunk_count ?? 0} chunks indexed`
          : item.error ?? 'Processing failed'}</small></div>
        {item.status === 'failed' && <button className="button button-quiet" disabled={retrying === item.filename} onClick={() => void retry(item)}>
          {retrying === item.filename ? <LoaderCircle className="spin" size={15} /> : <RotateCcw size={15} />} Retry
        </button>}
      </div>)}</div>
    </section>}
    {!files.length && !outcomes.length && <div className="upload-empty-note">Your uploads are processed locally using the configured text extraction and embedding model.</div>}
  </>
}
