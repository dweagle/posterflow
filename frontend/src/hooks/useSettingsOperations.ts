import { useState } from 'react'
import {
  cleanupDatabase,
  DatabaseStats,
  downloadBackup,
  getApiErrorMessage,
  getDatabaseStats,
  uploadBackup,
} from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface UseSettingsOperationsParams {
  showToast: (message: string, type?: ToastType) => void
}

export const useSettingsOperations = ({ showToast }: UseSettingsOperationsParams) => {
  const [databaseStats, setDatabaseStats] = useState<DatabaseStats | null>(null)
  const [loadingStats, setLoadingStats] = useState(false)
  const [cleanupLoading, setCleanupLoading] = useState(false)
  const [showCleanupConfirm, setShowCleanupConfirm] = useState(false)

  const [backupLoading, setBackupLoading] = useState(false)
  const [restoreLoading, setRestoreLoading] = useState(false)
  const [showRestartModal, setShowRestartModal] = useState(false)

  const fetchDatabaseStats = async () => {
    setLoadingStats(true)
    try {
      const stats = await getDatabaseStats()
      setDatabaseStats(stats)
    } catch (error) {
      console.error('Error fetching database stats:', error)
      showToast('Failed to load database statistics', 'error')
    } finally {
      setLoadingStats(false)
    }
  }

  const handleDatabaseCleanup = async () => {
    setCleanupLoading(true)
    try {
      const result = await cleanupDatabase()
      showToast(`Successfully cleaned ${result.deleted} orphaned records`, 'success')
      await fetchDatabaseStats()
      setShowCleanupConfirm(false)
    } catch (error) {
      console.error('Database cleanup failed:', error)
      showToast(getApiErrorMessage(error, 'Failed to clean database'), 'error')
    } finally {
      setCleanupLoading(false)
    }
  }

  const handleDownloadBackup = async () => {
    setBackupLoading(true)
    try {
      const blob = await downloadBackup()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `posterflow_backup_${new Date().toISOString().slice(0, 10)}.zip`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      showToast('Backup downloaded successfully', 'success')
    } catch (error) {
      console.error('Backup failed:', error)
      showToast('Failed to create backup', 'error')
    } finally {
      setBackupLoading(false)
    }
  }

  const handleRestoreBackup = async (file: File) => {
    setRestoreLoading(true)
    try {
      const result = await uploadBackup(file)
      showToast(result.message, 'success')
      setShowRestartModal(true)
    } catch (error) {
      console.error('Restore failed:', error)
      showToast(getApiErrorMessage(error, 'Failed to restore backup'), 'error')
    } finally {
      setRestoreLoading(false)
    }
  }
  return {
    databaseStats,
    loadingStats,
    cleanupLoading,
    showCleanupConfirm,
    setShowCleanupConfirm,
    backupLoading,
    restoreLoading,
    showRestartModal,
    setShowRestartModal,
    fetchDatabaseStats,
    handleDatabaseCleanup,
    handleDownloadBackup,
    handleRestoreBackup,
  }
}
