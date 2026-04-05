import { getData, putData } from './http'

export interface HookConfig {
  enabled: boolean
  script: string
  run_when: 'always' | 'scheduled' | 'manual'
  trigger_on: 'always' | 'success' | 'failure'
  label?: string
}

export interface HooksResponse {
  hooks: Record<string, HookConfig>
  scripts_dir: string
}

export interface AvailableScriptsResponse {
  scripts: string[]
  scripts_dir: string
}

export const getAvailableScripts = (): Promise<AvailableScriptsResponse> =>
  getData<AvailableScriptsResponse>('/api/scripts/available')

export const getHooks = (): Promise<HooksResponse> =>
  getData<HooksResponse>('/api/scripts/hooks')

export const saveHooks = (hooks: Record<string, Omit<HookConfig, 'label'>>): Promise<{ message: string }> =>
  putData<{ message: string }>('/api/scripts/hooks', { hooks })
