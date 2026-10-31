import type { ReactNode } from 'react'
import { AlertCircle, LoaderCircle, SearchX } from 'lucide-react'

export function PageHeading({ eyebrow, title, description, action }: {
  eyebrow?: string; title: string; description?: string; action?: ReactNode
}) {
  return <div className="page-heading">
    <div><div className="eyebrow">{eyebrow ?? 'INTELLIGENCE WORKSPACE'}</div><h1>{title}</h1>
      {description && <p>{description}</p>}</div>
    {action && <div className="heading-action">{action}</div>}
  </div>
}

export function StatusBadge({ status }: { status?: string }) {
  const label = (status ?? 'unknown').replaceAll('_', ' ')
  return <span className={`status-badge status-${status ?? 'unknown'}`}><i />{label}</span>
}

export function Loading({ label = 'Loading workspace data…' }: { label?: string }) {
  return <div className="state-card"><LoaderCircle className="spin" size={22} />{label}</div>
}

export function ErrorState({ message }: { message: string }) {
  return <div className="state-card error-state"><AlertCircle size={20} /><span>{message}</span></div>
}

export function EmptyState({ title, text }: { title: string; text: string }) {
  return <div className="state-card empty-state"><SearchX size={25} /><strong>{title}</strong><span>{text}</span></div>
}

export function LocationText({ location }: { location?: Record<string, string | number> }) {
  if (!location || !Object.keys(location).length) return <span className="muted">—</span>
  return <span className="location-text">{Object.entries(location).map(([key, value]) =>
    `${key.replaceAll('_', ' ')} ${value}`).join(' · ')}</span>
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function formatDate(value: string): string {
  return new Date(value).toLocaleString()
}

export function Pagination({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  if (pages <= 1) return null
  return <div className="pagination">
    <button className="button button-quiet" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
    <span>Page {page} of {pages}</span>
    <button className="button button-quiet" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
  </div>
}
