import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Activity, Blocks, BookOpenText, Boxes, CircleHelp, Files, Gauge, Menu, Search, Settings, ShieldAlert, Target, Upload, Workflow, X, ListChecks, FileOutput, HeartPulse, MessagesSquare, ChartNoAxesCombined } from 'lucide-react'
import { apiService } from '../services/api'
import type { SystemHealth } from '../types'

const navigationGroups = [
  { title: 'WORKSPACE', items: [
    { to: '/', label: 'Overview', icon: Gauge, end: true },
    { to: '/documents', label: 'Documents', icon: Files },
    { to: '/search', label: 'Semantic search', icon: Search },
    { to: '/upload', label: 'Document upload', icon: Upload },
  ] },
  { title: 'PROJECT INTELLIGENCE', items: [
    { to: '/project-intelligence?view=scope', label: 'Scope & Deliverables', icon: Target },
    { to: '/project-intelligence?view=risks', label: 'Risk Intelligence', icon: ShieldAlert },
    { to: '/project-intelligence?view=blockers', label: 'Blockers & Actions', icon: ListChecks },
    { to: '/documentation', label: 'Documentation generation', icon: FileOutput },
    { to: '/project-health', label: 'Project health', icon: HeartPulse },
    { to: '/assistant', label: 'Project assistant', icon: MessagesSquare },
  ] },
  { title: 'ANALYTICS & INSIGHTS', items: [
    { to: '/project-insights', label: 'Project insights', icon: ChartNoAxesCombined },
    { to: '/chunks', label: 'Chunk explorer', icon: Blocks },
    { to: '/embeddings', label: 'Embeddings', icon: Boxes },
  ] },
]
const navigation = navigationGroups.flatMap(group => group.items)

export function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [systemHealth, setSystemHealth] = useState<SystemHealth | null>(null)
  const currentPage = location.pathname === '/project-intelligence'
    ? ({ scope: 'Scope & Deliverables', risks: 'Risk Intelligence', blockers: 'Blockers & Actions' }[new URLSearchParams(location.search).get('view') ?? ''] ?? 'Project Intelligence')
    : location.pathname.startsWith('/documents/') ? 'Document details'
      : location.pathname === '/settings' ? 'System status'
        : navigation.find(item => item.to.split('?')[0] === location.pathname)?.label ?? 'Workspace'

  useEffect(() => {
    let active = true
    const refreshStatus = () => {
      apiService.health()
        .then(result => { if (active) setSystemHealth(result) })
        .catch(() => { if (active) setSystemHealth(null) })
    }
    refreshStatus()
    const interval = window.setInterval(refreshStatus, 30_000)
    return () => { active = false; window.clearInterval(interval) }
  }, [])
  useEffect(() => {
    const handleSearchShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        navigate('/search')
      }
    }
    window.addEventListener('keydown', handleSearchShortcut)
    return () => window.removeEventListener('keydown', handleSearchShortcut)
  }, [navigate])

  return <div className="app-shell">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar${sidebarOpen ? ' sidebar-open' : ''}`}>
      <div className="brand">
        <NavLink to="/" className="brand-home">
          <span className="brand-mark"><Workflow size={21} /></span>
          <span><strong>Project Intelligence</strong><small>INTELLIGENCE PLATFORM</small></span>
        </NavLink>
        <button className="sidebar-close" aria-label="Close navigation" onClick={() => setSidebarOpen(false)}><X size={17} /></button>
      </div>
      <nav>{navigationGroups.map(group => <section className="sidebar-nav-group" key={group.title}>
        <div className="nav-caption">{group.title}</div>
        {group.items.map(({ to, label, icon: Icon, end }) =>
          <NavLink key={to} to={to} end={end} onClick={() => setSidebarOpen(false)} className={({ isActive }) => {
            const targetView = new URLSearchParams(to.split('?')[1] ?? '').get('view')
            const activeView = new URLSearchParams(location.search).get('view')
            return `nav-link${isActive && (!targetView || targetView === activeView) ? ' active' : ''}`
          }}>
            <Icon size={18} strokeWidth={1.8} /><span>{label}</span>
          </NavLink>)}
      </section>)}</nav>
      <div className="sidebar-bottom">
        <div className="sidebar-health"><Activity size={16} /><span>Backend health</span><i className={`health-${systemHealth?.status === 'healthy' ? 'healthy' : 'unavailable'}`} /></div>
        <NavLink to="/settings" onClick={() => setSidebarOpen(false)}
          className={({ isActive }) => `nav-link settings-link${isActive ? ' active' : ''}`}>
          <Settings size={18} strokeWidth={1.8} /><span>System status</span>
        </NavLink>
        <div className="help-label"><CircleHelp size={15} /> Local-first project workspace</div>
      </div>
    </aside>
    <main className="main-area">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button>
        <div className="breadcrumb"><BookOpenText size={16} /><span>Project Intelligence</span><span>/</span><strong>{currentPage}</strong></div>
        <NavLink to="/search" className="topbar-search"><Search size={15} /><span>Search workspace</span><kbd>Ctrl K</kbd></NavLink>
        <div className="topbar-statuses" aria-label="System connectivity">
          <NavLink to="/settings" className={`topbar-status ${systemHealth?.api_status === 'online' ? 'healthy' : 'unavailable'}`}
            title={systemHealth ? `API ${systemHealth.status}; database ${systemHealth.database}; vector store ${systemHealth.vector_store}` : 'Backend API unavailable'}
            aria-label={systemHealth ? `API ${systemHealth.status}` : 'Backend API unavailable'}>
            <i /> API {systemHealth?.api_status === 'online' ? 'CONNECTED' : 'UNAVAILABLE'}
          </NavLink>
          <NavLink to="/settings" className={`topbar-status ${systemHealth?.ai_provider.operational ? 'healthy' : systemHealth?.ai_provider.configured ? 'configured' : 'unavailable'}`}
            title={systemHealth?.ai_provider.message ?? 'AI provider status unavailable'}
            aria-label={`AI provider ${systemHealth?.ai_provider.status ?? 'status unavailable'}`}>
            <i /> AI {systemHealth?.ai_provider.operational ? 'OPERATIONAL' : systemHealth?.ai_provider.configured ? 'CONFIGURED' : 'UNAVAILABLE'}
          </NavLink>
        </div>
      </header>
      <div className="content-area"><Outlet /></div>
    </main>
  </div>
}
