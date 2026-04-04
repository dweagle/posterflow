import { useState } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { setAppPassword, clearAppPassword } from '../../api/auth'
import { Eye, EyeOff, ShieldCheck, ShieldOff } from 'lucide-react'
import './SettingsSecuritySection.css'

type ToastType = 'success' | 'error' | 'info'

interface SettingsSecuritySectionProps {
  showToast: (message: string, type?: ToastType) => void
}

function PasswordInput({
  id,
  label,
  value,
  onChange,
  placeholder,
  disabled,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
}) {
  const [visible, setVisible] = useState(false)
  return (
    <div className="security-field">
      <label htmlFor={id}>{label}</label>
      <div className="security-input-row">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          autoComplete="new-password"
        />
        <button
          type="button"
          className="security-eye-btn"
          onClick={() => setVisible((v) => !v)}
          tabIndex={-1}
          aria-label={visible ? 'Hide password' : 'Show password'}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </div>
  )
}

function SettingsSecuritySection({ showToast }: SettingsSecuritySectionProps) {
  const { isAuthRequired } = useAuth()

  // Set-password form (used when no password is set)
  const [setNew, setSetNew] = useState('')
  const [setConfirm, setSetConfirm] = useState('')
  const [setSaving, setSetSaving] = useState(false)

  // Change-password form
  const [changeCurrent, setChangeCurrent] = useState('')
  const [changeNew, setChangeNew] = useState('')
  const [changeConfirm, setChangeConfirm] = useState('')
  const [changeSaving, setChangeSaving] = useState(false)

  // Remove password form
  const [removeCurrent, setRemoveCurrent] = useState('')
  const [removeSaving, setRemoveSaving] = useState(false)

  const handleSetPassword = async () => {
    if (!setNew.trim()) { showToast('Password cannot be empty', 'error'); return }
    if (setNew !== setConfirm) { showToast('Passwords do not match', 'error'); return }
    if (setNew.length < 6) { showToast('Password must be at least 6 characters', 'error'); return }
    try {
      setSetSaving(true)
      await setAppPassword('', setNew)
      showToast('Password set successfully')
      setSetNew('')
      setSetConfirm('')
      // Refresh auth state — reload so AuthContext re-initialises
      window.location.reload()
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showToast(detail || 'Failed to set password', 'error')
    } finally {
      setSetSaving(false)
    }
  }

  const handleChangePassword = async () => {
    if (!changeCurrent.trim()) { showToast('Enter your current password', 'error'); return }
    if (!changeNew.trim()) { showToast('New password cannot be empty', 'error'); return }
    if (changeNew !== changeConfirm) { showToast('New passwords do not match', 'error'); return }
    if (changeNew.length < 6) { showToast('Password must be at least 6 characters', 'error'); return }
    try {
      setChangeSaving(true)
      await setAppPassword(changeCurrent, changeNew)
      showToast('Password changed successfully')
      setChangeCurrent('')
      setChangeNew('')
      setChangeConfirm('')
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showToast(detail || 'Failed to change password', 'error')
    } finally {
      setChangeSaving(false)
    }
  }

  const handleRemovePassword = async () => {
    if (!removeCurrent.trim()) { showToast('Enter your current password to confirm', 'error'); return }
    try {
      setRemoveSaving(true)
      await clearAppPassword(removeCurrent)
      showToast('Password removed – app is now open access')
      setRemoveCurrent('')
      window.location.reload()
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showToast(detail || 'Failed to remove password', 'error')
    } finally {
      setRemoveSaving(false)
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section-header">
        <h2>App Password</h2>
        <p className="setting-description">
          Protect the PosterFlow UI and all API endpoints with a password. When set, anyone accessing the app must enter it before proceeding.
        </p>
      </div>

      <div className="security-status-row">
        {isAuthRequired ? (
          <span className="security-badge security-badge--active">
            <ShieldCheck size={14} /> Password active
          </span>
        ) : (
          <span className="security-badge security-badge--inactive">
            <ShieldOff size={14} /> No password set — app is open access
          </span>
        )}
      </div>

      {!isAuthRequired ? (
        <div className="security-form-block">
          <h3>Set a Password</h3>
          <PasswordInput id="set-new" label="New password" value={setNew} onChange={setSetNew} placeholder="Minimum 6 characters" />
          <PasswordInput id="set-confirm" label="Confirm password" value={setConfirm} onChange={setSetConfirm} placeholder="Repeat password" />
          <button className="btn-primary" onClick={handleSetPassword} disabled={setSaving}>
            {setSaving ? 'Saving…' : 'Set Password'}
          </button>
        </div>
      ) : (
        <>
          <div className="security-form-block">
            <h3>Change Password</h3>
            <PasswordInput id="change-current" label="Current password" value={changeCurrent} onChange={setChangeCurrent} />
            <PasswordInput id="change-new" label="New password" value={changeNew} onChange={setChangeNew} placeholder="Minimum 6 characters" />
            <PasswordInput id="change-confirm" label="Confirm new password" value={changeConfirm} onChange={setChangeConfirm} />
            <button className="btn-primary" onClick={handleChangePassword} disabled={changeSaving}>
              {changeSaving ? 'Saving…' : 'Change Password'}
            </button>
          </div>

          <div className="security-form-block security-form-block--danger">
            <h3>Remove Password</h3>
            <p className="setting-description">This will make the app open access to anyone who can reach it on the network.</p>
            <PasswordInput id="remove-current" label="Current password" value={removeCurrent} onChange={setRemoveCurrent} />
            <button className="btn-danger" onClick={handleRemovePassword} disabled={removeSaving}>
              {removeSaving ? 'Removing…' : 'Remove Password'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export default SettingsSecuritySection
