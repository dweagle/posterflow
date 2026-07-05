import { useEffect, useState } from 'react'

type NumberFieldProps = {
  value: number
  onChange: (n: number) => void
  // Committed on blur when the field is left empty/invalid.
  fallback: number
  min?: number
  max?: number
  className?: string
  style?: React.CSSProperties
}

// A plain number input (no spinner) that lets you clear the field while typing
// instead of snapping to a fallback: it commits any valid number on change and
// only falls back to `fallback` on blur if the field is left empty/invalid.
function NumberField({ value, onChange, fallback, min, max, className, style }: NumberFieldProps) {
  const [text, setText] = useState(String(value))

  // Clamp to [min, max]. The spinner arrows are hidden globally, so the browser never
  // enforces these on typed input — without this a typed value (e.g. a 999px border) would
  // reach the backend and invert a PIL crop box, failing the whole run.
  const clamp = (n: number) => {
    if (min !== undefined) n = Math.max(min, n)
    if (max !== undefined) n = Math.min(max, n)
    return n
  }

  // Resync when the value changes from outside (load, reset, color cycling) — but
  // not while the field is mid-edit (which keeps `value` in sync already).
  useEffect(() => {
    setText((prev) => (parseInt(prev, 10) === value ? prev : String(value)))
  }, [value])

  return (
    <input
      type="number"
      className={className}
      style={style}
      value={text}
      min={min}
      max={max}
      onChange={(e) => {
        setText(e.target.value)
        const n = parseInt(e.target.value, 10)
        // Clamp only the upper bound live so a runaway value can't be committed, but leave
        // the lower bound to blur so intermediate keystrokes (e.g. "1" on the way to "15")
        // aren't snapped up mid-edit.
        if (!Number.isNaN(n)) onChange(max !== undefined ? Math.min(max, n) : n)
      }}
      onBlur={() => {
        const n = parseInt(text, 10)
        const committed = Number.isNaN(n) ? fallback : clamp(n)
        onChange(committed)
        setText(String(committed))
      }}
    />
  )
}

export default NumberField
