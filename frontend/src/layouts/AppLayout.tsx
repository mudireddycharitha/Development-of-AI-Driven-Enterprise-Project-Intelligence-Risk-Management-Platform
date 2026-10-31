import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Activity, Blocks, BookOpenText, Boxes, CircleHelp, Files, Gauge, Menu, Search, Settings, ShieldAlert, Target, Upload, Workflow, X, ListChecks } from 'lucide-react'
import { apiService } from '../services/api'

const navigationGroups = [
  { title: 'WORKSPACE', items: [
    { to: '/', label: 'Overview', icon: Gauge, end: true },
    { to: '/documents', label: 'Documents', icon: Files },
    { to: '/search', label: 'Semantic search', icon: Search },
    { to: '/upload', label: 'Upload documents', icon: Upload },
  ] },
  { title: 'PROJECT AGENTS', items: [
    { to: '/project-intelligence?view=scope', label: 'Scope & Deliverables', icon: Target },
    { to: '/project-intelligence?view=risks', label: 'Risk Intelligence', icon: ShieldAlert },
    { to: '/project-intelligence?view=blockers', label: 'Blockers & Actions', icon: ListChecks },
  ] },
  { title: 'TOOLS', items: [
    { to: '/chunks', label: 'Chunk explorer', icon: Blocks },
    { to: '/embeddings', label: 'Embeddings', icon: Boxes },
    { to: '/settings', label: 'System status', icon: Settings },
  ] },
]
const navigation = navigationGroups.flatMap(group => group.items)

export function AppLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [backendStatus, setBackendStatus] = useState<'healthy' | 'unavailable'>('unavailable')
  const currentPage = location.pathname === '/project-intelligence'
    ? ({ scope: 'Scope & Deliverables', risks: 'Risk Intelligence', blockers: 'Blockers & Actions' }[new URLSearchParams(location.search).get('view') ?? ''] ?? 'Project Intelligence')
    : navigation.find(item => item.to.split('?')[0] === location.pathname)?.label ?? 'Workspace'

  useEffect(() => {
    let active = true
    apiService.health().then(result => {
      if (active) setBackendStatus(result.status === 'healthy' ? 'healthy' : 'unavailable')
    }).catch(() => {
      if (active) setBackendStatus('unavailable')
    })
    return () => { active = false }
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
      <NavLink to="/" className="brand">
        <span className="brand-mark"><Workflow size={21} /></span>
        <span><strong>Project Intelligence</strong><small>AI PROJECT WORKSPACE</small></span>
        <button className="sidebar-close" aria-label="Close navigation" onClick={event => { event.preventDefault(); setSidebarOpen(false) }}><X size={17} /></button>
      </NavLink>
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
        <div className="sidebar-health"><Activity size={16} /><span>Backend service</span><i className={`health-${backendStatus}`} /></div>
        <div className="help-label"><CircleHelp size={15} /> Local-first project workspace</div>
      </div>
    </aside>
    <main className="main-area">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button>
        <div className="breadcrumb"><BookOpenText size={16} /><span>Project Intelligence</span><span>/</span><strong>{currentPage}</strong></div>
        <NavLink to="/search" className="topbar-search"><Search size={15} /><span>Search workspace</span><kbd>Ctrl K</kbd></NavLink>
        <div className={`topbar-status ${backendStatus}`}><i /> {backendStatus === 'healthy' ? 'API CONNECTED' : 'API UNAVAILABLE'}</div>
      </header>
      <div className="content-area"><Outlet /></div>
    </main>
  </div>
}
