import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import NewCommunityRequestModal from '../../src/components/poster-manager/NewCommunityRequestModal'

// ── Mock factories ────────────────────────────────────────────────────────────

const mockLogin = vi.fn()
const mockLogout = vi.fn()
const mockShowToast = vi.fn()
const mockSubmitCommunityRequest = vi.fn()
const mockSearchUnmatchedTmdb = vi.fn()
const mockOnClose = vi.fn()

let mockDiscordAuth = {
  isConnected: false,
  username: null as string | null,
  token: null as string | null,
  connecting: false,
  connectError: null as string | null,
  login: mockLogin,
  logout: mockLogout,
}

vi.mock('../../src/hooks/useDiscordAuth', () => ({
  useDiscordAuth: () => mockDiscordAuth,
}))

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  submitCommunityRequest: (...args: unknown[]) => mockSubmitCommunityRequest(...args),
  searchUnmatchedTmdb: (...args: unknown[]) => mockSearchUnmatchedTmdb(...args),
}))

// ── Helpers ───────────────────────────────────────────────────────────────────

const defaultProps = {
  tmdbApiKeyConfigured: true,
  onClose: mockOnClose,
}

function renderModal(props = {}) {
  return render(<NewCommunityRequestModal {...defaultProps} {...props} />)
}

const candidate = (over: Partial<Record<string, unknown>> = {}) => ({
  tmdb_id: 27205,
  title: 'Inception',
  year: 2010,
  media_type: 'movie',
  poster_url: null,
  imdb_id: null,
  tvdb_id: null,
  ...over,
})

// Type a query and run the TMDB search, waiting for results to render.
async function runSearch(user: ReturnType<typeof userEvent.setup>, query = 'Inception', waitForText = 'Inception: Encore') {
  await user.type(screen.getByPlaceholderText(/search tmdb/i), query)
  await user.click(screen.getByRole('button', { name: /^search$/i }))
  await waitFor(() => expect(screen.getByText(waitForText)).toBeTruthy())
}

