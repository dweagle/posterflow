import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Download, FileQuestion, Info, Loader2, XCircle } from 'lucide-react'
import type { MouseEvent } from 'react'
import {
  type MatchReportCandidate,
  type MatchReportResponse,
  type MatchReportVerdict,
  downloadMatchReport,
  getApiErrorMessage,
  getUnmatchedMatchReport,
  startUnmatchedMatchReport,
} from '../../api/client'
import { cancelJob } from '../../api/jobs'
import { useToast } from '../Toast'
import { formatDateShortTime } from '../../utils/datetime'

export interface MatchReportItem {
  media_type: 'movies' | 'series' | 'collections'
  title: string
  year: number | null
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  missing_seasons?: number[]
  missing_main?: boolean
  // null/undefined = poster report; set for artwork unmatched rows.
  artwork_type?: 'logo' | 'background' | 'squareart' | null
}

const ARTWORK_SCOPE_LABEL: Record<string, string> = {
  logo: 'Logo', background: 'Background', squareart: 'Square Art',
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'expired'])

type MatchReportModalProps = {
  item: MatchReportItem
  onClose: () => void
  // How often to ask the server about the report job (tests shorten it).
  pollIntervalMs?: number
}

type IdBag = { tmdb_id?: number | null; tvdb_id?: number | null; imdb_id?: string | null }

// Filename-style id tags, matching the report text ("{tvdb-475672}" …), color-coded
// per id kind so agreeing/disagreeing ids line up visually across rows.
function IdTags({ ids }: { ids: IdBag }) {
  const kinds = (['tmdb', 'tvdb', 'imdb'] as const).filter((k) => ids[`${k}_id`])
  if (kinds.length === 0) return <span className="match-report-muted">(none)</span>
  return (
    <>
      {kinds.map((k) => (
        <span key={k} className={`mr-id mr-id--${k}`}>{`{${k}-${ids[`${k}_id`]}}`}</span>
      ))}
    </>
  )
}

function VerdictLine({ verdict }: { verdict: MatchReportVerdict }) {
  const icon =
    verdict.level === 'problem' ? <XCircle size={15} /> :
    verdict.level === 'ok' ? <CheckCircle2 size={15} /> :
    <Info size={15} />
  return (
    <div className={`match-report-verdict match-report-verdict--${verdict.level}`}>
      {icon}
      <span>{verdict.message}</span>
    </div>
  )
}

function CandidateRow({ candidate }: { candidate: MatchReportCandidate }) {
  return (
    <div className="match-report-candidate">
      <div className="match-report-candidate-head">
        {candidate.matched ? <CheckCircle2 size={13} className="mr-ok" /> : <XCircle size={13} className="mr-problem" />}
        <span className="match-report-candidate-title">
          {candidate.title}{candidate.year ? ` (${candidate.year})` : ''}
        </span>
        {candidate.drive && <span className="match-report-drive-badge">{candidate.drive}</span>}
      </div>
      <div className="match-report-mono">
        <IdTags ids={candidate} /> — {candidate.matched ? `matched ${candidate.reason}` : `rejected: ${candidate.reason}`}
      </div>
      {candidate.files.map((file) => (
        <div key={file} className="match-report-mono match-report-file">{file}</div>
      ))}
    </div>
  )
}

// A 502/504 is the reverse proxy giving up before Posterflow answered — name it, since
// "check the app log" sends the user looking for an error that was never written.
function describeFailure(error: unknown, fallback: string): string {
  const status = (error as { response?: { status?: number } } | null)?.response?.status
  if (status === 502 || status === 504) {
    return `${fallback}: the request timed out at a reverse proxy (HTTP ${status}) before Posterflow answered.`
  }
  const detail = getApiErrorMessage(error, '')
  if (status) return `${fallback} (HTTP ${status})${detail ? `: ${detail}` : ''}.`
  return detail ? `${fallback}: ${detail}` : `${fallback}. Check the app log for details.`
}

