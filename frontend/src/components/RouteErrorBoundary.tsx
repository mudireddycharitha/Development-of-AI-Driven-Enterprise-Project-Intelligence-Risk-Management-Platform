import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface RouteErrorBoundaryProps {
  children: ReactNode
}

interface RouteErrorBoundaryState {
  error: Error | null
}

export class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Page rendering failed:', error, errorInfo.componentStack)
  }

  render() {
    if (this.state.error) {
      return <section className="panel" role="alert">
        <h2><AlertTriangle size={18} /> This page could not be displayed</h2>
        <p>The rest of the application is still available. Try reloading this page.</p>
        <button className="button button-secondary" onClick={() => window.location.reload()}>Reload page</button>
      </section>
    }
    return this.props.children
  }
}
