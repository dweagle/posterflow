import { CheckCircle, Download } from 'lucide-react'
import { type MouseEvent } from 'react'
import { UnmatchedStats } from '../../api/client'

export type UnmatchedModalType = 'movies' | 'series' | 'collections' | 'seasons' | null

type UnmatchedItemsModalProps = {
  modalType: UnmatchedModalType
  unmatchedStats: UnmatchedStats | null
  modalDisplayLimit: number
  onClose: () => void
  onDownloadList: (type: Exclude<UnmatchedModalType, null>) => void
}

function UnmatchedItemsModal({
  modalType,
  unmatchedStats,
  modalDisplayLimit,
  onClose,
  onDownloadList,
}: UnmatchedItemsModalProps) {
  if (!modalType || !unmatchedStats?.unmatched) {
    return null
  }

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      onClose()
    }
  }

  let hasItems = false
  let itemCount = 0

  if (modalType === 'movies') {
    itemCount = unmatchedStats.unmatched.movies?.length || 0
    hasItems = itemCount > 0
  } else if (modalType === 'series') {
    itemCount = unmatchedStats.unmatched.series?.filter((s) => s.missing_main_poster).length || 0
    hasItems = itemCount > 0
  } else if (modalType === 'seasons') {
    itemCount = unmatchedStats.unmatched.series?.filter((s) => s.missing_seasons.length > 0).length || 0
    hasItems = itemCount > 0
  } else if (modalType === 'collections') {
    itemCount = unmatchedStats.unmatched.collections?.length || 0
    hasItems = itemCount > 0
  }

  if (!hasItems) {
    return (
      <div className="modal-overlay" onClick={handleOverlayClick}>
        <div className="modal-content schedule-modal">
          <div className="modal-header">
            <h2>
              {modalType === 'movies' && 'Movies'}
              {modalType === 'series' && 'Series'}
              {modalType === 'seasons' && 'Seasons'}
              {modalType === 'collections' && 'Collections'}
            </h2>
            <button className="modal-close" onClick={onClose}>×</button>
          </div>
          <div className="modal-body">
            <div className="success-modal-body">
              <CheckCircle className="success-icon" size={64} />
              <p className="success-message">All assets matched!</p>
            </div>
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal">
        <div className="modal-header">
          <h2>
            {modalType === 'movies' && 'Movies Missing Posters'}
            {modalType === 'series' && 'Series Missing Main Posters'}
            {modalType === 'seasons' && 'Series Missing Season Posters'}
            {modalType === 'collections' && 'Collections Missing Posters'}
          </h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          {modalType === 'movies' && unmatchedStats.unmatched.movies && (
            <div className="unmatched-list">
              <p className="list-count">{unmatchedStats.unmatched.movies.length} movies without posters</p>
              {unmatchedStats.unmatched.movies.length > modalDisplayLimit && (
                <p className="performance-warning">
                  ⚠️ Showing first {modalDisplayLimit} of {unmatchedStats.unmatched.movies.length} items for performance
                </p>
              )}
              {unmatchedStats.unmatched.movies.slice(0, modalDisplayLimit).map((item, idx) => (
                <div key={idx} className="unmatched-item">
                  <span className="item-title">{item.title}</span>
                  {item.year && <span className="item-year">({item.year})</span>}
                </div>
              ))}
            </div>
          )}

          {modalType === 'series' && unmatchedStats.unmatched.series && (
            <div className="unmatched-list">
              <p className="list-count">
                {unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length} series without main posters
              </p>
              {unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length > modalDisplayLimit && (
                <p className="performance-warning">
                  ⚠️ Showing first {modalDisplayLimit} of {unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length} items for performance
                </p>
              )}
              {unmatchedStats.unmatched.series
                .filter((s) => s.missing_main_poster)
                .slice(0, modalDisplayLimit)
                .map((item, idx) => (
                  <div key={idx} className="unmatched-item">
                    <span className="item-title">{item.title}</span>
                    {item.year && <span className="item-year">({item.year})</span>}
                  </div>
                ))}
            </div>
          )}

          {modalType === 'seasons' && unmatchedStats.unmatched.series && (
            <div className="unmatched-list">
              {unmatchedStats.unmatched.series.filter((s) => s.missing_seasons.length > 0).length > modalDisplayLimit && (
                <p className="performance-warning">
                  ⚠️ Showing first {modalDisplayLimit} of {unmatchedStats.unmatched.series.filter((s) => s.missing_seasons.length > 0).length} items for performance
                </p>
              )}
              {unmatchedStats.unmatched.series
                .filter((s) => s.missing_seasons.length > 0)
                .slice(0, modalDisplayLimit)
                .map((item, idx) => (
                  <div key={idx} className="unmatched-item-group">
                    <div className="item-title-row">
                      <span className="item-title">{item.title}</span>
                      {item.year && <span className="item-year">({item.year})</span>}
                    </div>
                    <div className="missing-seasons">
                      Missing: {item.missing_seasons.map((season) => `Season ${season}`).join(', ')}
                    </div>
                  </div>
                ))}
            </div>
          )}

          {modalType === 'collections' && unmatchedStats.unmatched.collections && (
            <div className="unmatched-list">
              <p className="list-count">{unmatchedStats.unmatched.collections.length} collections without posters</p>
              {unmatchedStats.unmatched.collections.length > modalDisplayLimit && (
                <p className="performance-warning">
                  ⚠️ Showing first {modalDisplayLimit} of {unmatchedStats.unmatched.collections.length} items for performance
                </p>
              )}
              {unmatchedStats.unmatched.collections.slice(0, modalDisplayLimit).map((item, idx) => (
                <div key={idx} className="unmatched-item">
                  <span className="item-title">{item.title}</span>
                  {item.year && <span className="item-year">({item.year})</span>}
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Close</button>
          <button
            className="btn-primary"
            onClick={() => onDownloadList(modalType)}
            title="Download full list as text file"
          >
            <Download size={18} />
            Download List
          </button>
        </div>
      </div>
    </div>
  )
}

export default UnmatchedItemsModal
