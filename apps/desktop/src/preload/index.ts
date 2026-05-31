import { contextBridge, ipcRenderer } from 'electron'

const api = {
  setAlwaysOnTop: (value: boolean) => ipcRenderer.invoke('window:set-always-on-top', value),
  minimizeWindow: () => ipcRenderer.invoke('window:minimize'),
  closeWindow: () => ipcRenderer.invoke('window:close'),
}

contextBridge.exposeInMainWorld('amadeus', api)

export type AmadeusDesktopApi = typeof api
