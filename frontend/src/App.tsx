import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef } from 'react'
import MainLayout from './components/MainLayout'
import SetupWizard from './pages/SetupWizard'
import Dashboard from './pages/Dashboard'
import GDrives from './pages/GDrives'
import Logs from './pages/Logs'
import Settings from './pages/Settings'
import PosterManager from './pages/PosterManager'
import IDarr from './pages/IDarr'
import MakerTools from './pages/MakerTools'
import PosterSearch from './pages/PosterSearch'
import PlexUpload from './pages/PlexUpload'
import CommunityRequests from './pages/CommunityRequests'
import { checkSetupComplete } from './api/client'
import { ToastProvider } from './components/Toast'
import { UnmatchedProvider } from './contexts/UnmatchedContext'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import LockScreen from './components/LockScreen'

const LAST_ROUTE_STORAGE_KEY = 'posterflow.navigation.lastRoute'

function RoutePersistence() {
  const location = useLocation()
  const navigate = useNavigate()
  const hasAttemptedRestoreRef = useRef(false)

  useEffect(() => {
    if (location.pathname === '/setup') {
      return
    }

    const fullPath = `${location.pathname}${location.search}${location.hash}`
    localStorage.setItem(LAST_ROUTE_STORAGE_KEY, fullPath)
  }, [location.pathname, location.search, location.hash])

  useEffect(() => {
    if (hasAttemptedRestoreRef.current || location.pathname !== '/') {
      return
    }

    hasAttemptedRestoreRef.current = true
    const navigationEntry = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
    if (!navigationEntry || navigationEntry.type !== 'reload') {
      return
    }

    const savedRoute = localStorage.getItem(LAST_ROUTE_STORAGE_KEY)
    if (!savedRoute || savedRoute === '/' || savedRoute.startsWith('/setup')) {
      return
    }

    navigate(savedRoute, { replace: true })
  }, [location.pathname, navigate])

  return null
}

/** Inner content – rendered inside AuthProvider so useAuth() is available. */
function AppContent() {
  const { isReady, isAuthRequired, isAuthenticated } = useAuth()
  const [isSetupComplete, setIsSetupComplete] = useState<boolean | null>(null)

  // Only check setup once authenticated (avoid wasted 401 calls while locked)
  useEffect(() => {
    if (!isReady || !isAuthenticated) return
    checkSetup()
  }, [isReady, isAuthenticated])

  useEffect(() => {
    document.body.classList.remove('ui-ready')

    const frameId = requestAnimationFrame(() => {
      document.body.classList.add('ui-ready')
    })

    return () => {
      cancelAnimationFrame(frameId)
      document.body.classList.remove('ui-ready')
    }
  }, [])

  const checkSetup = async () => {
    try {
      const complete = await checkSetupComplete()
      setIsSetupComplete(complete)
    } catch (error) {
      console.error('Error checking setup:', error)
      setIsSetupComplete(false)
    }
  }

  // Auth check still in progress
  if (!isReady) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        background: '#121212',
        color: '#fff'
      }}>
        <h2>Loading PosterFlow...</h2>
      </div>
    )
  }

  // Password required but not yet entered
  if (isAuthRequired && !isAuthenticated) {
    return <LockScreen />
  }

  // Authenticated – waiting for setup check
  if (isSetupComplete === null) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        background: '#121212',
        color: '#fff'
      }}>
        <h2>Loading PosterFlow...</h2>
      </div>
    )
  }

  return (
    <ToastProvider>
      <UnmatchedProvider>
        <BrowserRouter>
          <RoutePersistence />
          <Routes>
          {!isSetupComplete ? (
            <>
              <Route path="/setup" element={<SetupWizard onComplete={() => setIsSetupComplete(true)} />} />
              <Route path="*" element={<Navigate to="/setup" replace />} />
            </>
          ) : (
            <>
              <Route path="/" element={<MainLayout />}>
                <Route index element={<Dashboard />} />
                <Route path="drives" element={<GDrives />} />
                <Route path="poster-manager" element={<PosterManager />} />
                <Route path="IDarr" element={<IDarr />} />
                <Route path="maker-tools" element={<MakerTools />} />
                <Route path="plex-upload" element={<PlexUpload />} />
                <Route path="poster-search" element={<PosterSearch />} />
                <Route path="logs" element={<Logs />} />
                <Route path="community-requests" element={<CommunityRequests />} />
                <Route path="settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
              <Route path="/setup" element={<SetupWizard onComplete={() => window.history.back()} />} />
            </>
          )}
          </Routes>
        </BrowserRouter>
      </UnmatchedProvider>
    </ToastProvider>
  )
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  )
}

export default App