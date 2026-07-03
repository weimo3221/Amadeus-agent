/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AGENT_WS_URL?: string
  readonly VITE_AGENT_HTTP_URL?: string
  readonly VITE_AMADEUS_SESSION_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
