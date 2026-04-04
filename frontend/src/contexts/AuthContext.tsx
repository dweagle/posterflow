import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { getAuthStatus, verifyPassword } from '../api/auth'
import { setAuthToken, AUTH_TOKEN_STORAGE_KEY } from '../api/http'

interface AuthContextType {
  /** True once the initial auth check has completed. */
  isReady: boolean
  /** True when the server has an app password configured. */
  isAuthRequired: boolean
  /** True when the user has provided a valid password (or no password is set). */
  isAuthenticated: boolean
  /** Attempt to unlock the app with the given password. Returns true on success. */
  authenticate: (password: string) => Promise<boolean>
  /** Clear the stored token and return to the lock screen. */
  logout: () => void
}

const AuthContext = createContext<AuthContextType>({
  isReady: true,
  isAuthRequired: false,
  isAuthenticated: true,
  authenticate: async () => false,
  logout: () => {},
})

export const useAuth = () => useContext(AuthContext)

interface AuthProviderProps {
  children: ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [isReady, setIsReady] = useState(false)
  const [isAuthRequired, setIsAuthRequired] = useState(false)
  const [isAuthenticated, setIsAuthenticated] = useState(false)

  // On mount: check whether the backend requires a password, then validate any stored token
  useEffect(() => {
    const init = async () => {
      try {
        const status = await getAuthStatus()
        if (!status.password_set) {
          // No password configured – always open
          setIsAuthRequired(false)
          setIsAuthenticated(true)
        } else {
          setIsAuthRequired(true)
          // Try to auto-authenticate with a stored token
          const stored = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
          if (stored) {
            try {
              const result = await verifyPassword(stored)
              setIsAuthenticated(result.valid)
              if (!result.valid) {
                // Token no longer valid (e.g. password was changed) – clear it
                setAuthToken(null)
              }
            } catch {
              setAuthToken(null)
              setIsAuthenticated(false)
            }
          } else {
            setIsAuthenticated(false)
          }
        }
      } catch {
        // If we can't reach the backend at all, let the app render normally
        // (other API calls will surface the real error)
        setIsAuthenticated(true)
      } finally {
        setIsReady(true)
      }
    }

    init()
  }, [])

  // Listen for 401 events fired by the axios interceptor
  useEffect(() => {
    const handleUnauthorized = () => {
      if (isAuthRequired) {
        setIsAuthenticated(false)
        setAuthToken(null)
      }
    }
    window.addEventListener('auth:unauthorized', handleUnauthorized)
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized)
  }, [isAuthRequired])

  const authenticate = async (password: string): Promise<boolean> => {
    try {
      const result = await verifyPassword(password)
      if (result.valid) {
        setAuthToken(password)
        setIsAuthenticated(true)
      }
      return result.valid
    } catch {
      return false
    }
  }

  const logout = () => {
    setAuthToken(null)
    setIsAuthenticated(false)
  }

  return (
    <AuthContext.Provider value={{ isReady, isAuthRequired, isAuthenticated, authenticate, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
