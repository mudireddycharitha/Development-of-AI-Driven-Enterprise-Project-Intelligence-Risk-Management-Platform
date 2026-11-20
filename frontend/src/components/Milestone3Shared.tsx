import { Link } from 'react-router-dom'
import { ArrowUpRight, FileText } from 'lucide-react'
import type { EvidenceReference } from '../types'

export function EvidenceReferenceCard({ evidence }: { evidence: EvidenceReference }) {
  const source = evidence.filename ?? evidence.source ?? 'Source document'
  const destination = evidence.document_id
    ? `/documents/${encodeURIComponent(evidence.document_id)}${evidence.chunk_id ? `?chunk=${encodeURIComponent(evidence.chunk_id)}` : ''}`
    : null
  const location = evidence.location && Object.keys(evidence.location).length > 0
    ? Object.entries(evidence.location).map(([key, value]) => `${key.replaceAll('_', ' ')} ${value}`).join(' · ')
    : ''
  return <details className="m3-evidence">
    <summary className="m3-evidence-heading"><FileText size={14} />
      {destination ? <Link to={destination}>{source} <ArrowUpRight size={12} /></Link> : <span>{source}</span>}
      {location && <small>{location}</small>}
    </summary>
    {evidence.excerpt && <blockquote>{evidence.excerpt
      .replace(/<svg\b[^>]*>[\s\S]*?<\/svg\s*>/gi, '')
      .replace(/<svg\b[^>]*/gi, '')
      .replace(/<\/?(?:script|style|path|g|text|foreignObject)\b[^>]*>/gi, '')
      .replace(/<[^>]{1,300}>/g, '')
      .replace(/&lt;\s*svg\b[^&]*(?:&gt;|$)/gi, '')
      .replace(/^\s*(?:svg|svg icon|svg label)\s*$/gim, '')}</blockquote>}
  </details>
}
