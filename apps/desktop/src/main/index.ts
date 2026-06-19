import { electronApp, is, optimizer } from '@electron-toolkit/utils'
import { app, BrowserWindow, ipcMain, screen } from 'electron'
import { appendFileSync, mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

const projectRoot = resolve(__dirname, '../../../..')
const logDir = join(projectRoot, 'logs')
const runtimeLogPath = join(logDir, 'desktop-runtime.log')
const isE2eSmoke = process.env.AMADEUS_E2E_SMOKE === '1'

if (is.dev) {
  process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = 'true'
}

function writeRuntimeLog(message: string): void {
  mkdirSync(logDir, { recursive: true })
  appendFileSync(runtimeLogPath, `${new Date().toISOString()} ${message}\n`, 'utf8')
}

function createMainWindow(): BrowserWindow {
  const primaryDisplay = screen.getPrimaryDisplay()
  const { width, height } = primaryDisplay.workAreaSize

  const mainWindow = new BrowserWindow({
    width: 420,
    height: 620,
    x: Math.max(0, width - 460),
    y: Math.max(0, height - 680),
    frame: false,
    transparent: true,
    resizable: true,
    minimizable: true,
    hasShadow: false,
    alwaysOnTop: true,
    skipTaskbar: false,
    backgroundColor: '#00000000',
    title: 'Amadeus Agent',
    webPreferences: {
      preload: join(__dirname, '../preload/index.mjs'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  mainWindow.setAlwaysOnTop(true, 'floating')

  mainWindow.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    const location = sourceId ? `${sourceId}:${line}` : 'renderer'
    const text = `[renderer:${level}] ${message} (${location})`
    writeRuntimeLog(text)
    if (level >= 2) {
      console.error(text)
    }
    else {
      console.log(text)
    }
  })

  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    writeRuntimeLog(`Renderer process gone: ${details.reason}`)
    console.error(`Renderer process gone: ${details.reason}`)
  })

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedUrl) => {
    writeRuntimeLog(`Renderer failed to load ${validatedUrl}: ${errorCode} ${errorDescription}`)
    console.error(`Renderer failed to load ${validatedUrl}: ${errorCode} ${errorDescription}`)
    if (isE2eSmoke) {
      app.exit(1)
    }
  })

  mainWindow.webContents.on('did-finish-load', () => {
    writeRuntimeLog('Renderer finished loading')
    if (isE2eSmoke) {
      console.log('AMADEUS_E2E_SMOKE renderer-ready')
      setTimeout(() => app.quit(), 100)
      return
    }

    let checks = 0
    const timer = setInterval(() => {
      checks += 1
      void mainWindow.webContents.executeJavaScript('document.querySelector("#stage-status")?.textContent ?? ""')
        .then((status) => {
          writeRuntimeLog(`Stage status: ${status || '(hidden)'}`)
        })
        .catch((error: unknown) => {
          writeRuntimeLog(`Stage status read failed: ${error instanceof Error ? error.message : String(error)}`)
        })

      if (checks >= 8) {
        clearInterval(timer)
      }
    }, 3000)
  })

  if (is.dev && process.env.ELECTRON_RENDERER_URL) {
    void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  }
  else {
    void mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }

  return mainWindow
}

app.whenReady().then(() => {
  electronApp.setAppUserModelId('local.amadeus.agent')

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  ipcMain.handle('window:set-always-on-top', (event, value: boolean) => {
    const window = BrowserWindow.fromWebContents(event.sender)
    window?.setAlwaysOnTop(value, 'floating')
    return window?.isAlwaysOnTop() ?? false
  })

  ipcMain.handle('window:close', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender)
    window?.close()
    return true
  })

  ipcMain.handle('window:minimize', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender)
    window?.setAlwaysOnTop(false)
    window?.setSkipTaskbar(false)
    window?.minimize()
    return window?.isMinimized() ?? false
  })

  createMainWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow()
    }
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
