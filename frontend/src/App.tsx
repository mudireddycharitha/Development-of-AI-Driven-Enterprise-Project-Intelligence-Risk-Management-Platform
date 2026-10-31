import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppLayout } from './layouts/AppLayout'
import { Dashboard } from './pages/Dashboard'
import { UploadDocuments } from './pages/UploadDocuments'
import { Documents } from './pages/Documents'
import { DocumentDetails } from './pages/DocumentDetails'
import { ChunkExplorer } from './pages/ChunkExplorer'
import { EmbeddingsExplorer } from './pages/EmbeddingsExplorer'
import { SemanticSearch } from './pages/SemanticSearch'
import { SettingsPage } from './pages/Settings'
import { ProjectIntelligence } from './pages/ProjectIntelligence'

export default function App() {
  return <BrowserRouter><Routes><Route element={<AppLayout />}>
    <Route index element={<Dashboard />} />
    <Route path="upload" element={<UploadDocuments />} />
    <Route path="documents" element={<Documents />} />
    <Route path="documents/:documentId" element={<DocumentDetails />} />
    <Route path="chunks" element={<ChunkExplorer />} />
    <Route path="embeddings" element={<EmbeddingsExplorer />} />
    <Route path="search" element={<SemanticSearch />} />
    <Route path="project-intelligence" element={<ProjectIntelligence />} />
    <Route path="settings" element={<SettingsPage />} />
  </Route></Routes></BrowserRouter>
}
