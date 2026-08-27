import { useMemo, useState } from 'react'
import { ChevronRight, Download, List } from 'lucide-react'
import { CompareCandidate, CompareTarget, DriveUsage, FallbackItem } from '../../api/posterManager'
import DriveUsageModal, { ALL_DRIVES_ID } from './DriveUsageModal'
import { SLOT_LABELS } from './itemSort'

export const formatItemLine = (item: FallbackItem) => {
  // Strip trailing (YYYY) from title if year is already appended separately
  const cleanTitle = item.year ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : item.title
  let line = item.year ? `${cleanTitle} (${item.year})` : cleanTitle
  if (item.type === 'show' && item.season != null) {
    line += item.season === 0 ? ' - Specials' : ` - Season ${item.season}`
  }
  if (item.slot) line += ` - ${SLOT_LABELS[item.slot] ?? item.slot}`
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
  compareForItem?: (item: FallbackItem, target: CompareTarget) => CompareCandidate[]
  availableCountFor?: (item: FallbackItem, target: CompareTarget) => number
  overrideDomain?: 'poster' | 'artwork'
  noun?: string
  filePrefix?: string
  title?: string
  defaultOpen?: boolean
  collapsible?: boolean
}

// The "Last Rename — Drive Usage" card, shared by the poster and artwork scopes.
export default function DriveUsagePanel({
  usage,
  itemsForDrive,
  outrankedForDrive,
  compareForItem,
  availableCountFor,
  overrideDomain = 'poster',
  noun = 'poster',
  filePrefix = 'drive-usage',
  title = 'Last Rename - Drive Usage',
  defaultOpen = false,
  collapsible = true,
}: DriveUsagePanelProps) {
  const [open, setOpen] = useState(defaultOpen)
  const expanded = open || !collapsible

  // Name column sized to the panel's longest drive name.
  const nameColWidth = useMemo(() => {
    if (usage.length === 0) return undefined
    try {
      const ctx = document.createElement('canvas').getContext('2d')
      if (!ctx) return undefined
      ctx.font = `13.6px ${getComputedStyle(document.body).fontFamily}`
      const widest = Math.max(...usage.map((d) => ctx.measureText(d.name).width))
      return Math.min(Math.ceil(widest) + 8, 256)
    } catch {
      return undefined
    }
  }, [usage])
  const [openDrive, setOpenDrive] = useState<DriveUsage | null>(null)

  if (usage.length === 0) return null

  // Sqrt bars scaled to the second-largest drive; only the off-scale drive passes 88%.
  const totals = usage.map((d) => d.count + (d.outranked ?? 0))
  const maxTotal = Math.max(1, ...totals)
  const usageScale = Math.max(1, [...totals].sort((a, b) => b - a).find((t) => t < maxTotal) ?? maxTotal)
  const scaledPct = (n: number) => (n > 0 ? Math.max(1.5, Math.sqrt(n / usageScale) * 88) : 0)

  // Aggregate "All Drives" pseudo-entry: first stop in the modal's drive navigation.
  const allEntry: DriveUsage = {
    drive_id: ALL_DRIVES_ID,
    name: 'All Drives',
    count: usage.reduce((a, d) => a + d.count, 0),
    outranked: usage.reduce((a, d) => a + (d.outranked ?? 0), 0),
  }
  const navList = [allEntry, ...usage]

  const itemsFor = (driveId: string) =>
    driveId === ALL_DRIVES_ID
      ? usage.flatMap((u) => itemsForDrive(u.drive_id).map((it) => ({ ...it, drive_name: u.name, drive_style: u.style })))
      : itemsForDrive(driveId)

  const outrankedFor = (driveId: string) => {
    if (driveId !== ALL_DRIVES_ID) return outrankedForDrive(driveId)
    // No dedupe: each drive's tagged candidate rides along for the aggregate badges.
    return usage.flatMap((u) =>
      outrankedForDrive(u.drive_id).map((it) => ({ ...it, drive_name: u.name, drive_style: u.style }))
    )
  }

  const handleDownload = (entry: DriveUsage, mode: 'used' | 'outranked' = 'used') => {
    let items = mode === 'used' ? itemsFor(entry.drive_id) : outrankedFor(entry.drive_id)
    if (entry.drive_id === ALL_DRIVES_ID && mode === 'outranked') {
      // One txt line per title is enough for the aggregate.
      const seen = new Set<string>()
      items = items.filter((it) => {
        const key = `${it.type}::${it.title}::${it.year}::${it.season ?? null}::${it.slot ?? ''}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
    }
    const slug = entry.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
    const header = mode === 'used'
      ? `# ${noun[0].toUpperCase()}${noun.slice(1)}s used from ${entry.name}${entry.style ? ` (${entry.style})` : ''}`
      : `# ${noun[0].toUpperCase()}${noun.slice(1)}s matched from ${entry.name}${entry.style ? ` (${entry.style})` : ''} but not used - a higher-priority drive covered them`
    downloadText(`${filePrefix}-${slug || 'drive'}${mode === 'outranked' ? '-not-used' : ''}.txt`, [
      header,
      `# Generated: ${new Date().toLocaleString()}`,
      `# Total: ${items.length}`,
    ], items)
  }

  return (
    <>
      <div className="style-usage-panel">
        {collapsible ? (
          <button
            type="button"
            className="style-usage-header drive-usage-toggle"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
          >
            <span className="style-usage-title">
              <ChevronRight size={15} className={`drive-usage-chevron${open ? ' open' : ''}`} />
              {title}
            </span>
            <span className="style-usage-total">
              {usage.length} drive{usage.length !== 1 ? 's' : ''} used
            </span>
          </button>
        ) : (
          <div className="style-usage-header">
            <span className="style-usage-title">{title}</span>
            <span className="style-usage-total">
              {usage.length} drive{usage.length !== 1 ? 's' : ''} used
            </span>
          </div>
        )}
        {expanded && (
          <>
            <div className="drive-usage-hint-row">
              <p className="drive-usage-hint">
                Drives are listed by {noun}s used, not priority order. The dimmed end of a bar is {noun}s that
                matched from that drive but were covered by a higher-priority drive.
              </p>
              <button
                className="style-usage-download-btn"
                onClick={() => setOpenDrive(allEntry)}
                title={`View all ${noun}s across every drive`}
              >
                <List size={13} />
                View All
              </button>
            </div>
            <div className="style-usage-bars drive-usage-bars">
              {usage.map((entry) => {
                const outranked = entry.outranked ?? 0
                const total = entry.count + outranked
                const capped = total > usageScale
                const totalPct = capped ? 100 : scaledPct(total)
                const usedPct = capped
                  ? (total > 0 ? (entry.count / total) * 100 : 0)
                  : Math.min(totalPct, scaledPct(entry.count))
                const outrankedPct = totalPct - usedPct
                const styleKey = (entry.style ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')
                return (
                  <div
                    key={entry.drive_id}
                    className={`style-usage-row drive-usage-row${styleKey ? ` style-usage-${styleKey}` : ''}`}
                  >
                    <span
                      className="drive-usage-name"
                      title={entry.name}
                      style={nameColWidth ? ({ '--name-col': `${nameColWidth}px` } as React.CSSProperties) : undefined}
                    >
                      {entry.name}
                    </span>
                    {entry.style && <span className={`style-badge style-${styleKey}`}>{entry.style}</span>}
                    <div
                      className={`style-usage-bar-track${outranked > 0 ? ' has-extension' : ''}${capped ? ' drive-usage-bar-capped' : ''}`}
                    >
                      <div className="style-usage-bar-fill" style={{ width: `${usedPct}%` }} />
                      {outranked > 0 && (
                        <div className="drive-usage-outranked-fill" style={{ width: `${outrankedPct}%` }} />
                      )}
                    </div>
                    <span className="style-usage-count">{entry.count.toLocaleString()}</span>
                    <span
                      className={`drive-usage-outranked-count${outranked > 0 ? ' drive-usage-tip' : ''}`}
                      data-tooltip={outranked > 0 ? `${outranked.toLocaleString()} matched but a higher-priority drive was used` : undefined}
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
      {openDrive && (() => {
        const openIndex = navList.findIndex((u) => u.drive_id === openDrive.drive_id)
        return (
          <DriveUsageModal
            drive={openDrive}
            items={itemsFor(openDrive.drive_id)}
            outrankedItems={outrankedFor(openDrive.drive_id)}
            compareForItem={compareForItem}
            availableCountFor={availableCountFor}
            overrideDomain={overrideDomain}
            noun={noun}
            onClose={() => setOpenDrive(null)}
            onDownload={(mode) => handleDownload(openDrive, mode)}
            onNavigateDrive={(delta) => {
              const next = navList[openIndex + delta]
              if (next) setOpenDrive(next)
            }}
            hasPrevDrive={openIndex > 0}
            hasNextDrive={openIndex >= 0 && openIndex < navList.length - 1}
          />
        )
      })()}
    </>
  )
}
