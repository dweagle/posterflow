/** English display name for an ISO 639-1 code ("ko" → "Korean"); the upper-cased code when unknown. */
export function languageName(code: string | null | undefined): string {
  const c = (code ?? '').trim().toLowerCase()
  if (!c) return ''
  try {
    const name = new Intl.DisplayNames(['en'], { type: 'language' }).of(c)
    if (name && name.toLowerCase() !== c) return name
  } catch {
    // unsupported runtime or malformed code: fall through to the raw code
  }
  return c.toUpperCase()
}
