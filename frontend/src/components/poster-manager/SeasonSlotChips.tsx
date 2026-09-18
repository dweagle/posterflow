type SeasonSlotChipsProps = {
  // Seasons the library actually has for the show; nothing else is offered.
  seasons: number[]
  value: number | null
  onChange: (season: number | null) => void
  loading?: boolean
  posterLabel?: string
}

// Poster-or-season slot chips shared by the override pickers.
export default function SeasonSlotChips({ seasons, value, onChange, loading = false, posterLabel = 'Show poster' }: SeasonSlotChipsProps) {
  return (
    <>
      <button
        type="button"
        className={`poster-filter-btn${value == null ? ' active' : ''}`}
        onClick={() => onChange(null)}
      >
        {posterLabel}
      </button>
      {seasons.map((n) => (
        <button
          key={n}
          type="button"
          className={`poster-filter-btn${value === n ? ' active' : ''}`}
          onClick={() => onChange(n)}
        >
          {n === 0 ? 'Specials' : `Season ${n}`}
        </button>
      ))}
      {loading && <span className="season-slot-chips-note">Loading seasons…</span>}
      {!loading && seasons.length === 0 && (
        <span className="season-slot-chips-note">No seasons in the library for this show</span>
      )}
    </>
  )
}