function MatchReportModal({ item, onClose, pollIntervalMs = 1500 }: MatchReportModalProps) {
  const { showToast } = useToast()
  const [response, setResponse] = useState<MatchReportResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState('Queuing the report…')

  useEffect(() => {
    let cancelled = false
    let finished = false
    let jobId: number | null = null
    let timer: number | undefined

    const finish = (apply: () => void) => {
      finished = true
      if (!cancelled) apply()
    }

    const poll = async () => {
      if (cancelled || jobId == null) return
      try {
        const status = await getUnmatchedMatchReport(jobId)
        if (cancelled) return
        if (status.status === 'completed' && status.result) {
          finish(() => setResponse(status.result))
          return
        }
        if (status.status === 'expired') {
          finish(() => setError('This report expired before it could be shown. Close and open it again.'))
          return
        }
        if (TERMINAL_STATUSES.has(status.status)) {
          finish(() => setError(
            status.error
              ? `Failed to build the match report: ${status.error}`
              : 'Failed to build the match report. Check the app log for details.',
          ))
          return
        }
        setProgress(status.message || 'Working…')
      } catch (err) {
        finish(() => setError(describeFailure(err, 'Lost track of the match report job')))
        return
      }
      timer = window.setTimeout(poll, pollIntervalMs)
    }

    startUnmatchedMatchReport({
      media_type: item.media_type,
      title: item.title,
      year: item.year,
      tmdb_id: item.tmdb_id ?? null,
      tvdb_id: item.tvdb_id ?? null,
      imdb_id: item.imdb_id ?? null,
      missing_seasons: item.missing_seasons ?? [],
      missing_main: item.missing_main ?? false,
      artwork_type: item.artwork_type ?? null,
    })
      .then((started) => {
        jobId = started.job_id
        if (cancelled) {
          cancelJob(started.job_id).catch(() => {})
          return
        }
        setProgress(started.message || 'Queued…')
        void poll()
      })
      .catch((err) => finish(() => setError(describeFailure(err, 'Failed to start the match report'))))

    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
      // Closing the modal mid-build stops the job instead of leaving it scanning drives.
      if (jobId != null && !finished) cancelJob(jobId).catch(() => {})
    }
    // The modal mounts fresh per item; the item identity is fixed for its lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose()
  }

  const handleDownload = () => {
    if (!response) return
    downloadMatchReport(response)
    showToast(`Saved ${response.filename}`)
  }

  const report = response?.report
  const placed = report?.placed ?? null
  const scopeSuffix = item.artwork_type ? ` — ${ARTWORK_SCOPE_LABEL[item.artwork_type]}` : ''
  const titleLine = `${item.title}${item.year ? ` (${item.year})` : ''}${scopeSuffix}`

  return (
    <div className="modal-overlay match-report-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal match-report-modal">
        <div className="modal-header">
          <h2><FileQuestion size={18} style={{ verticalAlign: 'text-bottom', marginRight: '0.4rem' }} />Match Report — {titleLine}</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          {error ? (
            <div className="match-report-error"><AlertCircle size={16} /><span>{error}</span></div>
          ) : !report ? (
            <div className="match-report-loading">
              <Loader2 size={18} className="spin-icon" />
              <span>{progress}</span>
            </div>
          ) : (
            <>
              <div className="match-report-footnote match-report-footnote--top">
                v{report.app_version} — {report.generated_at}. The download includes everything below plus the raw data.
              </div>

              <div className="match-report-section">
                <h4>Verdict</h4>
                {report.verdicts.map((verdict, idx) => <VerdictLine key={idx} verdict={verdict} />)}
              </div>

              <div className="match-report-section">
                <h4>Library record</h4>
                {report.library.records.length === 0 ? (
                  <div className="match-report-muted">
                    {report.library.manual_entry ? 'Manual media entry' : 'Not found in the configured Sonarr/Radarr/media server instances'}
                  </div>
                ) : (
                  report.library.records.map((record, idx) => (
                    <div key={idx}>
                      <div>[{record.instance}] {record.title}{record.year ? ` (${record.year})` : ''}</div>
                      <table className="match-report-table">
                        <tbody>
                          <tr><td>ids</td><td className="match-report-mono"><IdTags ids={record} /></td></tr>
                          {record.folder && (
                            <tr>
                              <td>folder</td>
                              <td className="match-report-mono">
                                {record.folder}{!record.folder_has_year && <span className="mr-problem">  ⚠ no (year) in path</span>}
                              </td>
                            </tr>
                          )}
                          {!record.source && (
                            <tr>
                              <td>state</td>
                              <td className="match-report-muted">
                                {[
                                  record.monitored != null && (record.monitored ? 'monitored' : 'unmonitored'),
                                  record.status,
                                  record.available != null && (record.available ? 'downloaded' : 'not downloaded'),
                                ].filter(Boolean).join(' · ')}
                              </td>
                            </tr>
                          )}
                          {report.item.media_type === 'series' && (
                            <tr>
                              <td>seasons</td>
                              <td className="match-report-muted">
                                {record.seasons_with_episodes.length > 0
                                  ? `${record.seasons_with_episodes.length} with episodes (${record.seasons_with_episodes.join(', ')})`
                                  : 'none with episodes'}
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  ))
                )}
              </div>

              {!item.artwork_type && (
                <div className="match-report-section">
                  <h4>Placed folder</h4>
                  {!placed ? (
                    <div className="match-report-muted">No placed poster found in the destination for this title</div>
                  ) : (
                    <table className="match-report-table">
                      <tbody>
                        <tr>
                          <td>folder</td>
                          <td className="match-report-mono">
                            {placed.name}
                            {placed.year == null && report.item.media_type !== 'collections' && (
                              <span className="mr-problem">  ⚠ no (year) in name</span>
                            )}
                          </td>
                        </tr>
                        <tr><td>files</td><td className="match-report-mono">{placed.files.join(', ') || 'none'}</td></tr>
                        <tr>
                          <td>read as</td>
                          <td className="match-report-mono">
                            {placed.type ?? 'unknown'}{placed.year ? ` (${placed.year})` : ''} · <IdTags ids={placed} />
                          </td>
                        </tr>
                        <tr>
                          <td>matcher</td>
                          <td>
                            {placed.matched
                              ? <span className="mr-ok">matched {placed.reason}</span>
                              : <span className="mr-problem">not matched{placed.reason ? `: ${placed.reason}` : ''}</span>}
                          </td>
                        </tr>
                        {placed.siblings.length > 0 && (
                          <tr><td>also</td><td className="match-report-muted">{placed.siblings.join(', ')}</td></tr>
                        )}
                      </tbody>
                    </table>
                  )}
                </div>
              )}

              <div className="match-report-section">
                <h4>ID cross-check</h4>
                <table className="match-report-table">
                  <tbody>
                    <tr><td><span className="mr-source-label">{report.library.ids_source || 'library'}</span></td><td className="match-report-mono"><IdTags ids={report.library.effective_ids} /></td></tr>
                    {(['tvdb', 'tmdb'] as const).map((source) => {
                      const reference = report.reference[source]
                      return (
                        <tr key={source}>
                          <td>{source.toUpperCase()}</td>
                          <td className="match-report-mono">
                            {reference.skipped ? <span className="match-report-muted">skipped: {reference.skipped}</span>
                              : reference.error ? <span className="mr-problem">{reference.error}</span>
                              : reference.missing ? <span className="match-report-muted">{reference.missing}</span>
                              : <><IdTags ids={reference} /> → {reference.title}{reference.year ? ` (${reference.year})` : ''}</>}
                          </td>
                        </tr>
                      )
                    })}
                    {(report.reference.plex.servers ?? [report.reference.plex]).map((entry, index) => (
                      <tr key={`server-${index}`}>
                        <td>{entry.type === 'jellyfin' ? 'JELLYFIN' : entry.type === 'plex' ? 'PLEX' : 'SERVER'}</td>
                        <td className="match-report-mono">
                          {'skipped' in entry && entry.skipped ? <span className="match-report-muted">skipped: {entry.skipped}</span>
                            : entry.error ? <span className="mr-problem">{entry.instance ? `${entry.instance}: ` : ''}{entry.error}</span>
                            : entry.missing ? <span className="match-report-muted">{typeof entry.missing === 'string' ? entry.missing : `not found on ${entry.instance}`}</span>
                            : <><IdTags ids={entry} /> → {entry.title}{entry.year ? ` (${entry.year})` : ''}{entry.library ? ` in ${entry.library} [${entry.instance}]` : ''}</>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {(() => {
                const aliasRows: { label: string; names: string[]; total?: number }[] = []
                const record = report.library.records[0]
                if (record && record.alternate_titles.length > 0) {
                  aliasRows.push({ label: report.library.ids_source || 'library', names: record.alternate_titles })
                }
                for (const source of ['tvdb', 'tmdb'] as const) {
                  const reference = report.reference[source]
                  if (reference.alternate_titles && reference.alternate_titles.length > 0) {
                    aliasRows.push({ label: source.toUpperCase(), names: reference.alternate_titles, total: reference.alternate_titles_total })
                  }
                }
                if (aliasRows.length === 0) return null
                return (
                  <div className="match-report-section">
                    <h4>Alternate titles</h4>
                    <table className="match-report-table">
                      <tbody>
                        {aliasRows.map((row) => (
                          <tr key={row.label}>
                            <td><span className="mr-source-label">{row.label}</span></td>
                            <td className="match-report-muted">
                              {row.names.join(', ')}
                              {row.total && row.total > row.names.length ? ` (+${row.total - row.names.length} more)` : ''}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              })()}

              <div className="match-report-section">
                <h4>
                  Drive candidates ({report.candidates.shown} shown, {report.candidates.considered} similar titles checked)
                </h4>
                {report.candidates.items.length === 0 ? (
                  <div className="match-report-muted">
                    No matching or near-miss {item.artwork_type ? `${ARTWORK_SCOPE_LABEL[item.artwork_type].toLowerCase()} files` : 'posters'} found on the subscribed drives
                  </div>
                ) : (
                  report.candidates.items.map((candidate, idx) => <CandidateRow key={idx} candidate={candidate} />)
                )}
                {report.candidates.omitted > 0 && (
                  <div className="match-report-muted">…and {report.candidates.omitted} more near-miss candidate(s) in the file</div>
                )}
              </div>

              <div className="match-report-section">
                <h4>Drives scanned</h4>
                {report.drives.error && <div className="mr-problem">{report.drives.error}</div>}
                {report.drives.scanned.map((drive) => (
                  <div key={drive.name} className="match-report-mono">
                    [{drive.style_type}] {drive.name} — {drive.missing ? 'MISSING LOCALLY' : `synced ${drive.last_synced ? formatDateShortTime(drive.last_synced) : 'never'}`}
                  </div>
                ))}
                <div className="match-report-muted">{report.drives.total_assets.toLocaleString()} assets in the scan index</div>
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <div className="modal-footer-actions">
            <button className="btn-secondary" onClick={onClose}>Close</button>
            <button className="btn-primary" onClick={handleDownload} disabled={!response} title="Save as a .txt you can drop into Discord or a support thread">
              <Download size={15} /> Download report
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default MatchReportModal
