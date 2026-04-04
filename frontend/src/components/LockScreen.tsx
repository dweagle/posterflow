import { useState, FormEvent, KeyboardEvent } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { Lock } from 'lucide-react'
import './LockScreen.css'

function LockScreen() {
  const { authenticate } = useAuth()
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault()
    if (!password.trim()) return

    setLoading(true)
    setError('')

    const valid = await authenticate(password)
    if (!valid) {
      setError('Incorrect password')
      setPassword('')
    }
    setLoading(false)
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSubmit()
  }

  return (
    <div className="lock-screen">
      <div className="lock-card">
        <div className="lock-icon">
          <Lock size={32} />
        </div>
        <h1 className="lock-title">PosterFlow</h1>
        <p className="lock-subtitle">Enter your password to continue</p>

        <form className="lock-form" onSubmit={handleSubmit}>
          <input
            type="password"
            className={`lock-input${error ? ' lock-input--error' : ''}`}
            placeholder="Password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
              if (error) setError('')
            }}
            onKeyDown={handleKeyDown}
            autoFocus
            disabled={loading}
          />
          {error && <p className="lock-error">{error}</p>}
          <button
            type="submit"
            className="lock-btn"
            disabled={loading || !password.trim()}
          >
            {loading ? 'Verifying…' : 'Unlock'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default LockScreen
