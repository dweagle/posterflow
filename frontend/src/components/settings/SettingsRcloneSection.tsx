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
  onToggleClientSecretVisibility: () => void
  showToken: boolean
  onToggleTokenVisibility: () => void
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
  onToggleClientSecretVisibility,
  showToken,
  onToggleTokenVisibility,
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
              <li>Click the <strong>project name button</strong> in the top-left header (it shows your current project name, e.g. "My First Project")</li>
              <li>In the modal that appears, click <strong>"New project"</strong> in the top-right corner</li>
              <li>Name it "PosterFlow" (or any name you prefer) and click "Create"</li>
            </ul>
          </div>

          <div className="instruction-step">
            <strong>2. Enable Google Drive API</strong>
            <ul>
              <li>Click the <strong>&#9776; hamburger menu</strong> (top-left) → "APIs &amp; Services" → "Library"</li>
              <li>Search for "Google Drive API" and click on it</li>
              <li>Click "Enable"</li>
            </ul>
          </div>

          <div className="instruction-step">
            <strong>3. Configure OAuth Consent Screen</strong>
            <ul>
              <li>Click the <strong>&#9776; hamburger menu</strong> (top-left) → "APIs &amp; Services" → "OAuth consent screen"</li>
              <li>This opens <strong>Google Auth Platform</strong></li>
              <li><strong>Branding:</strong> Enter app name ("PosterFlow"), support email, and developer contact. Click Save.</li>
              <li><strong>Audience:</strong> Select "External" for personal use. Click Save.</li>
              <li><strong>Data Access:</strong> Click "Add or remove scopes"
                <ul>
                  <li>In the scope picker, find and check <code>.../auth/drive</code> — "See, edit, create, and delete all of your Google Drive files"</li>
                  <li><strong>Important:</strong> Do NOT check <code>.../auth/drive.readonly</code> or any other drive scope — rclone requires full access to sync files</li>
                  <li>Click "Update" then "Save"</li>
                  <li>⚠️ <strong>Important:</strong> After saving, go back to the main "OAuth consent screen" - "Audience" page and click <strong>"Publish App"</strong> (then confirm). Apps left in "Testing" status cause Google tokens to <strong>expire every 7 days</strong>, requiring you to re-authorize repeatedly. Publishing to production (even unverified) gives you long-lived tokens — Google will show a one-time "unverified app" warning when you authorize, which is normal for self-hosted apps.</li>
                </ul>
              </li>
            </ul>
          </div>

          <div className="instruction-step">
            <strong>4. Create OAuth 2.0 Credentials</strong>
            <ul>
              <li>Click the <strong>&#9776; hamburger menu</strong> (top-left) → "APIs &amp; Services" → "Credentials"</li>
              <li>Click "Create credentials" → "OAuth client ID"</li>
              <li>Application type: "Desktop app"</li>
              <li>Name: "PosterFlow Desktop"</li>
              <li>Click "Create"</li>
              <li>⚠️ <strong>Copy your Client ID and Client Secret immediately</strong> — Google no longer allows you to view the secret after leaving this page</li>
            </ul>
          </div>

          <div className="instruction-step">
            <strong>5. Generate Refresh Token (Recommended Method)</strong>
            <ul>
              <li>Install rclone on your computer: <a href="https://rclone.org/install/" target="_blank" rel="noopener noreferrer">rclone.org/install</a></li>
              <li>Open terminal and run:</li>
              <li><code>rclone authorize "drive" "YOUR_CLIENT_ID" "YOUR_CLIENT_SECRET"</code></li>
              <li>A browser window will open — sign in and authorize</li>
              <li>The terminal will display a token JSON — copy the entire output from &#123; to &#125;</li>
              <li>Paste it in the "Refresh Token" field below</li>
            </ul>
          </div>

          <div className="instruction-step alternate-method">
            <strong>Alternative: OAuth Playground (if rclone not available)</strong>
            <ul>
              <li>Use <a href="https://developers.google.com/oauthplayground/" target="_blank" rel="noopener noreferrer">OAuth 2.0 Playground</a></li>
              <li>Click settings gear → Check "Use your own OAuth credentials"</li>
              <li>Paste your Client ID and Client Secret</li>
              <li>In Step 1: Select "Drive API v3" → <code>https://www.googleapis.com/auth/drive</code></li>
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
              onClick={onToggleClientSecretVisibility}
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
              onClick={onToggleTokenVisibility}
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
