export type ProcessingStatus = 'uploaded' | 'validating' | 'extracting' | 'chunking' | 'embedding' | 'indexed' | 'failed'

export interface DocumentRecord {
  id: string
  filename: string
  file_type: string
  file_size: number
  uploaded_at: string
  processing_status: ProcessingStatus
  extraction_error?: string | null
  failed_stage?: string | null
  chunk_count: number
  indexed_chunk_count: number | null
}

export interface ChunkRecord {
  id: string
  document_id: string
  filename: string
  file_type: string
  chunk_index: number
  text: string
  token_count: number
  location: Record<string, string | number>
  embedding_status: string
  indexing_status: string
  embedding_dimensions?: number | null
  embedding_model?: string | null
}

export interface IndexedChunkSummary {
  indexed_chunk_total: number
  unassociated_indexed_chunk_count: number
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export type DocumentPageResult = PageResult<DocumentRecord> & IndexedChunkSummary
export type ChunkPageResult = PageResult<ChunkRecord> & IndexedChunkSummary

export interface PlatformStats {
  total_documents: number
  successful_documents: number
  failed_documents: number
  processing_documents: number
  total_chunks: number
  indexed_chunks: number | null
  recent_documents: DocumentRecord[]
  supported_formats: string[]
  backend_status: string
  vector_store_status: string
  vector_count: number | null
}

export interface SearchResult {
  rank: number
  chunk_id: string
  document_id: string
  filename: string
  file_type: string
  chunk_index: number
  text: string
  location: Record<string, string | number>
  distance: number
  embedding_model: string
}

export interface EvidenceReference {
  document_id?: string
  filename?: string
  chunk_id?: string
  excerpt: string
  location: Record<string, string | number>
  source?: string
}

export interface ScopeMilestone {
  name?: string
  date?: string
  status?: string
  description?: string
  source?: string
  evidence: EvidenceReference[]
}

export interface ScopeDependency {
  from_task?: string
  to_task?: string
  description?: string
  source?: string
  evidence: EvidenceReference[]
}

export interface ScopeResponsibility {
  task?: string
  owner?: string
  role?: string
  status?: string
  source?: string
  evidence: EvidenceReference[]
}

export interface ScopeFinding {
  id: string
  category: string
  title: string
  description: string
  related_milestone?: string | null
  related_task?: string | null
  related_deliverable?: string | null
  evidence: EvidenceReference[]
}

export interface ScopeAnalysis {
  goals: string[]
  objectives: string[]
  deliverables: string[]
  milestones: ScopeMilestone[]
  responsibilities: ScopeResponsibility[]
  dependencies: ScopeDependency[]
  progress: string[]
  findings: ScopeFinding[]
  missing_information: string[]
  source_documents: string[]
  notes: string[]
}

export interface RiskFinding {
  id: string
  title: string
  level: 'Low' | 'Medium' | 'High' | 'Critical'
  description: string
  affected_milestones: string[]
  evidence: EvidenceReference[]
  recommendations: string[]
  forecast?: string | null
  inference: boolean
}

export interface RiskAnalysis {
  findings: RiskFinding[]
  missing_information: string[]
  source_documents: string[]
  rules_used: string[]
}

export interface BlockerItem {
  id: string
  description: string
  category: string
  status: string
  assignee?: string | null
  due_date?: string | null
  priority?: string | null
  related_task?: string | null
  related_milestone?: string | null
  source_document?: string | null
  evidence: EvidenceReference[]
  recommendation?: string | null
}

export interface BlockerAnalysis {
  blockers: BlockerItem[]
  pending_decisions: BlockerItem[]
  action_items: BlockerItem[]
  missing_information: string[]
  source_documents: string[]
  notes: string[]
}
