import { useEffect, useMemo, useState } from 'react'
import { Download, FileOutput, RefreshCw, Sparkles } from 'lucide-react'
import { EvidenceReferenceCard } from '../components/Milestone3Shared'
import { EmptyState, ErrorState, Loading, PageHeading } from '../components/Shared'
import { apiService, errorMessage } from '../services/api'
import type { DocumentationResult, DocumentRecord, EvidenceReference } from '../types'

type DocumentationTab = 'stories' | 'risks' | 'actions' | 'decisions'

const tabs: Array<{ key: DocumentationTab; label: string }> = [
  { key: 'stories', label: 'User Stories' },
  { key: 'risks', label: 'Risk Register' },
  { key: 'actions', label: 'Action Items' },
  { key: 'decisions', label: 'Pending Decisions' },
]

export function DocumentationGeneration() {
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [indexedDocumentTotal, setIndexedDocumentTotal] = useState(0)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [result, setResult] = useState<DocumentationResult | null>(null)
  const [activeTab, setActiveTab] = useState<DocumentationTab>('stories')
  const [loadingSources, setLoadingSources] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const selectionKey = useMemo(() => [...selectedIds].sort().join(','), [selectedIds])

  useEffect(() => {
    let active = true
    apiService.documents({ page: 1, page_size: 100, status: 'indexed' })
      .then(async page => {
        if (!active) return
        setDocuments(page.items)
        setIndexedDocumentTotal(page.total)
        const ids = page.items.map(document => document.id)
        setSelectedIds(ids)
        if (ids.length) {
          const saved = await apiService.documentationLatest(ids)
          if (active) setResult(saved)
        }
      })
      .catch(fetchError => { if (active) setError(errorMessage(fetchError)) })
      .finally(() => { if (active) setLoadingSources(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (loadingSources) return
    if (!selectedIds.length) {
      setResult(null)
      return
    }
    let active = true
    apiService.documentationLatest(selectedIds)
      .then(saved => { if (active) setResult(saved) })
      .catch(fetchError => { if (active) setError(errorMessage(fetchError)) })
    return () => { active = false }
  }, [selectionKey, loadingSources])

  const toggleDocument = (id: string) => {
    setNotice('')
    setSelectedIds(previous => previous.includes(id)
      ? previous.filter(item => item !== id)
      : [...previous, id])
  }

  const generate = async () => {
    if (!selectedIds.length) {
      setError('Select at least one indexed source document.')
      return
    }
    setGenerating(true)
    setError('')
    setNotice('')
    try {
      const generated = await apiService.generateDocumentation(selectedIds)
      setResult(generated)
      setNotice(`Generated ${generated.total_generated_items} evidence-linked item(s). Re-running for this selection updates the saved result instead of creating duplicates.`)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setGenerating(false)
    }
  }

  const exportResult = async (format: 'csv' | 'json') => {
    if (!result) return
    setExporting(true)
    setError('')
    try {
      const blob = await apiService.documentationExport(result.id, format)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `project-documentation.${format}`
      link.click()
      URL.revokeObjectURL(url)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setExporting(false)
    }
  }

  const itemCount = result
    ? result.user_stories.length + result.risk_register.length + result.action_items.length + result.pending_decisions.length
    : 0

  return <div className="m3-page">
    <PageHeading eyebrow="PROJECT INTELLIGENCE" title="Documentation Generation"
      description="Turn indexed project evidence into traceable user stories, risks, actions, and pending decisions."
      action={<button className="button button-primary" onClick={() => void generate()} disabled={generating || loadingSources || selectedIds.length === 0}>
        {generating ? <><RefreshCw size={15} className="spin" /> Generating…</> : <><Sparkles size={15} /> Generate documentation</>}
      </button>} />

    {error && <ErrorState message={error} />}
    {notice && <div className="m3-notice" role="status">{notice}</div>}
    <section className="panel m3-source-panel">
      <div className="panel-heading"><div><h2><FileOutput size={16} /> Source documents</h2>
        <p>Only indexed documents can be selected. Extracted evidence is linked back to its source.</p></div>
        <button className="button button-quiet compact-button" disabled={!documents.length}
          onClick={() => setSelectedIds(selectedIds.length === documents.length ? [] : documents.map(item => item.id))}>
          {selectedIds.length === documents.length ? 'Clear selection' : 'Select all'}
        </button>
      </div>
      {loadingSources ? <Loading label="Loading indexed source documents…" />
        : documents.length === 0 ? <EmptyState title="No indexed documents" text="Upload and index project documents before generating documentation." />
          : <>{indexedDocumentTotal > documents.length && <p className="m3-source-limit-note">Showing the first {documents.length} indexed documents of {indexedDocumentTotal}. Generation is limited to 100 selected documents per run.</p>}
            <div className="m3-document-list">{documents.map(item =>
            <label key={item.id} className="m3-document-option"><input type="checkbox" checked={selectedIds.includes(item.id)} onChange={() => toggleDocument(item.id)} />
              <span><strong>{item.filename}</strong><small>{item.file_type.toUpperCase()} · {item.indexed_chunk_count ?? 0} indexed chunks</small></span>
            </label>)}</div></>}
    </section>

    {result && <section className="panel">
      <div className="panel-heading m3-results-heading"><div><h2>Generated project documentation</h2>
        <p>{itemCount} total item(s) · {result.source_documents.length} source document(s) · Updated {new Date(result.generated_at).toLocaleString()}</p></div>
        <div className="m3-export-actions">
          <button className="button button-quiet compact-button" onClick={() => void exportResult('csv')} disabled={exporting}><Download size={14} /> CSV</button>
          <button className="button button-quiet compact-button" onClick={() => void exportResult('json')} disabled={exporting}><Download size={14} /> JSON</button>
        </div>
      </div>
      <div className="m3-tab-list" role="tablist" aria-label="Generated documentation">
        {tabs.map(tab => {
          const count = tab.key === 'stories' ? result.user_stories.length
            : tab.key === 'risks' ? result.risk_register.length
              : tab.key === 'actions' ? result.action_items.length : result.pending_decisions.length
          return <button role="tab" aria-selected={activeTab === tab.key} key={tab.key}
            className={`m3-tab${activeTab === tab.key ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.key)}>{tab.label}<span>{count}</span></button>
        })}
      </div>
      <div className="m3-generated-list">
        {activeTab === 'stories' && result.user_stories.map(item => <article className="m3-generated-card" key={item.id}>
          <div className="m3-card-heading"><h3>{item.title}</h3><span>{item.priority ?? 'Priority not stated'}</span></div>
          <p className="m3-story">{item.description}</p>
          <div className="m3-facts"><span>Role: {item.user_role}</span><span>Requirement: {item.requirement}</span><span>Benefit: {item.expected_benefit}</span><span>Status: {item.status}</span></div>
          {item.acceptance_criteria.length > 0 && <div className="m3-subsection"><strong>Documented acceptance criteria</strong><ul>{item.acceptance_criteria.map((criterion, index) => <li key={`${item.id}-criterion-${index}`}>{criterion}</li>)}</ul></div>}
          <Evidence evidence={item.evidence} source={item.source_document} />
        </article>)}
        {activeTab === 'risks' && result.risk_register.map(item => <article className="m3-generated-card" key={item.id}>
          <div className="m3-card-heading"><h3>{item.title}</h3><span className={`risk-level risk-${item.risk_level.toLowerCase()}`}>{item.risk_level}{item.inference ? ' · inferred' : ''}</span></div>
          <p>{item.description}</p>
          <div className="m3-facts"><span>Category: {item.category}</span><span>Likelihood: {item.likelihood}</span><span>Impact: {item.impact}</span><span>Owner: {item.responsible_person ?? 'Not stated'}</span></div>
          <p className="m3-mitigation"><strong>Suggested mitigation:</strong> {item.suggested_mitigation}</p>
          <Evidence evidence={item.evidence} source={item.source_document} />
        </article>)}
        {activeTab === 'actions' && result.action_items.map(item => <article className="m3-generated-card" key={item.id}>
          <div className="m3-card-heading"><h3>{item.description}</h3><span>{item.priority ?? 'Priority not stated'}</span></div>
          <div className="m3-facts"><span>Assigned to: {item.assignee ?? 'Not stated'}</span><span>Due: {item.due_date ?? 'Not stated'}</span><span>Status: {item.status}</span><span>Milestone: {item.related_project_milestone ?? 'Not stated'}</span></div>
          <Evidence evidence={item.evidence} source={item.source_document} />
        </article>)}
        {activeTab === 'decisions' && result.pending_decisions.map(item => <article className="m3-generated-card" key={item.id}>
          <div className="m3-card-heading"><h3>{item.description}</h3><span>Pending</span></div>
          <div className="m3-facts"><span>Decision owner: {item.responsible_person ?? 'Not stated'}</span><span>Project area: {item.related_project_area ?? 'Not stated'}</span></div>
          <Evidence evidence={item.evidence} source={item.source_document} />
        </article>)}
        {((activeTab === 'stories' && !result.user_stories.length)
          || (activeTab === 'risks' && !result.risk_register.length)
          || (activeTab === 'actions' && !result.action_items.length)
          || (activeTab === 'decisions' && !result.pending_decisions.length))
          && <EmptyState title="No grounded items found" text="The selected indexed documents did not contain evidence that met this category's extraction rules. No placeholder items were added." />}
      </div>
    </section>}
    {!result && !loadingSources && documents.length > 0 && <EmptyState title="No saved generation for this selection" text="Generate documentation to extract supported items. Results for this document selection will be saved for later." />}
  </div>
}

function Evidence({ evidence, source }: { evidence: EvidenceReference; source: string }) {
  return <div className="m3-source"><EvidenceReferenceCard evidence={{ ...evidence, filename: source }} /></div>
}
