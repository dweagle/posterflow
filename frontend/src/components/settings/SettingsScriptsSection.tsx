import { useCallback, useEffect, useState } from 'react'
import { FileCode, RefreshCw } from 'lucide-react'
import { getAvailableScripts, getHooks, saveHooks, type HookConfig } from '../../api/scripts'
import './SettingsScriptsSection.css'

type ToastType = 'success' | 'error' | 'info'

interface SettingsScriptsSectionProps {
  showToast: (message: string, type?: ToastType) => void
}

const HOOK_ORDER = [
  'sync_one',
  'sync_all',
  'poster_workflow',
  'poster_renamer',
  'border_replacer',
  'unmatched',
]

type HooksState = Record<string, HookConfig>

function SettingsScriptsSection({ showToast }: SettingsScriptsSectionProps) {
  const [hooks, setHooks] = useState<HooksState>({})
  const [scriptsDir, setScriptsDir] = useState('/config/scripts')
  const [availableScripts, setAvailableScripts] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [refreshingScripts, setRefreshingScripts] = useState(false)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [hooksRes, scriptsRes] = await Promise.all([getHooks(), getAvailableScripts()])
      setHooks(hooksRes.hooks)
      setScriptsDir(hooksRes.scripts_dir)
      setAvailableScripts(scriptsRes.scripts)
    } catch {
      showToast('Failed to load scripts configuration', 'error')
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  const refreshScripts = async () => {
    setRefreshingScripts(true)
    try {
      const res = await getAvailableScripts()
      setAvailableScripts(res.scripts)
      showToast(`Found ${res.scripts.length} script(s)`, 'info')
    } catch {
      showToast('Failed to refresh scripts list', 'error')
    } finally {
      setRefreshingScripts(false)
    }
  }

  const updateHook = (key: string, field: keyof HookConfig, value: string | boolean) => {
    setHooks((prev) => ({
      ...prev,
      [key]: { ...prev[key], [field]: value },
    }))
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const payload: Record<string, Omit<HookConfig, 'label'>> = {}
      for (const key of HOOK_ORDER) {
        if (hooks[key]) {
          const { label: _label, ...rest } = hooks[key]
          payload[key] = rest
        }
      }
      await saveHooks(payload)
      showToast('Scripts configuration saved', 'success')
    } catch {
      showToast('Failed to save scripts configuration', 'error')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="scripts-loading">
        <span>Loading scripts configuration…</span>
      </div>
    )
  }

  return (
    <div className="scripts-section">
      <div className="scripts-info-banner">
        <FileCode size={16} className="scripts-info-icon" />
        <div>
          <p>
            Place executable scripts in <code>{scriptsDir}</code>. They will appear in the
            dropdowns below. Scripts receive three environment variables:
          </p>
          <ul>
            <li><code>POSTERFLOW_JOB</code> — hook key (e.g. <code>poster_workflow</code>)</li>
            <li><code>POSTERFLOW_STATUS</code> — <code>success</code> or <code>failure</code></li>
            <li><code>POSTERFLOW_TRIGGERED_BY</code> — <code>manual</code> or <code>scheduled</code></li>
          </ul>
          <p className="scripts-info-note">
            Scripts must be executable (<code>chmod +x</code>). They are run with a 60-second timeout
            and never block or fail the parent job.
          </p>
        </div>
      </div>

      <div className="scripts-table-header">
        <h3>Post-Job Hooks</h3>
        <button
          className="scripts-refresh-btn"
          onClick={refreshScripts}
          disabled={refreshingScripts}
          title="Refresh script list from disk"
        >
          <RefreshCw size={14} className={refreshingScripts ? 'spinning' : ''} />
          Refresh
        </button>
      </div>

      {availableScripts.length === 0 && (
        <div className="scripts-empty-notice">
          No scripts found in <code>{scriptsDir}</code>. Place your scripts there and click Refresh.
        </div>
      )}

      <div className="scripts-table">
        <div className="scripts-table-row scripts-table-row--header">
          <span>Job</span>
          <span>Enabled</span>
          <span>Script</span>
          <span>Run When</span>
          <span>Trigger On</span>
        </div>

        {HOOK_ORDER.map((key) => {
          const hook = hooks[key]
          if (!hook) return null
          return (
            <div key={key} className={`scripts-table-row ${hook.enabled ? 'scripts-table-row--active' : ''}`}>
              <span className="scripts-job-label">{hook.label ?? key}</span>

              <span className="scripts-toggle-cell">
                <label className="scripts-toggle">
                  <input
                    type="checkbox"
                    checked={hook.enabled}
                    onChange={(e) => updateHook(key, 'enabled', e.target.checked)}
                  />
                  <span className="scripts-toggle-slider" />
                </label>
              </span>

              <span>
                <select
                  className="scripts-select"
                  value={hook.script}
                  onChange={(e) => updateHook(key, 'script', e.target.value)}
                  disabled={!hook.enabled}
                >
                  <option value="">— none —</option>
                  {availableScripts.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </span>

              <span>
                <select
                  className="scripts-select"
                  value={hook.run_when}
                  onChange={(e) => updateHook(key, 'run_when', e.target.value)}
                  disabled={!hook.enabled}
                >
                  <option value="always">Always</option>
                  <option value="scheduled">Scheduled only</option>
                  <option value="manual">Manual only</option>
                </select>
              </span>

              <span>
                <select
                  className="scripts-select"
                  value={hook.trigger_on}
                  onChange={(e) => updateHook(key, 'trigger_on', e.target.value)}
                  disabled={!hook.enabled}
                >
                  <option value="always">Always</option>
                  <option value="success">Success only</option>
                  <option value="failure">Failure only</option>
                </select>
              </span>
            </div>
          )
        })}
      </div>

      <div className="scripts-footer">
        <button className="btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

export default SettingsScriptsSection
