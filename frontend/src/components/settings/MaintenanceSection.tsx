import { Database, Trash2 } from 'lucide-react'
import { DatabaseStats } from '../../api/client'

type MaintenanceSectionProps = {
  databaseStats: DatabaseStats | null
  loadingStats: boolean
  cleanupLoading: boolean
  onFetchStats: () => void
  onStartCleanup: () => void
}

function MaintenanceSection({
  databaseStats,
  loadingStats,
  cleanupLoading,
  onFetchStats,
  onStartCleanup,
}: MaintenanceSectionProps) {
  const orphanedRecords = databaseStats?.orphaned_records ?? databaseStats?.sample_records ?? []

  return (
    <div className="settings-section">
      <div className="settings-section-header">
        <h2>Database Maintenance</h2>
        <p className="setting-description">
          Clean up orphaned database records for files that no longer exist on disk.
        </p>
      </div>

      <div className="backup-section">
        <div className="backup-item">
          <h3>Database Cleanup</h3>
          <p>Remove database records for poster files that have been deleted from disk. This helps keep your database clean and improves performance.</p>

          {!databaseStats && !loadingStats && (
            <button className="btn-secondary" onClick={onFetchStats} style={{ marginTop: '1rem' }}>
              <Database size={16} style={{ marginRight: '0.5rem' }} />
              Check Database Health
            </button>
          )}

          {loadingStats && (
            <div style={{ marginTop: '1rem', color: '#aaa' }}>
              Loading database statistics...
            </div>
          )}

          {databaseStats && (
            <>
              <div className="database-stats">
                <div className="stat-row">
                  <span className="stat-label">Total Records:</span>
                  <span className="stat-value">{databaseStats.total_records.toLocaleString()}</span>
                </div>
                <div className="stat-row">
                  <span className="stat-label">Orphaned Records:</span>
                  <span className="stat-value" style={{ color: databaseStats.orphaned_count > 0 ? '#ffb74d' : '#4caf50' }}>
                    {databaseStats.orphaned_count.toLocaleString()}
                  </span>
                </div>
                {databaseStats.orphaned_count > 0 && Object.keys(databaseStats.by_location).length > 0 && (
                  <div className="stat-breakdown">
                    <h4>Breakdown by Drive:</h4>
                    {Object.entries(databaseStats.by_location).map(([driveName, count]) => (
                      <div key={driveName} className="stat-detail">
                        <span>{driveName}</span>
                        <span>{(count as number).toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {databaseStats.orphaned_count > 0 && orphanedRecords.length > 0 && (
                <div className="orphaned-records-list">
                  <h4 className="orphaned-records-title">
                    Orphaned Records
                    {orphanedRecords.length < databaseStats.orphaned_count && (
                      <span className="orphaned-records-note"> (showing {orphanedRecords.length} of {databaseStats.orphaned_count})</span>
                    )}
                  </h4>
                  <div className="orphaned-records-table">
                    <div className="orphaned-record-header">
                      <span>File / Location</span>
                      <span>Drive / Reason</span>
                    </div>
                    {orphanedRecords.map((record) => {
                      const parentDir = record.file_path
                        .replace(/\\/g, '/')
                        .split('/')
                        .slice(0, -1)
                        .join('/')
                      return (
                        <div key={record.id} className="orphaned-record-row">
                          <div className="orphaned-record-file-cell">
                            <span className="orphaned-record-file" title={record.file_path}>{record.file_name}</span>
                            <span className="orphaned-record-path" title={record.file_path}>{parentDir}</span>
                          </div>
                          <div className="orphaned-record-drive-cell">
                            <span className="orphaned-record-drive">{record.drive_name}</span>
                            {record.reason && (
                              <span className={`orphaned-reason-badge orphaned-reason-${record.reason.toLowerCase().replace(/\s+/g, '-')}`}>
                                {record.reason}
                              </span>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              <div style={{ marginTop: '1.5rem', display: 'flex', gap: '1rem' }}>
                <button className="btn-secondary" onClick={onFetchStats} disabled={loadingStats || cleanupLoading}>
                  <Database size={16} style={{ marginRight: '0.5rem' }} />
                  Refresh Stats
                </button>

                {databaseStats.orphaned_count > 0 && (
                  <button
                    className="btn-danger"
                    onClick={onStartCleanup}
                    disabled={cleanupLoading}
                  >
                    <Trash2 size={16} />
                    Clean Database ({databaseStats.orphaned_count.toLocaleString()} records)
                  </button>
                )}
              </div>

              {databaseStats.orphaned_count === 0 && (
                <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: 'rgba(76, 175, 80, 0.1)', borderRadius: '0.5rem', color: '#4caf50' }}>
                  ✓ Database is clean! No orphaned records found.
                </div>
              )}
            </>
          )}

          {databaseStats && databaseStats.orphaned_count > 0 && (
            <div className="backup-warning" style={{ marginTop: '1.5rem' }}>
              ⚠️ <strong>Warning:</strong> This action cannot be undone. Database records for deleted files will be permanently removed.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default MaintenanceSection
