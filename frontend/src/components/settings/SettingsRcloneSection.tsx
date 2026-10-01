import { Eye, EyeOff } from 'lucide-react'
import { GoogleDriveCredentialsGuide, GoogleCredentialsConflictNotice } from './GoogleDriveCredentialsGuide'

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
        <h2>Google Drive Access</h2>
        <p className="setting-description">
          Sync-only users need a service account. Poster makers who upload to their own Google Drive need an OAuth client. Set up one, not both.
        </p>
        <p className="setting-description">
          Saved values are shown directly. Use the eye button to hide or reveal sensitive fields.
        </p>
      </div>

      <GoogleDriveCredentialsGuide open={showInstructions} onToggle={onToggleInstructions} />

      <div className="rclone-form">
        <h3 className="credentials-path-title">Service account (sync only)</h3>

        <div className="form-group">
          <label>Service Account JSON Path</label>
          <input
            type="text"
            value={rcloneSettings.google_service_account_file}
            onChange={(e) => setRcloneSettings({ ...rcloneSettings, google_service_account_file: e.target.value })}
            placeholder="/config/service_accounts/my-service-account.json"
          />
          <small>
            Uploaded key files are stored under <code>/config/service_accounts</code>. Clear this field to use OAuth instead.
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

        <GoogleCredentialsConflictNotice
          serviceAccountFile={rcloneSettings.google_service_account_file}
          clientId={rcloneSettings.google_client_id}
          clientSecret={rcloneSettings.google_client_secret}
          token={rcloneSettings.google_token}
        />

        <h3 className="credentials-path-title">OAuth client (poster makers)</h3>

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
          <label>Google Drive Token (full JSON from rclone authorize)</label>
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

        <button className={`btn-save ${hasUnsaved ? 'btn-unsaved' : ''}`} onClick={onSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save Rclone Configuration'}
        </button>
      </div>
    </div>
  )
}

export default SettingsRcloneSection
