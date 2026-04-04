import { getData, postData } from './http'

export interface AuthStatus {
  password_set: boolean
}

export interface VerifyResult {
  valid: boolean
}

export interface AuthActionResult {
  message: string
}

export const getAuthStatus = async (): Promise<AuthStatus> => {
  return getData<AuthStatus>('/api/auth/status')
}

export const verifyPassword = async (password: string): Promise<VerifyResult> => {
  return postData<VerifyResult>('/api/auth/verify', { password })
}

export const setAppPassword = async (
  currentPassword: string,
  newPassword: string
): Promise<AuthActionResult> => {
  return postData<AuthActionResult>('/api/auth/set', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}

export const clearAppPassword = async (
  currentPassword: string
): Promise<AuthActionResult> => {
  return postData<AuthActionResult>('/api/auth/clear', {
    current_password: currentPassword,
  })
}
