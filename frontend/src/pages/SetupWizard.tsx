import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { saveSettings, testPlex, testSonarr, testRadarr, getSettings, uploadBackup, uploadServiceAccountJson, getApiErrorMessage } from '../api/client'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { Eye, EyeOff } from 'lucide-react'
import posterFlowIcon from '../assets/PosterFlow.webp'
import './SetupWizard.css'

interface SetupWizardProps {
  onComplete: () => void
}

interface ServerInstance {
  name: string
  url: string
  api_key: string
}

interface FormData {
  google_client_id: string
  google_client_secret: string
  google_refresh_token: string
  google_service_account_file: string
  poster_destination: string
  plex_instances: ServerInstance[]
  sonarr_instances: ServerInstance[]
  radarr_instances: ServerInstance[]
}

const STORAGE_KEY = 'posterflow_setup_data'
const DEFAULT_POSTER_DESTINATION = '/posters/assets'

function SetupWizard({ onComplete }: SetupWizardProps) {
  const navigate = useNavigate()
  const [step, setStep] = useState(0) // Start at 0 for welcome screen
  const [showInstructions, setShowInstructions] = useState(false)
  const [testStatus, setTestStatus] = useState<Record<string, { loading: boolean; success?: boolean; message?: string }>>({})
  const [skipPlex, setSkipPlex] = useState(false)
  const [skipSonarr, setSkipSonarr] = useState(false)
  const [skipRadarr, setSkipRadarr] = useState(false)
  const [isLoadingSettings, setIsLoadingSettings] = useState(true)
  const [restoreLoading, setRestoreLoading] = useState(false)
  const [showRestoreRestartConfirm, setShowRestoreRestartConfirm] = useState(false)
  const [serviceAccountUploadLoading, setServiceAccountUploadLoading] = useState(false)
  
  // Field visibility state for sensitive data
  const [showClientId, setShowClientId] = useState(false)
  const [showClientSecret, setShowClientSecret] = useState(false)
  const [showRefreshToken, setShowRefreshToken] = useState(false)
  const [showPlexTokens, setShowPlexTokens] = useState<Record<number, boolean>>({})
  const [showSonarrKeys, setShowSonarrKeys] = useState<Record<number, boolean>>({})
  const [showRadarrKeys, setShowRadarrKeys] = useState<Record<number, boolean>>({})
  
  const { showToast } = useToast()
  
  // Load from localStorage or use defaults
  const loadSavedData = (): FormData => {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      try {
        return JSON.parse(saved)
      } catch (e) {
        console.error('Error parsing saved data:', e)
      }
    }
    return {
      google_client_id: '',
      google_client_secret: '',
      google_refresh_token: '',
      google_service_account_file: '',
      poster_destination: '',
      plex_instances: [{ name: 'Plex', url: '', api_key: '' }],
      sonarr_instances: [{ name: 'Sonarr', url: '', api_key: '' }],
      radarr_instances: [{ name: 'Radarr', url: '', api_key: '' }]
    }
  }

  const [formData, setFormData] = useState<FormData>(loadSavedData)

  // Fetch existing settings from backend on mount
  useEffect(() => {
    const loadExistingSettings = async () => {
      try {
        const settings = await getSettings()
        
        // Start with current form data to avoid overwriting localStorage auto-save
        const updatedFormData: FormData = {
          google_client_id: settings.google_client_id || '',
          google_client_secret: settings.google_client_secret || '',
          google_refresh_token: settings.google_token || settings.google_refresh_token || '',
          google_service_account_file: settings.google_service_account_file || '',
          poster_destination: settings.poster_destination || '',
          plex_instances: [{ name: 'Plex', url: '', api_key: '' }],
          sonarr_instances: [{ name: 'Sonarr', url: '', api_key: '' }],
          radarr_instances: [{ name: 'Radarr', url: '', api_key: '' }]
        }

        // Parse Plex instances (stored as JSON string)
        if (settings.plex_instances) {
          try {
            const plexInstances = JSON.parse(settings.plex_instances)
            if (Array.isArray(plexInstances) && plexInstances.length > 0) {
              // Keep all instances, even if empty (user might be editing them)
              updatedFormData.plex_instances = plexInstances
            }
          } catch (e) {
            console.error('Error parsing plex instances:', e)
          }
        }

        // Parse Sonarr instances (stored as JSON string)
        if (settings.sonarr_instances) {
          try {
            const sonarrInstances = JSON.parse(settings.sonarr_instances)
            if (Array.isArray(sonarrInstances) && sonarrInstances.length > 0) {
              // Keep all instances, even if empty (user might be editing them)
              updatedFormData.sonarr_instances = sonarrInstances
            }
          } catch (e) {
            console.error('Error parsing sonarr instances:', e)
          }
        }

        // Parse Radarr instances (stored as JSON string)
        if (settings.radarr_instances) {
          try {
            const radarrInstances = JSON.parse(settings.radarr_instances)
            if (Array.isArray(radarrInstances) && radarrInstances.length > 0) {
              // Keep all instances, even if empty (user might be editing them)
              updatedFormData.radarr_instances = radarrInstances
            }
          } catch (e) {
            console.error('Error parsing radarr instances:', e)
          }
        }

        // Update form data with backend settings (takes precedence over localStorage)
        setFormData(updatedFormData)
      } catch (error) {
        console.error('Error loading settings:', error)
        // If error, keep the localStorage defaults
      } finally {
        setIsLoadingSettings(false)
      }
    }

    loadExistingSettings()
  }, [])

  // Auto-save to localStorage whenever formData changes (but only after initial load)
  useEffect(() => {
    // Don't save during initial loading to avoid race conditions
    if (!isLoadingSettings) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(formData))
    }
  }, [formData, isLoadingSettings])

  const updateGoogleCreds = (field: string, value: string) => {
    setFormData(prev => ({ ...prev, [field]: value }))
  }

  const updatePlexInstance = (index: number, field: keyof ServerInstance, value: string) => {
    setFormData(prev => {
      const updated = [...prev.plex_instances]
      updated[index] = { ...updated[index], [field]: value }
      return { ...prev, plex_instances: updated }
    })
  }

  const updateSonarrInstance = (index: number, field: keyof ServerInstance, value: string) => {
    setFormData(prev => {
      const updated = [...prev.sonarr_instances]
      updated[index] = { ...updated[index], [field]: value }
      return { ...prev, sonarr_instances: updated }
    })
  }

  const updateRadarrInstance = (index: number, field: keyof ServerInstance, value: string) => {
    setFormData(prev => {
      const updated = [...prev.radarr_instances]
      updated[index] = { ...updated[index], [field]: value }
      return { ...prev, radarr_instances: updated }
    })
  }

  const addPlexInstance = () => {
    setFormData(prev => ({
      ...prev,
      plex_instances: [...prev.plex_instances, { name: `Plex ${prev.plex_instances.length + 1}`, url: '', api_key: '' }]
    }))
  }

  const addSonarrInstance = () => {
    setFormData(prev => ({
      ...prev,
      sonarr_instances: [...prev.sonarr_instances, { name: `Sonarr ${prev.sonarr_instances.length + 1}`, url: '', api_key: '' }]
    }))
  }

  const addRadarrInstance = () => {
    setFormData(prev => ({
      ...prev,
      radarr_instances: [...prev.radarr_instances, { name: `Radarr ${prev.radarr_instances.length + 1}`, url: '', api_key: '' }]
    }))
  }

  const removePlexInstance = (index: number) => {
    if (formData.plex_instances.length > 1) {
      setFormData(prev => ({
        ...prev,
        plex_instances: prev.plex_instances.filter((_, i) => i !== index)
      }))
    }
  }

  const removeSonarrInstance = (index: number) => {
    if (formData.sonarr_instances.length > 1) {
      setFormData(prev => ({
        ...prev,
        sonarr_instances: prev.sonarr_instances.filter((_, i) => i !== index)
      }))
    }
  }

  const removeRadarrInstance = (index: number) => {
    if (formData.radarr_instances.length > 1) {
      setFormData(prev => ({
        ...prev,
        radarr_instances: prev.radarr_instances.filter((_, i) => i !== index)
      }))
    }
  }

  const handleTestPlex = async (index: number) => {
    const instance = formData.plex_instances[index]
    if (!instance.url || !instance.api_key) {
      showToast('Please enter both URL and Token', 'error')
      return
    }

    const key = `plex_${index}`
    setTestStatus(prev => ({ ...prev, [key]: { loading: true } }))
    
    try {
      const result = await testPlex(instance.url, instance.api_key)
      setTestStatus(prev => ({ 
        ...prev, 
        [key]: { loading: false, success: true, message: result.message }
      }))
    } catch (error) {
      setTestStatus(prev => ({ 
        ...prev, 
        [key]: { loading: false, success: false, message: getApiErrorMessage(error, 'Connection failed') }
      }))
    }
  }

  const handleTestSonarr = async (index: number) => {
    const instance = formData.sonarr_instances[index]
    if (!instance.url || !instance.api_key) {
      showToast('Please enter both URL and API Key', 'error')
      return
    }

    const key = `sonarr_${index}`
    setTestStatus(prev => ({ ...prev, [key]: { loading: true } }))
    
    try {
      const result = await testSonarr(instance.url, instance.api_key)
      setTestStatus(prev => ({ 
        ...prev, 
        [key]: { loading: false, success: true, message: result.message }
      }))
    } catch (error) {
      setTestStatus(prev => ({ 
        ...prev, 
        [key]: { loading: false, success: false, message: getApiErrorMessage(error, 'Connection failed') }
      }))
    }
  }

  const handleTestRadarr = async (index: number) => {
    const instance = formData.radarr_instances[index]
    if (!instance.url || !instance.api_key) {
      showToast('Please enter both URL and API Key', 'error')
      return
    }

    const key = `radarr_${index}`
    setTestStatus(prev => ({ ...prev, [key]: { loading: true } }))
    
    try {
      const result = await testRadarr(instance.url, instance.api_key)
      setTestStatus(prev => ({ 
        ...prev, 
        [key]: { loading: false, success: true, message: result.message }
      }))
    } catch (error) {
      setTestStatus(prev => ({ 
        ...prev, 
        [key]: { loading: false, success: false, message: getApiErrorMessage(error, 'Connection failed') }
      }))
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      const posterDestination = formData.poster_destination.trim() || DEFAULT_POSTER_DESTINATION
      const settings = {
        google_client_id: formData.google_client_id,
        google_client_secret: formData.google_client_secret,
        google_refresh_token: formData.google_refresh_token,
        google_token: formData.google_refresh_token,
        google_service_account_file: formData.google_service_account_file,
        poster_destination: posterDestination,
        plex_instances: JSON.stringify(formData.plex_instances.filter(p => p.url)),
        sonarr_instances: JSON.stringify(formData.sonarr_instances.filter(s => s.url)),
        radarr_instances: JSON.stringify(formData.radarr_instances.filter(r => r.url)),
        setup_complete: 'true'
      }
      await saveSettings(settings)
      localStorage.removeItem(STORAGE_KEY) // Clear saved data after successful setup
      onComplete()
    } catch (error) {
      console.error('Error saving settings:', error)
      showToast('Error saving settings. Please try again.', 'error')
    }
  }

  const handleSkip = async () => {
    try {
      await saveSettings({ setup_complete: 'true' })
      localStorage.removeItem(STORAGE_KEY)
      onComplete()
      // Force navigation to dashboard
      navigate('/', { replace: true })
    } catch (error) {
      console.error('Error skipping setup:', error)
      showToast('Error skipping setup. Please try again.', 'error')
    }
  }

  const handleServiceAccountUpload = async (file: File) => {
    setServiceAccountUploadLoading(true)
    try {
      const result = await uploadServiceAccountJson(file)
      updateGoogleCreds('google_service_account_file', result.path)
      showToast('Service account JSON uploaded successfully!', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to upload service account JSON'), 'error')
    } finally {
      setServiceAccountUploadLoading(false)
    }
  }

  // Validation functions
  const isStep1Valid = () => {
    const hasServiceAccount = formData.google_service_account_file.trim() !== ''
    const hasOAuth =
      formData.google_client_id.trim() !== '' &&
      formData.google_client_secret.trim() !== '' &&
      formData.google_refresh_token.trim() !== ''

    return hasServiceAccount || hasOAuth
  }

  const isStep2Valid = () => {
    const hasPlex = formData.plex_instances.some(p => p.url.trim() !== '' && p.api_key.trim() !== '')
    const hasSonarr = formData.sonarr_instances.some(s => s.url.trim() !== '' && s.api_key.trim() !== '')
    const hasRadarr = formData.radarr_instances.some(r => r.url.trim() !== '' && r.api_key.trim() !== '')
    
    return (hasPlex || skipPlex) && (hasSonarr || skipSonarr) && (hasRadarr || skipRadarr)
  }

  const isStep3Valid = () => {
    return true
  }

  return (
    <div className="setup-wizard">
      <div className="setup-container">
        <div className="setup-header">
          <h1>
            <img src={posterFlowIcon} alt="PosterFlow" className="setup-logo" />
            Welcome to PosterFlow
          </h1>
          <p>Let's get you set up with automated poster management</p>
          {step > 0 && <small className="auto-save-notice">✓ Progress automatically saved</small>}
        </div>

        {isLoadingSettings ? (
          <div className="loading-settings">
            <p>Loading existing settings...</p>
          </div>
        ) : (
          <>
            {step > 0 && (
              <div className="setup-steps">
                <div className={`step ${step === 1 ? 'active' : ''}`}>1. Google Drive</div>
                <div className={`step ${step === 2 ? 'active' : ''}`}>2. Media Servers</div>
                <div className={`step ${step === 3 ? 'active' : ''}`}>3. Destination</div>
                <div className={`step ${step === 4 ? 'active' : ''}`}>4. Finish</div>
              </div>
            )}

            <form onSubmit={handleSubmit} className="setup-form">
          {step === 0 && (
            <div className="welcome-screen">
              <div className="welcome-content">
                <h2>Welcome to PosterFlow Setup</h2>
                <p className="welcome-description">
                  PosterFlow automates poster management for your media library by syncing beautiful 
                  posters from community drives to an asset folder that can be uploaded to Plex or 
                  used by other community apps like Kometa.
                </p>
                
                <div className="welcome-options">
                  <div className="welcome-option">
                    <h3>🚀 Start Fresh</h3>
                    <p>Set up PosterFlow from scratch with the guided setup wizard.</p>
                    <button 
                      type="button" 
                      className="btn-primary" 
                      onClick={() => setStep(1)}
                    >
                      Start Setup Wizard
                    </button>
                  </div>

                  <div className="welcome-option">
                    <h3>📦 Restore Backup</h3>
                    <p>Already have a backup? Restore your previous configuration.</p>
                    <input
                      type="file"
                      accept=".zip"
                      id="welcome-restore-input"
                      style={{ display: 'none' }}
                      onChange={async (e) => {
                        const file = e.target.files?.[0]
                        if (!file) return
                        
                        setRestoreLoading(true)
                        try {
                          const result = await uploadBackup(file)
                          showToast(result.message, 'success')
                          e.target.value = ''
                          setTimeout(() => {
                            setShowRestoreRestartConfirm(true)
                          }, 1000)
                        } catch (error) {
                          console.error('Restore failed:', error)
                          showToast(getApiErrorMessage(error, 'Failed to restore backup'), 'error')
                          e.target.value = ''
                        } finally {
                          setRestoreLoading(false)
                        }
                      }}
                    />
                    <button 
                      type="button"
                      className="btn-secondary" 
                      onClick={() => document.getElementById('welcome-restore-input')?.click()}
                      disabled={restoreLoading}
                    >
                      {restoreLoading ? 'Restoring...' : 'Load Backup File'}
                    </button>
                  </div>

                  <div className="welcome-option">
                    <h3>⏭️ Skip Setup</h3>
                    <p>Configure later and go straight to the dashboard.</p>
                    <button 
                      type="button"
                      className="btn-tertiary" 
                      onClick={handleSkip}
                    >
                      Skip to Dashboard
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
          
          {step === 1 && (
            <div className="form-section">
              <h2>Google Drive Configuration</h2>
              <p className="section-description">
                Enter OAuth credentials or a Service Account JSON path to access community poster drives.
              </p>

              <button 
                type="button" 
                className="instructions-toggle"
                onClick={() => setShowInstructions(!showInstructions)}
              >
                {showInstructions ? '▼' : '▶'} How to get Google API credentials
              </button>

              {showInstructions && (
                <div className="instructions-box">
                  <h3>Step-by-step Guide:</h3>
                  
                  <div className="instruction-step">
                    <strong>1. Create a Google Cloud Project</strong>
                    <ul>
                      <li>Go to <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer">Google Cloud Console</a></li>
                      <li>Click "Create Project" or select existing project</li>
                      <li>Name it "Posterflow" (or any name you prefer)</li>
                    </ul>
                  </div>

                  <div className="instruction-step">
                    <strong>2. Enable Google Drive API</strong>
                    <ul>
                      <li>In your project, go to "APIs & Services" → "Library"</li>
                      <li>Search for "Google Drive API"</li>
                      <li>Click "Enable"</li>
                    </ul>
                  </div>

                  <div className="instruction-step">
                    <strong>3. Create OAuth 2.0 Credentials</strong>
                    <ul>
                      <li>Go to "APIs & Services" → "Credentials"</li>
                      <li>Click "Create Credentials" → "OAuth client ID"</li>
                      <li>If prompted, configure OAuth consent screen:
                        <ul>
                          <li>Choose "External" user type</li>
                          <li>Fill in app name: "Posterflow"</li>
                          <li>Add your email as developer contact</li>
                          <li>Add scope: <code>../auth/drive.readonly</code></li>
                          <li>Add your email as test user</li>
                        </ul>
                      </li>
                      <li>Application type: "Desktop app"</li>
                      <li>Name: "Posterflow Desktop"</li>
                      <li>Click "Create"</li>
                    </ul>
                  </div>

                  <div className="instruction-step">
                    <strong>4. Get Client ID and Secret</strong>
                    <ul>
                      <li>Copy the Client ID and Client Secret or download the .json file.</li>
                      <li>TIP: You can't download the .json after leaving and returning to this page. Download it now and save for later if needed.</li>
                      <li>Paste them in the fields below</li>
                    </ul>
                  </div>

                  <div className="instruction-step">
                    <strong>5. Generate Refresh Token (Recommended Method)</strong>
                    <ul>
                      <li>Install rclone on your computer: <a href="https://rclone.org/install/" target="_blank" rel="noopener noreferrer">rclone.org/install</a></li>
                      <li>Open terminal and run:</li>
                      <li><code>rclone authorize "drive" "YOUR_CLIENT_ID" "YOUR_CLIENT_SECRET"</code></li>
                      <li>A browser window will open - sign in and authorize</li>
                      <li>The terminal will display a token - copy the <code>refresh_token</code> value. Copy from bracket to bracket &#123; to &#125;</li>
                      <li>Paste it in the "Refresh Token" field below</li>
                    </ul>
                  </div>

                  <div className="instruction-step alternate-method">
                    <strong>Alternative: OAuth Playground (if rclone not available)</strong>
                    <ul>
                      <li>Use <a href="https://developers.google.com/oauthplayground/" target="_blank" rel="noopener noreferrer">OAuth 2.0 Playground</a></li>
                      <li>Click settings gear (⚙️) → Check "Use your own OAuth credentials"</li>
                      <li>Paste your Client ID and Client Secret</li>
                      <li>In Step 1: Select "Drive API v3" → <code>../auth/drive.readonly</code></li>
                      <li>Click "Authorize APIs" and sign in</li>
                      <li>In Step 2: Click "Exchange authorization code for tokens"</li>
                      <li>Copy the "Refresh token" value</li>
                    </ul>
                  </div>
                </div>
              )}

              <div className="form-group">
                <label>Client ID <span className="required">*</span></label>
                <div className="input-with-toggle">
                  <input
                    type={showClientId ? "text" : "password"}
                    name="google_client_id"
                    value={formData.google_client_id}
                    onChange={(e) => updateGoogleCreds('google_client_id', e.target.value)}
                    placeholder="123456789.apps.googleusercontent.com"
                    required
                  />
                  <button
                    type="button"
                    className="toggle-visibility"
                    onClick={() => setShowClientId(!showClientId)}
                    title={showClientId ? "Hide" : "Show"}
                  >
                    {showClientId ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label>Client Secret <span className="required">*</span></label>
                <div className="input-with-toggle">
                  <input
                    type={showClientSecret ? "text" : "password"}
                    name="google_client_secret"
                    value={formData.google_client_secret}
                    onChange={(e) => updateGoogleCreds('google_client_secret', e.target.value)}
                    placeholder="GOCSPX-xxxxxxxxxxxxx"
                    required
                  />
                  <button
                    type="button"
                    className="toggle-visibility"
                    onClick={() => setShowClientSecret(!showClientSecret)}
                    title={showClientSecret ? "Hide" : "Show"}
                  >
                    {showClientSecret ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label>Refresh Token <span className="required">*</span></label>
                <div className="input-with-toggle">
                  <input
                    type={showRefreshToken ? "text" : "password"}
                    name="google_refresh_token"
                    value={formData.google_refresh_token}
                    onChange={(e) => updateGoogleCreds('google_refresh_token', e.target.value)}
                    placeholder="{1//xxxxxxxxxxxxx}"
                    required
                  />
                  <button
                    type="button"
                    className="toggle-visibility"
                    onClick={() => setShowRefreshToken(!showRefreshToken)}
                    title={showRefreshToken ? "Hide" : "Show"}
                  >
                    {showRefreshToken ? <EyeOff size={18} /> : <Eye size={18} />}
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label>Service Account JSON Path (Optional)</label>
                <input
                  type="text"
                  name="google_service_account_file"
                  value={formData.google_service_account_file}
                  onChange={(e) => updateGoogleCreds('google_service_account_file', e.target.value)}
                  placeholder="/config/service_accounts/my-service-account.json"
                />
                <small>
                  You can provide this instead of OAuth fields. PosterFlow will pass it to rclone with <code>--drive-service-account-file</code>.
                </small>
                <input
                  type="file"
                  accept="application/json,.json"
                  id="setup-service-account-upload"
                  style={{ display: 'none' }}
                  onChange={async (e) => {
                    const file = e.target.files?.[0]
                    if (!file) return
                    await handleServiceAccountUpload(file)
                    e.target.value = ''
                  }}
                />
                <button
                  type="button"
                  className="btn-secondary service-account-upload-btn"
                  onClick={() => document.getElementById('setup-service-account-upload')?.click()}
                  disabled={serviceAccountUploadLoading}
                >
                  {serviceAccountUploadLoading ? 'Uploading...' : 'Upload Service Account JSON'}
                </button>
              </div>

              <div className="button-group-wrapper">
                <div className="button-group">
                  <button type="button" className="btn-secondary" onClick={() => setStep(0)}>
                    ← Back
                  </button>
                  <button 
                    type="button" 
                    className="btn-primary" 
                    onClick={() => setStep(2)}
                    disabled={!isStep1Valid()}
                  >
                    Next
                  </button>
                </div>
                <small className="btn-subtext">Enter either OAuth fields above or a Service Account JSON path</small>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="form-section">
              <h2>Media Server Configuration</h2>
              <p className="section-description">
                Connect to Plex, Sonarr, and Radarr (all optional).
              </p>

              <div className="server-section">
                <div className="server-header">
                  <h3>Plex Instances <span className="required">*</span></h3>
                  <button type="button" className="btn-add" onClick={addPlexInstance}>
                    + Add Instance
                  </button>
                </div>
                
                <div className="skip-service-checkbox">
                  <label>
                    <input
                      type="checkbox"
                      checked={skipPlex}
                      onChange={(e) => setSkipPlex(e.target.checked)}
                    />
                    I don't have Plex
                  </label>
                </div>

                {!skipPlex && formData.plex_instances.map((instance, index) => {
                  const statusKey = `plex_${index}`
                  const status = testStatus[statusKey]

                  return (
                    <div key={index} className="instance-group">
                      <div className="instance-header">
                        <span className="instance-number">Instance {index + 1}</span>
                        {formData.plex_instances.length > 1 && (
                          <button 
                            type="button" 
                            className="btn-remove" 
                            onClick={() => removePlexInstance(index)}
                          >
                            ✕
                          </button>
                        )}
                      </div>

                      <div className="form-group">
                        <label>Name</label>
                        <input
                          type="text"
                          value={instance.name}
                          onChange={(e) => updatePlexInstance(index, 'name', e.target.value)}
                          placeholder="e.g., Plex Main, Plex 4K"
                        />
                      </div>

                      <div className="form-group">
                        <label>Plex URL</label>
                        <input
                          type="text"
                          value={instance.url}
                          onChange={(e) => updatePlexInstance(index, 'url', e.target.value)}
                          placeholder="http://localhost:32400"
                        />
                      </div>

                      <div className="form-group">
                        <label>Plex Token</label>
                        <div className="input-with-toggle">
                          <input
                            type={showPlexTokens[index] ? "text" : "password"}
                            value={instance.api_key}
                            onChange={(e) => updatePlexInstance(index, 'api_key', e.target.value)}
                            placeholder="Your Plex Token"
                          />
                          <button
                            type="button"
                            className="toggle-visibility"
                            onClick={() => setShowPlexTokens(prev => ({ ...prev, [index]: !prev[index] }))}
                            title={showPlexTokens[index] ? "Hide" : "Show"}
                          >
                            {showPlexTokens[index] ? <EyeOff size={18} /> : <Eye size={18} />}
                          </button>
                        </div>
                        <small>Find your token: <a href="https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/" target="_blank" rel="noopener noreferrer">Plex Support</a></small>
                      </div>

                      <button 
                        type="button" 
                        className="btn-test" 
                        onClick={() => handleTestPlex(index)}
                        disabled={status?.loading}
                      >
                        {status?.loading ? 'Testing...' : 'Test Connection'}
                      </button>

                      {status && !status.loading && (
                        <div className={`test-result ${status.success ? 'success' : 'error'}`}>
                          {status.success ? '✓' : '✕'} {status.message}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              <div className="server-section">
                <div className="server-header">
                  <h3>Sonarr Instances <span className="required">*</span></h3>
                  <button type="button" className="btn-add" onClick={addSonarrInstance}>
                    + Add Instance
                  </button>
                </div>
                
                <div className="skip-service-checkbox">
                  <label>
                    <input
                      type="checkbox"
                      checked={skipSonarr}
                      onChange={(e) => setSkipSonarr(e.target.checked)}
                    />
                    I don't have Sonarr
                  </label>
                </div>

                {!skipSonarr && formData.sonarr_instances.map((instance, index) => {
                  const statusKey = `sonarr_${index}`
                  const status = testStatus[statusKey]

                  return (
                    <div key={index} className="instance-group">
                      <div className="instance-header">
                        <span className="instance-number">Instance {index + 1}</span>
                        {formData.sonarr_instances.length > 1 && (
                          <button 
                            type="button" 
                            className="btn-remove" 
                            onClick={() => removeSonarrInstance(index)}
                          >
                            ✕
                          </button>
                        )}
                      </div>

                      <div className="form-group">
                        <label>Name</label>
                        <input
                          type="text"
                          value={instance.name}
                          onChange={(e) => updateSonarrInstance(index, 'name', e.target.value)}
                          placeholder="e.g., Sonarr 4K, Sonarr HD"
                        />
                      </div>

                      <div className="form-group">
                        <label>Sonarr URL</label>
                        <input
                          type="text"
                          value={instance.url}
                          onChange={(e) => updateSonarrInstance(index, 'url', e.target.value)}
                          placeholder="http://localhost:8989"
                        />
                      </div>

                      <div className="form-group">
                        <label>API Key</label>
                        <div className="input-with-toggle">
                          <input
                            type={showSonarrKeys[index] ? "text" : "password"}
                            value={instance.api_key}
                            onChange={(e) => updateSonarrInstance(index, 'api_key', e.target.value)}
                            placeholder="Your Sonarr API Key"
                          />
                          <button
                            type="button"
                            className="toggle-visibility"
                            onClick={() => setShowSonarrKeys(prev => ({ ...prev, [index]: !prev[index] }))}
                            title={showSonarrKeys[index] ? "Hide" : "Show"}
                          >
                            {showSonarrKeys[index] ? <EyeOff size={18} /> : <Eye size={18} />}
                          </button>
                        </div>
                        <small>Find in Sonarr: Settings → General → Security → API Key</small>
                      </div>

                      <button 
                        type="button" 
                        className="btn-test" 
                        onClick={() => handleTestSonarr(index)}
                        disabled={status?.loading}
                      >
                        {status?.loading ? 'Testing...' : 'Test Connection'}
                      </button>

                      {status && !status.loading && (
                        <div className={`test-result ${status.success ? 'success' : 'error'}`}>
                          {status.success ? '✓' : '✕'} {status.message}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              <div className="server-section">
                <div className="server-header">
                  <h3>Radarr Instances <span className="required">*</span></h3>
                  <button type="button" className="btn-add" onClick={addRadarrInstance}>
                    + Add Instance
                  </button>
                </div>

                <div className="skip-service-checkbox">
                  <label>
                    <input
                      type="checkbox"
                      checked={skipRadarr}
                      onChange={(e) => setSkipRadarr(e.target.checked)}
                    />
                    I don't have Radarr
                  </label>
                </div>

                {!skipRadarr && formData.radarr_instances.map((instance, index) => {
                  const statusKey = `radarr_${index}`
                  const status = testStatus[statusKey]

                  return (
                    <div key={index} className="instance-group">
                      <div className="instance-header">
                        <span className="instance-number">Instance {index + 1}</span>
                        {formData.radarr_instances.length > 1 && (
                          <button 
                            type="button" 
                            className="btn-remove" 
                            onClick={() => removeRadarrInstance(index)}
                          >
                            ✕
                          </button>
                        )}
                      </div>

                      <div className="form-group">
                        <label>Name</label>
                        <input
                          type="text"
                          value={instance.name}
                          onChange={(e) => updateRadarrInstance(index, 'name', e.target.value)}
                          placeholder="e.g., Radarr 4K, Radarr HD"
                        />
                      </div>

                      <div className="form-group">
                        <label>Radarr URL</label>
                        <input
                          type="text"
                          value={instance.url}
                          onChange={(e) => updateRadarrInstance(index, 'url', e.target.value)}
                          placeholder="http://localhost:7878"
                        />
                      </div>

                      <div className="form-group">
                        <label>API Key</label>
                        <div className="input-with-toggle">
                          <input
                            type={showRadarrKeys[index] ? "text" : "password"}
                            value={instance.api_key}
                            onChange={(e) => updateRadarrInstance(index, 'api_key', e.target.value)}
                            placeholder="Your Radarr API Key"
                          />
                          <button
                            type="button"
                            className="toggle-visibility"
                            onClick={() => setShowRadarrKeys(prev => ({ ...prev, [index]: !prev[index] }))}
                            title={showRadarrKeys[index] ? "Hide" : "Show"}
                          >
                            {showRadarrKeys[index] ? <EyeOff size={18} /> : <Eye size={18} />}
                          </button>
                        </div>
                        <small>Find in Radarr: Settings → General → Security → API Key</small>
                      </div>

                      <button 
                        type="button" 
                        className="btn-test" 
                        onClick={() => handleTestRadarr(index)}
                        disabled={status?.loading}
                      >
                        {status?.loading ? 'Testing...' : 'Test Connection'}
                      </button>

                      {status && !status.loading && (
                        <div className={`test-result ${status.success ? 'success' : 'error'}`}>
                          {status.success ? '✓' : '✕'} {status.message}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>

              <div className="button-group">
                <button type="button" className="btn-secondary" onClick={() => setStep(1)}>
                  Back
                </button>
                <button 
                  type="button" 
                  className="btn-primary"
                  onClick={() => setStep(3)}
                  disabled={!isStep2Valid()}
                >
                  Next
                </button>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="form-section">
              <h2>Destination Folder Setup</h2>
              <p className="section-description">
                Choose where organized posters will be written on disk.
              </p>

              <div className="form-group">
                <label>Destination Directory (possibly kometa's asset directory)</label>
                <input
                  type="text"
                  name="poster_destination"
                  value={formData.poster_destination}
                  onChange={(e) => updateGoogleCreds('poster_destination', e.target.value)}
                  placeholder="ex. /kometa/config/assets"
                />
                <small>Leave blank to use default: <code>{DEFAULT_POSTER_DESTINATION}</code></small>
                <small className="destination-warning">⚠️ Where organized and renamed posters will be saved. Must be a mounted volume in your Docker container</small>
              </div>

              <div className="button-group">
                <button type="button" className="btn-secondary" onClick={() => setStep(2)}>
                  Back
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => setStep(4)}
                  disabled={!isStep3Valid()}
                >
                  Next
                </button>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="form-section">
              <h2>Setup Complete</h2>
              <p className="section-description">
                You're ready to enter PosterFlow. Here are recommended next steps once you open the app:
              </p>

              <ul className="setup-complete-list">
                <li>Open <strong>GDrives</strong> and subscribe to the poster drive packs you want to use.</li>
                <li>Open <strong>Poster Manager → Settings</strong> and confirm your destination directory and file operation mode.</li>
                <li>Open <strong>Poster Manager → Drive Priority</strong> and order drives/styles so matching uses your preferred source first.</li>
                <li>Open <strong>Poster Manager → Workflow</strong> and run a <strong>Dry Run</strong> before running the full workflow.</li>
                <li>Open <strong>Settings → Scheduling</strong> to automate sync and workflow jobs.</li>
                <li>Check <strong>Job Logs</strong> / <strong>Logs</strong> after first runs to verify everything is working as expected.</li>
              </ul>

              <div className="button-group">
                <button type="button" className="btn-secondary" onClick={() => setStep(3)}>
                  Back
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                >
                  Complete Setup
                </button>
              </div>
            </div>
          )}
        </form>
          </>
        )}
      </div>

      <ConfirmDialog
        isOpen={showRestoreRestartConfirm}
        title="Restart Required"
        message="Backup restored! The application needs to restart for changes to take effect. Restart now?"
        confirmText="Restart Now"
        cancelText="Later"
        variant="warning"
        onConfirm={() => {
          setShowRestoreRestartConfirm(false)
          window.location.reload()
        }}
        onCancel={() => setShowRestoreRestartConfirm(false)}
      />
    </div>
  )
}

export default SetupWizard