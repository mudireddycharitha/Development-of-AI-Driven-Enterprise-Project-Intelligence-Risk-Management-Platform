import axios from 'axios'
import type { BlockerAnalysis, ChunkPageResult, ChunkRecord, DocumentPageResult, DocumentRecord, PageResult, PlatformStats, RiskAnalysis, ScopeAnalysis, SearchResult } from '../types'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000',
  timeout: 0,
})

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail.message === 'string') return detail.message
    if (Array.isArray(detail)) {
      const messages = detail.map(item => {
        if (!item || typeof item !== 'object') return ''
        const issue = item as { loc?: unknown[]; msg?: unknown }
        const location = Array.isArray(issue.loc) ? issue.loc.join('.') : ''
        const message = typeof issue.msg === 'string' ? issue.msg : ''
        return [location, message].filter(Boolean).join(': ')
      }).filter(Boolean)
      if (messages.length) return messages.join('; ')
    }
    return error.message || 'The API request failed.'
  }
  return error instanceof Error ? error.message : 'An unexpected error occurred.'
}

export const apiService = {
  stats: async () => (await api.get<PlatformStats>('/stats')).data,
  health: async () => (await api.get<Record<string, unknown>>('/health')).data,
  formats: async () => (await api.get<{ formats: Record<string, string> }>('/supported-formats')).data.formats,
  documents: async (params: Record<string, string | number | undefined> = {}) =>
    (await api.get<DocumentPageResult>('/documents', { params })).data,
  document: async (id: string) => (await api.get<DocumentRecord & { extracted_text: string }>(`/documents/${id}`)).data,
  documentText: async (id: string) => (await api.get<{ text: string; filename: string }>(`/documents/${id}/text`)).data,
  documentChunks: async (id: string, params: Record<string, number> = {}) =>
    (await api.get<PageResult<ChunkRecord>>(`/documents/${id}/chunks`, { params })).data,
  upload: async (files: File[], onProgress?: (progress: number) => void) => {
    const data = new FormData()
    files.forEach(file => data.append('files', file))
    return (await api.post<{ results: Array<{ document_id?: string; filename: string; status: string; error?: string; chunk_count?: number; duplicate?: boolean }> }>(
      '/documents/upload',
      data,
      { onUploadProgress: event => onProgress?.(event.total ? Math.round(event.loaded * 100 / event.total) : 0) },
    )).data
  },
  reprocess: async (id: string) => (await api.post(`/documents/${id}/reprocess`)).data,
  removeDocument: async (id: string) => api.delete(`/documents/${id}`),
  chunks: async (params: Record<string, string | number | undefined> = {}) =>
    (await api.get<ChunkPageResult>('/chunks', { params })).data,
  chunk: async (id: string) => (await api.get<ChunkRecord>(`/chunks/${id}`)).data,
  embedding: async (id: string, full = false) =>
    (await api.get<{ dimensions: number; preview: number[]; vector?: number[]; embedding_model: string; filename: string }>(
      `/chunks/${id}/embedding`, { params: { full } },
    )).data,
  search: async (query: string, top_k: number, document_id?: string, file_type?: string) =>
    (await api.post<{ results: SearchResult[] }>('/search', { query, top_k, document_id, file_type })).data.results,
  scopeAnalysis: async (document_ids?: string[]) =>
    (await api.post<ScopeAnalysis>('/analysis/scope', { document_ids })).data,
  riskAnalysis: async (document_ids?: string[]) =>
    (await api.post<RiskAnalysis>('/analysis/risks', { document_ids })).data,
  blockerAnalysis: async (document_ids?: string[]) =>
    (await api.post<BlockerAnalysis>('/analysis/blockers', { document_ids })).data,
}
