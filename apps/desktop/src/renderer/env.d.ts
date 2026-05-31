import type { AmadeusDesktopApi } from '../preload'

declare global {
  interface Window {
    amadeus: AmadeusDesktopApi
    PIXI?: unknown
  }
}

export {}
