import { fireEvent, screen, within } from '@testing-library/react'

// Helpers for the overlays' ListPicker (a button that draws its own list in the page).
// Opening or closing a picker changes the page, so never call these inside waitFor: it
// re-runs on those very changes and never settles.

// The picker's button, by its label (a <label for> or an aria-label).
export const picker = (label: string | RegExp): HTMLButtonElement =>
  screen.getByLabelText(label, { selector: 'button[role="combobox"]' }) as HTMLButtonElement

const listOf = (button: HTMLElement): HTMLElement | null => document.getElementById(button.getAttribute('aria-controls') ?? '')

// The labels a picker offers, in order ([] when it is disabled or empty). Leaves it closed.
export const pickerOptions = (label: string | RegExp): (string | null)[] => {
  const button = picker(label)
  fireEvent.click(button)
  const list = listOf(button)
  const labels = list ? within(list).getAllByRole('option').map((o) => o.textContent) : []
  if (button.getAttribute('aria-expanded') === 'true') fireEvent.click(button)
  return labels
}

// Choose an entry by its label, the way a click on it would.
export const pick = (label: string | RegExp, option: string | RegExp): void => {
  const button = picker(label)
  fireEvent.click(button)
  const list = listOf(button)
  if (!list) throw new Error(`The ${String(label)} picker did not open`)
  fireEvent.click(within(list).getByRole('option', { name: option }))
}
