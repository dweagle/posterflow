import { useState } from 'react'
import { ChevronRight, Download, List } from 'lucide-react'
import { DriveUsage, FallbackItem } from '../../api/posterManager'
import DriveUsageModal from './DriveUsageModal'
import { SLOT_LABELS } from './itemSort'

export const formatItemLine = (item: FallbackItem) => {
  // Strip trailing (YYYY) from title if year is already appended separately
  const cleanTitle = item.year ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : item.title
  let line = item.year ? `${cleanTitle} (${item.year})` : cleanTitle
  if (item.type === 'show' && item.season != null) {
    line += item.season === 0 ? ' — Specials' : ` — Season ${item.season}`
  }
  if (item.slot) line += ` — ${SLOT_LABELS[item.slot] ?? item.slot}`
  if (item.type === 'collection') line += ' [Collection]'
  return line
}

export const downloadText = (filename: string, header: string[], items: FallbackItem[]) => {
  const content = [...header, '', ...items.map(formatItemLine)].join('\n')
  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

type DriveUsagePanelProps = {
  usage: DriveUsage[]
  itemsForDrive: (driveId: string) => FallbackItem[]
  outrankedForDrive: (driveId: string) => FallbackItem[]
  noun?: string
  filePrefix?: string
}

// The "Last Rename — Drive Usage" card: collapsible bar list with a View/Download per
// drive. Shared by the poster and artwork priority scopes — only the data differs.
export default function DriveUsagePanel({
  usage,
  itemsForDrive,
  outrankedForDrive,
  noun = 'poster',
  filePrefix = 'drive-usage',
}: DriveUsagePanelProps) {
  const [open, setOpen] = useState(false)
  const [openDrive, setOpenDrive] = useState<DriveUsage | null>(null)

  if (usage.length === 0) return null

  // Bars scale to the largest used+outranked total so every segment fits the track.
  const usageScale = Math.max(1, ...usage.map((d) => d.count + (d.outranked ?? 0)))

  const handleDownload = (entry: DriveUsage, mode: 'used' | 'outranked' = 'used') => {
    const items = mode === 'used' ? itemsForDrive(entry.drive_id) : outrankedForDrive(entry.drive_id)
    const slug = entry.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
    const header = mode === 'used'
      ? `# ${noun[0].toUpperCase()}${noun.slice(1)}s used from ${entry.name}${entry.style ? ` (${entry.style})` : ''}`
      : `# ${noun[0].toUpperCase()}${noun.slice(1)}s matched from ${entry.name}${entry.style ? ` (${entry.style})` : ''} but not used — a higher-priority drive covered them`
    downloadText(`${filePrefix}-${slug || 'drive'}${mode === 'outranked' ? '-not-used' : ''}.txt`, [
      header,
      `# Generated: ${new Date().toLocaleString()}`,
      `# Total: ${items.length}`,
    ], items)
  }

  return (
    <>
      <div className="style-usage-panel">
        <button
          type="button"
          className="style-usage-header drive-usage-toggle"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
        >
          <span className="style-usage-title">
            <ChevronRight size={15} className={`drive-usage-chevron${open ? ' open' : ''}`} />
            Last Rename — Drive Usage
          </span>
          <span className="style-usage-total">
            {usage.length} drive{usage.length !== 1 ? 's' : ''} used
          </span>
        </button>
        {open && (
          <>
            <p className="drive-usage-hint">
              Drives are listed by {noun}s used, not priority order. The dimmed end of a bar is {noun}s that
              matched from that drive but were covered by a higher-priority drive.
            </p>
            <div className="style-usage-bars">
              {usage.map((entry) => {
                const outranked = entry.outranked ?? 0
                const usedPct = (entry.count / usageScale) * 100
                const outrankedPct = (outranked / usageScale) * 100
                const styleKey = (entry.style ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')
                return (
                  <div
                    key={entry.drive_id}
                    className={`style-usage-row drive-usage-row${styleKey ? ` style-usage-${styleKey}` : ''}`}
                  >
                    <span className="drive-usage-name" title={entry.name}>{entry.name}</span>
                    {entry.style && <span className={`style-badge style-${styleKey}`}>{entry.style}</span>}
                    <div className={`style-usage-bar-track${outranked > 0 ? ' has-extension' : ''}`}>
                      <div className="style-usage-bar-fill" style={{ width: `${usedPct}%` }} />
                      {outranked > 0 && (
                        <div className="drive-usage-outranked-fill" style={{ width: `${outrankedPct}%` }} />
                      )}
                    </div>
                    <span className="style-usage-count">{entry.count.toLocaleString()}</span>
                    <span
                      className="drive-usage-outranked-count"
                      title={outranked > 0 ? `${outranked.toLocaleString()} matched but a higher-priority drive was used` : undefined}
                    >
                      {outranked > 0 ? `+${outranked.toLocaleString()}` : ''}
                    </span>
                    <button
                      className="style-usage-download-btn"
                      onClick={() => setOpenDrive(entry)}
                      disabled={entry.count === 0 && outranked === 0}
                      title={`View ${noun}s from ${entry.name}`}
                    >
                      <List size={13} />
                      View
                    </button>
                    <button
                      className="style-usage-download-btn"
                      onClick={() => handleDownload(entry)}
                      disabled={entry.count === 0}
                      title={entry.count > 0 ? `Download ${noun} list for ${entry.name}` : `No ${noun}s used from this drive`}
                    >
                      <Download size={13} />
                    </button>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
      {openDrive && (
        <DriveUsageModal
          drive={openDrive}
          items={itemsForDrive(openDrive.drive_id)}
          outrankedItems={outrankedForDrive(openDrive.drive_id)}
          noun={noun}
          onClose={() => setOpenDrive(null)}
          onDownload={(mode) => handleDownload(openDrive, mode)}
        />
      )}
    </>
  )
}
