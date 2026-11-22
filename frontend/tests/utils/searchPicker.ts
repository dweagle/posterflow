import { fireEvent, screen, within } from '@testing-library/react'

// Helpers for the overlays' SearchPicker: a box to type in whose matches are listed under it.

// The box, by its label (the list under it carries the same one).
export const searchBox = (label: string | RegExp): HTMLInputElement =>
  screen.getByLabelText(label, { selector: 'input[role="combobox"]' }) as HTMLInputElement

const listed = (input: HTMLInputElement): (string | null)[] => {
  const list = document.getElementById(input.getAttribute('aria-controls') ?? '')
  // An entry is read out in one piece ("Pixar (Plex)"): its name, and beside it where it is from.
  return list ? within(list).queryAllByRole('option').map((o) => o.getAttribute('aria-label') ?? o.textContent) : []
}

// What a click on the box lists, in order. Leaves it closed and its value alone.
export const searchOptions = (label: string | RegExp): (string | null)[] => {
  const input = searchBox(label)
  fireEvent.focus(input)
  const labels = listed(input)
  fireEvent.blur(input)
  return labels
}

// Type into the box and say what is listed for it. The box stays open, as it would.
export const searchType = (label: string | RegExp, text: string): (string | null)[] => {
  const input = searchBox(label)
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  return listed(input)
}

// Pick an entry by its label, the way a click on it would.
export const searchPick = (label: string | RegExp, option: string | RegExp): void => {
  const input = searchBox(label)
  fireEvent.focus(input)
  const list = document.getElementById(input.getAttribute('aria-controls') ?? '')
  if (!list) throw new Error(`The ${String(label)} box listed nothing`)
  fireEvent.click(within(list).getByRole('option', { name: option }))
}

// Type a value and press Enter: what was typed becomes a value of its own (a chip).
export const searchCommit = (label: string | RegExp, text: string): void => {
  const input = searchBox(label)
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.keyDown(input, { key: 'Enter' })
}
