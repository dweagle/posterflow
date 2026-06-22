import { submitCommunityListItems, type ListItemInput, getApiErrorMessage } from '../../api/client'

type ShowToast = (message: string, type?: 'success' | 'error' | 'info') => void

/**
 * Publish prepared items to the community Lists tab in ≤200-item batches (the
 * edge-function cap), tallying inserted/skipped into one toast. Shared by the
 * Unmatched and Poster Style modals; each builds its own inputs and owns the
 * publishing/login state — this just does the upload + result reporting.
 */
export async function publishToCommunityLists(
  inputs: ListItemInput[],
  token: string,
  showToast: ShowToast,
): Promise<void> {
  if (!inputs.length) return
  try {
    let inserted = 0
    let skipped = 0
    for (let i = 0; i < inputs.length; i += 200) {
      const res = await submitCommunityListItems({ items: inputs.slice(i, i + 200), discord_token: token })
      inserted += res.inserted
      skipped += res.skipped
    }
    showToast(
      `Added ${inserted} item${inserted !== 1 ? 's' : ''} to Community Lists${skipped ? ` (${skipped} already listed)` : ''}`,
      inserted > 0 ? 'success' : 'info',
    )
  } catch (err) {
    showToast(getApiErrorMessage(err, 'Failed to add to Community Lists'), 'error')
  }
}
