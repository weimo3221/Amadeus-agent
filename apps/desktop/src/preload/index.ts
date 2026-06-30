import { contextBridge, ipcRenderer } from 'electron'

interface MainUiInteractionStatus {
  exists: boolean
  destroyed: boolean
  visible: boolean
  minimized: boolean
  focused: boolean
  fullscreen: boolean
}

interface CompanionInteractionModePayload {
  interactive: boolean
  reason: string
  mainUi: MainUiInteractionStatus
}

const api = {
  setAlwaysOnTop: (value: boolean) => ipcRenderer.invoke('window:set-always-on-top', value),
  minimizeWindow: () => ipcRenderer.invoke('window:minimize'),
  toggleFullscreen: () => ipcRenderer.invoke('window:toggle-fullscreen'),
  isFullscreen: () => ipcRenderer.invoke('window:is-fullscreen'),
  closeWindow: () => ipcRenderer.invoke('window:close'),
  openMainUi: (sessionId?: string) => ipcRenderer.invoke('window:open-main-ui', sessionId),
  onCompanionInteractionMode: (
    listener: (payload: CompanionInteractionModePayload) => void,
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      payload: CompanionInteractionModePayload,
    ) => listener(payload)
    ipcRenderer.on('companion:interaction-mode', handler)
    return () => ipcRenderer.removeListener('companion:interaction-mode', handler)
  },
  onGlobalCursor: (
    listener: (payload: { cursor: { x: number, y: number }, window: { x: number, y: number, width: number, height: number } }) => void,
  ) => {
    const handler = (
      _event: Electron.IpcRendererEvent,
      payload: { cursor: { x: number, y: number }, window: { x: number, y: number, width: number, height: number } },
    ) => listener(payload)
    ipcRenderer.on('desktop:global-cursor', handler)
    return () => ipcRenderer.removeListener('desktop:global-cursor', handler)
  },
}

contextBridge.exposeInMainWorld('amadeus', api)

export type AmadeusDesktopApi = typeof api
