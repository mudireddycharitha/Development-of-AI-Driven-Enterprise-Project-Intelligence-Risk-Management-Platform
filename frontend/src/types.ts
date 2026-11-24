export type ProcessingStatus = 'uploaded' | 'validating' | 'extracting' | 'chunking' | 'embedding' | 'indexed' | 'failed'

export interface DocumentRecord {
  id: string
  filename: string
  file_type: string
  file_size: number
  uploaded_at: string
  updated_at?: string
  processing_status: ProcessingStatus
  extraction_error?: string | null
  failed_stage?: string | null
  chunk_count: number
  indexed_chunk_count: number | null
  document_version?: number
  version_of_document_id?: string | null
  is_current_version?: boolean | number
  document_category?: string
  document_title?: string | null
  document_description?: string | null
  project_name?: string | null
  milestone_name?: string | null
  change_type?: 'new' | 'updated' | 'unchanged' | 'failed'
  last_processed_at?: string | null
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

export interface SystemHealth {
  api_status: 'online'
  status: 'healthy' | 'degraded'
  database: 'healthy' | 'unavailable'
  vector_store: 'healthy' | 'unavailable'
  indexed_vectors: number | null
  embedding_model: string
  ai_provider: {
    provider: string
    model: string
    status: 'configured' | 'operational' | 'unavailable'
    configured: boolean
    operational: boolean | null
    connectivity: 'reachable' | 'unavailable' | 'not_checked' | 'not_applicable'
    message: string
  }
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
  owner?: string
  source?: string
  evidence: EvidenceReference[]
}

export interface MainProjectGoal {
  title: string
  statement: string
  problem?: string | null
  expected_outcome?: string | null
  evidence: EvidenceReference[]
  problem_evidence: EvidenceReference[]
  outcome_evidence: EvidenceReference[]
}

export interface SupportingObjective {
  id: string
  title: string
  description: string
  contribution: string
  related_deliverable?: string | null
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
  main_project_goal?: MainProjectGoal | null
  supporting_objectives?: SupportingObjective[]
  scope: string[]
  deliverables: string[]
  milestones: ScopeMilestone[]
  project_start_date?: string | null
  project_end_date?: string | null
  responsibilities: ScopeResponsibility[]
  dependencies: ScopeDependency[]
  progress: string[]
  findings: ScopeFinding[]
  missing_information: string[]
  source_documents: string[]
  notes: string[]
  processing?: {
    agent_status: string
    processing_duration_ms: number
    evidence_chunks_retrieved: number
    documents_analyzed: number
  }
}

export interface RiskFinding {
  id: string
  title: string
  level: 'Low' | 'Medium' | 'High' | 'Critical' | null
  description: string
  category?: string | null
  impact?: string | null
  likelihood?: string | null
  probability_score?: number | null
  impact_score?: number | null
  severity_score?: number | null
  severity_reason?: string | null
  insufficient_evidence?: string[]
  recommended_mitigation?: string | null
  owner?: string | null
  affected_milestones: string[]
  related_project_goal?: string | null
  affected_objectives?: string[]
  evidence: EvidenceReference[]
  recommendations: string[]
  forecast?: string | null
  inference: boolean
}

export interface RiskAnalysis {
  findings: RiskFinding[]
  severity_counts: Record<'Critical' | 'High' | 'Medium' | 'Low' | 'Unrated', number>
  missing_information: string[]
  source_documents: string[]
  rules_used: string[]
  notes: string[]
  processing?: {
    agent_status: string
    processing_duration_ms: number
    evidence_chunks_retrieved: number
    documents_analyzed: number
  }
}

export interface BlockerItem {
  id: string
  description: string
  category: string
  status: string | null
  assignee?: string | null
  due_date?: string | null
  priority?: string | null
  related_task?: string | null
  related_milestone?: string | null
  related_project_goal?: string | null
  affected_objectives?: string[]
  source_document?: string | null
  evidence: EvidenceReference[]
  recommendation?: string | null
}

export interface BlockerAnalysis {
  blockers: BlockerItem[]
  unresolved_issues: BlockerItem[]
  pending_decisions: BlockerItem[]
  action_items: BlockerItem[]
  missing_information: string[]
  source_documents: string[]
  notes: string[]
  processing?: {
    agent_status: string
    processing_duration_ms: number
    evidence_chunks_retrieved: number
    documents_analyzed: number
  }
}

export interface GeneratedUserStory {
  id: string
  user_role: string
  requirement: string
  expected_benefit: string
  title: string
  description: string
  priority: string | null
  acceptance_criteria: string[]
  source_document: string
  evidence: EvidenceReference
  status: string
}

export interface GeneratedRisk {
  id: string
  title: string
  description: string
  category: string
  likelihood: string | null
  impact: string | null
  risk_level: string | null
  probability_score?: number | null
  impact_score?: number | null
  severity_score?: number | null
  severity_reason?: string | null
  insufficient_evidence?: string[]
  suggested_mitigation: string | null
  responsible_person: string | null
  source_document: string
  evidence: EvidenceReference | null
  inference?: boolean
}

export interface GeneratedActionItem {
  id: string
  description: string
  assignee: string | null
  due_date: string | null
  priority: string | null
  status: string
  related_project_milestone: string | null
  source_document: string
  evidence: EvidenceReference
}

export interface GeneratedDecision {
  id: string
  description: string
  responsible_person: string | null
  related_project_area: string | null
  source_document: string
  evidence: EvidenceReference
}

export interface DocumentationResult {
  id: string
  generated_at: string
  document_ids: string[]
  source_documents: string[]
  user_stories: GeneratedUserStory[]
  risk_register: GeneratedRisk[]
  action_items: GeneratedActionItem[]
  pending_decisions: GeneratedDecision[]
  total_generated_items: number
}

export interface ProjectHealthDimension {
  name: string
  score: number | null
  available: boolean
  explanation: string
  factors: string[]
}

export interface ProjectHealthResult {
  id: string
  calculated_at: string
  overall_score: number | null
  classification: 'Healthy' | 'Needs Attention' | 'At Risk' | 'Insufficient Data'
  dimensions: {
    scope_clarity: ProjectHealthDimension
    timeline_health: ProjectHealthDimension
    blocker_health: ProjectHealthDimension
  }
  weights: Record<string, number>
  identified_risks: RiskFinding[]
  unresolved_blockers: BlockerItem[]
  pending_decisions: BlockerItem[]
  delayed_milestones: ScopeMilestone[]
  recommended_actions: string[]
  document_count: number
}

export interface ProjectInsightRisk extends RiskFinding {
  category: string
  probability: string | null
  impact: string | null
  mitigation: string | null
  source_document: string | null
}

export interface ProjectInsightAction extends BlockerItem {
  status: 'pending' | 'completed' | 'overdue'
  source_status?: string | null
}

export interface ProjectInsightsMetrics {
  total_uploaded_documents: number
  processed_documents: number
  current_indexed_documents: number
  total_risks: number
  total_blockers: number
  total_actions: number
  total_deliverables: number
  overall_project_health_score: number | null
  documentation_generated_items: number
}

export interface ProjectInsightsDocumentation {
  is_current: boolean
  generation_id: string | null
  generated_at: string | null
  source_documents: string[]
  counts: {
    user_stories: number
    risk_register: number
    action_items: number
    pending_decisions: number
  }
  total_generated_items: number
}

export interface ProjectInsightsResult {
  generated_at: string
  project_status: string
  metrics: ProjectInsightsMetrics
  health: ProjectHealthResult
  risks: ProjectInsightRisk[]
  blockers: BlockerItem[]
  actions: ProjectInsightAction[]
  documentation: ProjectInsightsDocumentation
  scope: Pick<ScopeAnalysis, 'goals' | 'objectives' | 'deliverables' | 'milestones' | 'responsibilities' | 'dependencies' | 'progress' | 'missing_information' | 'source_documents'>
  action_counts: { total: number; pending: number; completed: number; overdue: number }
  risk_counts: Record<'Critical' | 'High' | 'Medium' | 'Low' | 'Unrated', number>
  blocker_counts: { total: number; unresolved: number; resolved: number; pending_decisions: number }
  milestone_counts: {
    total: number
    completed: number
    in_progress: number
    delayed: number
    blocked: number
    at_risk: number
    pending: number
    not_stated: number
  }
}

export interface AssistantSource {
  document_id: string
  filename: string
  chunk_id: string
  excerpt: string
  location: Record<string, string | number>
  distance: number | null
}

export interface AssistantMessage {
  role: 'user' | 'assistant'
  content: string
  sources: AssistantSource[]
  answer_mode?: AssistantResponse['answer_mode']
  health?: ProjectHealthResult | null
  created_at: string
}

export interface AssistantResponse {
  session_id: string
  question: string
  answer: string
  answer_mode: 'llm' | 'health_analysis' | 'extractive' | 'insufficient_evidence'
  sources: AssistantSource[]
  health: ProjectHealthResult | null
  created_at: string
}
