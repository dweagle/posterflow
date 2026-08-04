/// <reference types="vite/client" />

// Release version baked in at build time (see vite.config.ts define)
declare const __APP_VERSION__: string

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
