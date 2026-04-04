import { Eye, EyeOff } from 'lucide-react'

type RcloneSettings = {
  google_client_id: string
  google_client_secret: string
  google_token: string
  google_service_account_file: string
}

type SettingsRcloneSectionProps = {
  showInstructions: boolean
  onToggleInstructions: () => void
  rcloneSettings: RcloneSettings
  setRcloneSettings: (next: RcloneSettings) => void
  showClientId: boolean
  setShowClientId: (next: boolean) => void
  showClientSecret: boolean
  setShowClientSecret: (next: boolean) => void
  showToken: boolean
  setShowToken: (next: boolean) => void
  onSave: () => void
  onUploadServiceAccount: (file: File) => Promise<void>
  uploadingServiceAccount: boolean
  saving: boolean
  hasUnsaved: boolean
}

function SettingsRcloneSection({
  showInstructions,
  onToggleInstructions,
  rcloneSettings,
  setRcloneSettings,
  showClientId,
  setShowClientId,
  showClientSecret,
  setShowClientSecret,
  showToken,
  setShowToken,
  onSave,
  onUploadServiceAccount,
  uploadingServiceAccount,
  saving,
  hasUnsaved,
}: SettingsRcloneSectionProps) {
  return (
    <div className="settings-section">
      <div className="settings-section-header">
        <h2>Google Drive OAuth Credentials</h2>
        <p className="setting-description">
          Configure Google Drive access for rclone using OAuth credentials or an optional service account JSON file path.
        </p>
        <p className="setting-description">
          Saved values are shown directly. Use the eye button to hide or reveal sensitive fields.
        </p>
      </div>

      <button type="button" className="instructions-toggle" onClick={onToggleInstructions}>
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

      <div className="rclone-form">
        <div className="form-group">
          <label>Client ID</label>
          <div className="input-with-toggle">
            <input
              type={showClientId ? 'text' : 'password'}
              value={rcloneSettings.google_client_id}
              onChange={(e) => setRcloneSettings({ ...rcloneSettings, google_client_id: e.target.value })}
              placeholder="123456789.apps.googleusercontent.com"
            />
            <button
              type="button"
              className="toggle-visibility"
              onClick={() => setShowClientId(!showClientId)}
              title={showClientId ? 'Hide' : 'Show'}
            >
              {showClientId ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
        </div>

        <div className="form-group">
          <label>Client Secret</label>
          <div className="input-with-toggle">
            <input
              type={showClientSecret ? 'text' : 'password'}
              value={rcloneSettings.google_client_secret}
              onChange={(e) => setRcloneSettings({ ...rcloneSettings, google_client_secret: e.target.value })}
              placeholder="GOCSPX-xxxxxxxxxxxxx"
            />
            <button
              type="button"
              className="toggle-visibility"
              onClick={() => setShowClientSecret(!showClientSecret)}
              title={showClientSecret ? 'Hide' : 'Show'}
            >
              {showClientSecret ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
        </div>

        <div className="form-group">
          <label>Google Drive Token (Full JSON)</label>
          <div className="input-with-toggle">
            <textarea
              className={showToken ? '' : 'password-textarea'}
              value={rcloneSettings.google_token}
              onChange={(e) => setRcloneSettings({ ...rcloneSettings, google_token: e.target.value })}
              placeholder='{"access_token": "...", "token_type": "Bearer", "refresh_token": "...", "expiry": "..."}'
              rows={3}
            />
            <button
              type="button"
              className="toggle-visibility"
              onClick={() => setShowToken(!showToken)}
              title={showToken ? 'Hide' : 'Show'}
            >
              {showToken ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
        </div>

        <div className="form-group">
          <label>Service Account JSON Path (Optional)</label>
          <input
            type="text"
            value={rcloneSettings.google_service_account_file}
            onChange={(e) => setRcloneSettings({ ...rcloneSettings, google_service_account_file: e.target.value })}
            placeholder="/config/service_accounts/my-service-account.json"
          />
          <small>
            If set, rclone will use this file with <code>--drive-service-account-file</code>. Keep OAuth fields configured as fallback if desired.
          </small>
          <input
            type="file"
            accept="application/json,.json"
            id="settings-service-account-upload"
            style={{ display: 'none' }}
            onChange={async (e) => {
              const file = e.target.files?.[0]
              if (!file) return
              await onUploadServiceAccount(file)
              e.target.value = ''
            }}
          />
          <button
            type="button"
            className="btn-secondary service-account-upload-btn"
            onClick={() => document.getElementById('settings-service-account-upload')?.click()}
            disabled={uploadingServiceAccount}
          >
            {uploadingServiceAccount ? 'Uploading...' : 'Upload Service Account JSON'}
          </button>
        </div>

        <button className={`btn-save ${hasUnsaved ? 'btn-unsaved' : ''}`} onClick={onSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save Rclone Configuration'}
        </button>
      </div>
    </div>
  )
}

export default SettingsRcloneSection
