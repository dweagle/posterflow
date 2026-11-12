import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { lazy, Suspense, useState, useEffect, useRef } from 'react'
import MainLayout from './components/MainLayout'
// Every stylesheet, eagerly and in cascade order. Pages load lazily, and shared components lean on
// page stylesheets (ServiceLinks is styled by the maker-tools sheets), so no sheet may wait for a
// page's chunk.
import './styles/globals.css'
import './components/ConfirmDialog.css'
import './components/Toast.css'
import './components/Sidebar.css'
import './components/IdarrQuickAddNotice.css'
import './components/UpdateBanner.css'
import './components/MainLayout.css'
import './components/Toolbar.css'
import './components/Button.css'
import './components/IconButton.css'
import './components/Modal.css'
import './components/Tabs.css'
import './components/Subtabs.css'
import './components/Card.css'
import './components/PageHeader.css'
import './components/Form.css'
import './components/LibraryList.css'
import './components/Toggle.css'
import './components/settings/GoogleDriveCredentialsGuide.css'
import './pages/SetupWizard.css'
import './pages/Dashboard.css'
import './components/ArtworkTypePicker.css'
import './components/GdriveStorageModal.css'
import './pages/GDrives.css'
import './components/LogPane.css'
import './pages/Logs.css'
import './components/settings/SettingsSecuritySection.css'
import './components/settings/SettingsScriptsSection.css'
import './pages/Settings.css'
import './components/FloatTip.css'
import './pages/AssetSearch.css'
import './components/PosterDriveSearchModal.css'
import './pages/poster-manager/base.css'
import './pages/poster-manager/flow.css'
import './pages/poster-manager/renamer.css'
import './pages/poster-manager/settings.css'
import './pages/poster-manager/border.css'
import './pages/poster-manager/priority.css'
import './pages/poster-manager/unmatched.css'
import './pages/poster-manager/requests.css'
import './pages/IDarr.css'
import './pages/maker-tools/base.css'
import './pages/maker-tools/search.css'
import './pages/maker-tools/gallery.css'
import './pages/maker-tools/psd.css'
import './pages/maker-tools/unmatched.css'
import './pages/maker-tools/artwork.css'
import './pages/maker-tools/reminders.css'
import './pages/PlexUpload.css'
import './pages/Overlays.css'
import './pages/Tools.css'
import './pages/CommunityRequests.css'
import './components/LockScreen.css'
import './styles/utilities.css'
import Dashboard from './pages/Dashboard'
// Each page is its own chunk, fetched the first time it opens; the Dashboard, the first page seen, stays in the main bundle.
const SetupWizard = lazy(() => import('./pages/SetupWizard'))
const GDrives = lazy(() => import('./pages/GDrives'))
const Logs = lazy(() => import('./pages/Logs'))
const Settings = lazy(() => import('./pages/Settings'))
const PosterManager = lazy(() => import('./pages/PosterManager'))
const IDarr = lazy(() => import('./pages/IDarr'))
const MakerTools = lazy(() => import('./pages/MakerTools'))
const AssetSearch = lazy(() => import('./pages/AssetSearch'))
const PlexUpload = lazy(() => import('./pages/PlexUpload'))
const Overlays = lazy(() => import('./pages/Overlays'))
const Tools = lazy(() => import('./pages/Tools'))
const CommunityRequests = lazy(() => import('./pages/CommunityRequests'))
import { CommunityClaimStatusProvider } from './hooks/useCommunityClaimStatus'
import { checkSetupComplete } from './api/client'
import { ToastProvider } from './components/Toast'
import { AppEventsProvider } from './contexts/AppEventsContext'
import { RemindersProvider } from './contexts/RemindersContext'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import LockScreen from './components/LockScreen'
import RouteLoading from './components/RouteLoading'

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
      <div className="app-loading">
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
      <div className="app-loading">
        <h2>Loading PosterFlow...</h2>
      </div>
    )
  }

  return (
    <ToastProvider>
      <AppEventsProvider>
        <RemindersProvider>
        <BrowserRouter>
          <RoutePersistence />
          <Routes>
          {!isSetupComplete ? (
            <>
              <Route path="/setup" element={<Suspense fallback={<RouteLoading />}><SetupWizard onComplete={() => setIsSetupComplete(true)} /></Suspense>} />
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
                <Route path="asset-search" element={<AssetSearch />} />
                <Route path="poster-search" element={<Navigate to="/asset-search" replace />} />
                <Route path="overlays" element={<Overlays />} />
                <Route path="tools" element={<Tools />} />
                <Route path="logs" element={<Logs />} />
                <Route path="community-requests" element={<CommunityClaimStatusProvider><CommunityRequests /></CommunityClaimStatusProvider>} />
                <Route path="settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Route>
              <Route path="/setup" element={<Suspense fallback={<RouteLoading />}><SetupWizard onComplete={() => window.history.back()} /></Suspense>} />
            </>
          )}
          </Routes>
        </BrowserRouter>
        </RemindersProvider>
      </AppEventsProvider>
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