// Two candidates so nothing is auto-selected (auto-select only fires for a single hit).
const TWO_CANDIDATES = {
  candidates: [
    candidate({ tmdb_id: 27205, title: 'Inception', year: 2010 }),
    candidate({ tmdb_id: 99, title: 'Inception: Encore', year: 2012 }),
  ],
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('NewCommunityRequestModal', () => {
  beforeEach(() => {
    mockDiscordAuth = {
      isConnected: false,
      username: null,
      token: null,
      connecting: false,
      connectError: null,
      login: mockLogin,
      logout: mockLogout,
    }
    mockSubmitCommunityRequest.mockResolvedValue({ status: 'created' })
    mockSearchUnmatchedTmdb.mockResolvedValue({ candidates: [] })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  // ── Search ──────────────────────────────────────────────────────────────────

  describe('search', () => {
    it('shows a warning when no TMDB API key is configured', () => {
      renderModal({ tmdbApiKeyConfigured: false })
      expect(screen.getByText(/no tmdb api key configured/i)).toBeTruthy()
    })

    it('renders candidates after a search', async () => {
      mockSearchUnmatchedTmdb.mockResolvedValue(TWO_CANDIDATES)
      const user = userEvent.setup()
      renderModal()
      await runSearch(user)
      expect(screen.getByText('Inception')).toBeTruthy()
      expect(screen.getByText('Inception: Encore')).toBeTruthy()
    })

    it('auto-selects the only candidate', async () => {
      mockSearchUnmatchedTmdb.mockResolvedValue({ candidates: [candidate()] })
      const user = userEvent.setup()
      renderModal()
      await user.type(screen.getByPlaceholderText(/search tmdb/i), 'Inception')
      await user.click(screen.getByRole('button', { name: /^search$/i }))
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /inception/i }).className.includes('selected')).toBe(true)
      })
    })

    it('shows a no-results message when the search is empty', async () => {
      mockSearchUnmatchedTmdb.mockResolvedValue({ candidates: [] })
      const user = userEvent.setup()
      renderModal()
      await user.type(screen.getByPlaceholderText(/search tmdb/i), 'Nope')
      await user.click(screen.getByRole('button', { name: /^search$/i }))
      await waitFor(() => expect(screen.getByText(/no tmdb results found/i)).toBeTruthy())
    })
  })

  // ── Submit button gating ────────────────────────────────────────────────────

  describe('submit button', () => {
    it('is disabled when Discord is not connected', () => {
      renderModal()
      expect((screen.getByRole('button', { name: /request poster/i }) as HTMLButtonElement).disabled).toBe(true)
    })

    it('enables once a custom title and poster style are provided', async () => {
      mockDiscordAuth = { ...mockDiscordAuth, isConnected: true, username: 'testuser', token: 'tok' }
      const user = userEvent.setup()
      renderModal()
      const submit = () => screen.getByRole('button', { name: /request poster/i }) as HTMLButtonElement
      expect(submit().disabled).toBe(true)
      await user.type(screen.getByPlaceholderText(/title of the movie/i), 'My Custom Movie')
      expect(submit().disabled).toBe(true) // still needs a style
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      expect(submit().disabled).toBe(false)
    })
  })

  // ── TMDB selection guard ────────────────────────────────────────────────────

  describe('TMDB selection guard', () => {
    beforeEach(() => {
      mockDiscordAuth = { ...mockDiscordAuth, isConnected: true, username: 'testuser', token: 'test-discord-token' }
      mockSearchUnmatchedTmdb.mockResolvedValue(TWO_CANDIDATES)
    })

    it('warns and blocks submit when results exist but none is picked', async () => {
      const user = userEvent.setup()
      renderModal()
      await runSearch(user)
      await user.type(screen.getByPlaceholderText(/title of the movie/i), 'Inception')
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      expect(mockSubmitCommunityRequest).not.toHaveBeenCalled()
      expect(mockShowToast).toHaveBeenCalledWith(
        expect.stringMatching(/pick the correct tmdb match/i),
        'error',
      )
      expect(screen.getByRole('alert')).toBeTruthy()
    })

    it('submits as custom (no tmdb_id) once "none of these fit" is checked', async () => {
      const user = userEvent.setup()
      renderModal()
      await runSearch(user)
      await user.type(screen.getByPlaceholderText(/title of the movie/i), 'Inception')
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      expect(mockSubmitCommunityRequest).not.toHaveBeenCalled()
      // Confirm it's custom, then submit goes through with a null tmdb_id.
      await user.click(screen.getByRole('checkbox', { name: /none of these fit/i }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockSubmitCommunityRequest).toHaveBeenCalledOnce())
      expect(mockSubmitCommunityRequest).toHaveBeenCalledWith(
        expect.objectContaining({ tmdb_id: null, title: 'Inception' }),
      )
    })

    it('submits with the tmdb_id once a candidate is picked', async () => {
      const user = userEvent.setup()
      renderModal()
      await runSearch(user)
      await user.click(screen.getByRole('button', { name: /inception: encore/i }))
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockSubmitCommunityRequest).toHaveBeenCalledOnce())
      expect(mockSubmitCommunityRequest).toHaveBeenCalledWith(
        expect.objectContaining({ tmdb_id: 99, title: 'Inception: Encore', media_type: 'movie' }),
      )
    })
  })

  // ── Submission (no TMDB results → pure custom) ───────────────────────────────

  describe('submission', () => {
    beforeEach(() => {
      mockDiscordAuth = { ...mockDiscordAuth, isConnected: true, username: 'testuser', token: 'test-discord-token' }
    })

    it('submits a custom request without searching', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.type(screen.getByPlaceholderText(/title of the movie/i), 'Obscure Indie Film')
      await user.click(screen.getByRole('button', { name: 'MM2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockSubmitCommunityRequest).toHaveBeenCalledOnce())
      expect(mockSubmitCommunityRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          tmdb_id: null,
          title: 'Obscure Indie Film',
          requested_by: 'testuser',
          discord_token: 'test-discord-token',
          style_tags: ['MM2K Style'],
        }),
      )
    })

    it('shows the already-requested toast', async () => {
      mockSubmitCommunityRequest.mockResolvedValue({ status: 'already_requested' })
      const user = userEvent.setup()
      renderModal()
      await user.type(screen.getByPlaceholderText(/title of the movie/i), 'Dune')
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Already requested!', 'info'))
    })

    it('shows an error toast on failure', async () => {
      mockSubmitCommunityRequest.mockRejectedValue(new Error('Network error'))
      const user = userEvent.setup()
      renderModal()
      await user.type(screen.getByPlaceholderText(/title of the movie/i), 'Dune')
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Failed to submit request', 'error'))
    })
  })

  // ── Close behaviour ─────────────────────────────────────────────────────────

  describe('close behaviour', () => {
    it('calls onClose when the X button is clicked', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: '×' }))
      expect(mockOnClose).toHaveBeenCalledOnce()
    })

    it('calls onClose when Cancel is clicked', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: /cancel/i }))
      expect(mockOnClose).toHaveBeenCalledOnce()
    })
  })
})
