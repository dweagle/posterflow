// Whether the options hold every value a rule names (an overlay field's show_when, a type's placement_lock).
export const optionsMatch = (when: Record<string, unknown>, options: Record<string, unknown>): boolean =>
  Object.entries(when).every(([key, value]) => (Array.isArray(value) ? value : [value]).map(String).includes(String(options[key] ?? '')))
