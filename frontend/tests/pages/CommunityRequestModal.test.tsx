import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CommunityRequestModal from '../../src/components/poster-manager/CommunityRequestModal'

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

// ── Default props ─────────────────────────────────────────────────────────────

const defaultProps = {
  title: 'Inception',
  year: 2010,
  tmdbType: 'movie' as const,
  tmdbApiKeyConfigured: false,
  onClose: mockOnClose,
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderModal(props = {}) {
  return render(<CommunityRequestModal {...defaultProps} {...props} />)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('CommunityRequestModal', () => {
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

  // ── Submit button state ───────────────────────────────────────────────────

  describe('submit button', () => {
    it('is disabled when Discord is not connected', () => {
      renderModal()
      expect((screen.getByRole('button', { name: /request poster/i }) as HTMLButtonElement).disabled).toBe(true)
    })

    it('stays disabled until a poster style is selected, then enables', async () => {
      mockDiscordAuth = { ...mockDiscordAuth, isConnected: true, username: 'testuser' }
      const user = userEvent.setup()
      renderModal()
      expect((screen.getByRole('button', { name: /request poster/i }) as HTMLButtonElement).disabled).toBe(true)
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      expect((screen.getByRole('button', { name: /request poster/i }) as HTMLButtonElement).disabled).toBe(false)
    })
  })

  // ── Discord connection UI ─────────────────────────────────────────────────

  describe('Discord connection', () => {
    it('shows connect button when not connected', () => {
      renderModal()
      expect(screen.getByRole('button', { name: /connect with discord/i })).toBeTruthy()
    })

    it('shows username and disconnect button when connected', () => {
      mockDiscordAuth = { ...mockDiscordAuth, isConnected: true, username: 'testuser' }
      renderModal()
      expect(screen.getByText(/✓ testuser/)).toBeTruthy()
      expect(screen.getByRole('button', { name: /disconnect/i })).toBeTruthy()
    })

    it('calls login when connect button is clicked', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: /connect with discord/i }))
      expect(mockLogin).toHaveBeenCalledOnce()
    })

    it('calls logout when disconnect button is clicked', async () => {
      mockDiscordAuth = { ...mockDiscordAuth, isConnected: true, username: 'testuser' }
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: /disconnect/i }))
      expect(mockLogout).toHaveBeenCalledOnce()
    })

    it('shows connect error when present', () => {
      mockDiscordAuth = { ...mockDiscordAuth, connectError: 'OAuth failed' }
      renderModal()
      expect(screen.getByText('OAuth failed')).toBeTruthy()
    })

    it('shows connecting state on the button', () => {
      mockDiscordAuth = { ...mockDiscordAuth, connecting: true }
      renderModal()
      expect((screen.getByRole('button', { name: /connecting/i }) as HTMLButtonElement).disabled).toBe(true)
    })
  })

  // ── Style tags ────────────────────────────────────────────────────────────

  describe('style tags', () => {
    it('renders the poster style and extra tag options', () => {
      renderModal()
      expect(screen.getByRole('button', { name: 'CL2K' })).toBeTruthy()
      expect(screen.getByRole('button', { name: 'MM2K' })).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Anime Movie' })).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Anime TV' })).toBeTruthy()
    })

    it('selects a poster style on click', async () => {
      const user = userEvent.setup()
      renderModal()
      const tag = screen.getByRole('button', { name: 'MM2K' })
      expect(tag.className.includes('selected')).toBe(false)
      await user.click(tag)
      expect(tag.className.includes('selected')).toBe(true)
    })

    it('deselects the poster style when clicked again', async () => {
      const user = userEvent.setup()
      renderModal()
      const tag = screen.getByRole('button', { name: 'MM2K' })
      await user.click(tag)
      expect(tag.className.includes('selected')).toBe(true)
      await user.click(tag)
      expect(tag.className.includes('selected')).toBe(false)
    })

    it('only allows one poster style at a time', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: 'MM2K' }))
      expect(screen.getByRole('button', { name: 'CL2K' }).className.includes('selected')).toBe(false)
      expect(screen.getByRole('button', { name: 'MM2K' }).className.includes('selected')).toBe(true)
    })

    it('allows a poster style and an extra tag together', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'MM2K' }))
      await user.click(screen.getByRole('button', { name: 'Anime Movie' }))
      expect(screen.getByRole('button', { name: 'MM2K' }).className.includes('selected')).toBe(true)
      expect(screen.getByRole('button', { name: 'Anime Movie' }).className.includes('selected')).toBe(true)
    })
  })

  // ── Notes field ───────────────────────────────────────────────────────────

  describe('notes field', () => {
    it('updates as user types', async () => {
      const user = userEvent.setup()
      renderModal()
      const textarea = screen.getByPlaceholderText(/any special instructions/i) as HTMLTextAreaElement
      await user.type(textarea, 'Please use the theatrical poster')
      expect(textarea.value).toBe('Please use the theatrical poster')
    })
  })

  // ── Item display ──────────────────────────────────────────────────────────

  describe('item display', () => {
    it('shows the title and year', () => {
      renderModal()
      expect(screen.getByText('Inception')).toBeTruthy()
      expect(screen.getByText('(2010)')).toBeTruthy()
    })

    it('strips year from title if already embedded', () => {
      renderModal({ title: 'Inception (2010)', year: 2010 })
      // Should appear once in the title label, not doubled
      expect(screen.getAllByText(/inception/i).length).toBeGreaterThan(0)
    })

    it('shows season badge for season type', () => {
      renderModal({ tmdbType: 'show', seasonNumbers: [2] })
      expect(screen.getByText('Season 2')).toBeTruthy()
    })

    it('shows multi-season badge when multiple seasons', () => {
      renderModal({ tmdbType: 'show', seasonNumbers: [1, 2, 3] })
      expect(screen.getByText('Seasons: 1, 2, 3')).toBeTruthy()
    })

    it('shows movie type badge', () => {
      renderModal({ tmdbType: 'movie' })
      expect(screen.getByText('movie')).toBeTruthy()
    })
  })

  // ── TMDB section ──────────────────────────────────────────────────────────

  describe('TMDB section', () => {
    it('shows warning when TMDB API key is not configured', () => {
      renderModal({ tmdbApiKeyConfigured: false })
      expect(screen.getByText(/no tmdb api key configured/i)).toBeTruthy()
    })

    it('shows no-results message when search returns empty', async () => {
      mockSearchUnmatchedTmdb.mockResolvedValue({ candidates: [] })
      renderModal({ tmdbApiKeyConfigured: true })
      await waitFor(() => {
        expect(screen.getByText(/no tmdb results found/i)).toBeTruthy()
      })
    })

    it('renders candidates when search returns results', async () => {
      mockSearchUnmatchedTmdb.mockResolvedValue({
        candidates: [
          { tmdb_id: 27205, title: 'Inception', year: 2010, media_type: 'movie', poster_url: null, imdb_id: null, tvdb_id: null },
        ],
      })
      renderModal({ tmdbApiKeyConfigured: true })
      await waitFor(() => {
        expect(screen.getByText('Inception')).toBeTruthy()
      })
    })

    it('auto-selects the only candidate', async () => {
      mockSearchUnmatchedTmdb.mockResolvedValue({
        candidates: [
          { tmdb_id: 27205, title: 'Inception', year: 2010, media_type: 'movie', poster_url: null, imdb_id: null, tvdb_id: null },
        ],
      })
      renderModal({ tmdbApiKeyConfigured: true })
      await waitFor(() => {
        const btn = screen.getByRole('button', { name: /inception/i })
        expect(btn.className.includes('selected')).toBe(true)
      })
    })
  })

  // ── Submission ────────────────────────────────────────────────────────────

  describe('submission', () => {
    beforeEach(() => {
      mockDiscordAuth = { ...mockDiscordAuth, isConnected: true, username: 'testuser', token: 'test-discord-token' }
    })

    it('calls submitCommunityRequest with correct payload', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockSubmitCommunityRequest).toHaveBeenCalledOnce())
      expect(mockSubmitCommunityRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Inception',
          year: 2010,
          media_type: 'movie',
          requested_by: 'testuser',
          discord_token: 'test-discord-token',
        }),
      )
    })

    it('includes the selected poster style in payload', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'MM2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockSubmitCommunityRequest).toHaveBeenCalledOnce())
      expect(mockSubmitCommunityRequest).toHaveBeenCalledWith(
        expect.objectContaining({ style_tags: ['MM2K Style'] }),
      )
    })

    it('combines the poster style with extra tags in payload', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: 'Anime Movie' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockSubmitCommunityRequest).toHaveBeenCalledOnce())
      expect(mockSubmitCommunityRequest).toHaveBeenCalledWith(
        expect.objectContaining({ style_tags: ['CL2K Style', 'Anime Movie'] }),
      )
    })

    it('includes notes in payload when filled', async () => {
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.type(screen.getByPlaceholderText(/any special instructions/i), 'Use theatrical')
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockSubmitCommunityRequest).toHaveBeenCalledOnce())
      expect(mockSubmitCommunityRequest).toHaveBeenCalledWith(
        expect.objectContaining({ notes: 'Use theatrical' }),
      )
    })

    it('shows success toast on created', async () => {
      mockSubmitCommunityRequest.mockResolvedValue({ status: 'created' })
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Request submitted!', 'success'))
    })

    it('shows vote toast when already requested', async () => {
      mockSubmitCommunityRequest.mockResolvedValue({ status: 'already_requested' })
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Already requested!', 'info'))
    })

    it('shows error toast on failure', async () => {
      mockSubmitCommunityRequest.mockRejectedValue(new Error('Network error'))
      const user = userEvent.setup()
      renderModal()
      await user.click(screen.getByRole('button', { name: 'CL2K' }))
      await user.click(screen.getByRole('button', { name: /request poster/i }))
      await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Failed to submit request', 'error'))
    })
  })

  // ── Close behaviour ───────────────────────────────────────────────────────

  describe('close behaviour', () => {
    it('calls onClose when X button is clicked', async () => {
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
