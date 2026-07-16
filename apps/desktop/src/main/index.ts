import { electronApp, is, optimizer } from '@electron-toolkit/utils'
import { app, BrowserWindow, ipcMain, screen } from 'electron'
import { appendFileSync, mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

const projectRoot = resolve(__dirname, '../../../..')
const logDir = join(projectRoot, 'logs')
const runtimeLogPath = join(logDir, 'desktop-runtime.log')
const isE2eSmoke = process.env.AMADEUS_E2E_SMOKE === '1'
const isE2eRuntimeUi = process.env.AMADEUS_E2E_RUNTIME_UI === '1'
const isE2eLive2D = process.env.AMADEUS_E2E_LIVE2D_SWITCH === '1'
const isE2eAudioFeedback = process.env.AMADEUS_E2E_AUDIO_FEEDBACK === '1'
const isE2eAudioError = process.env.AMADEUS_E2E_EXPECT_AUDIO_ERROR === '1'
const isE2ePermissionPrompt = process.env.AMADEUS_E2E_PERMISSION_PROMPT === '1'
const isE2ePermissionAllow = process.env.AMADEUS_E2E_EXPECT_PERMISSION_ALLOW === '1'
const isE2eMultiSkillSelect = process.env.AMADEUS_E2E_MULTI_SKILL_SELECT === '1'
const isE2eOpenMainUi = process.env.AMADEUS_E2E_OPEN_MAIN_UI === '1'
const isE2eCompanionHover = process.env.AMADEUS_E2E_COMPANION_HOVER === '1'
const isE2eRealRuntime = process.env.AMADEUS_E2E_REAL_RUNTIME === '1'
const defaultCompanionSessionId = process.env.AMADEUS_SESSION_ID || 'companion:default'
const e2eSecondarySessionId = process.env.AMADEUS_E2E_SECONDARY_SESSION_ID || 'e2e-secondary'
const e2eReviewTaskTitle = process.env.AMADEUS_E2E_REVIEW_TASK_TITLE || 'Real runtime review task'

const mainUiDevServerUrl = process.env.AMADEUS_MAIN_UI_DEV_URL || 'http://127.0.0.1:5178/'

let mainUiWindow: BrowserWindow | undefined
let companionWindow: BrowserWindow | undefined
let companionCursorTimer: NodeJS.Timeout | undefined
let companionLastCursor: { x: number, y: number } | undefined
let realRuntimeE2eStarted = false

if (is.dev) {
  process.env.ELECTRON_DISABLE_SECURITY_WARNINGS = 'true'
}

if (process.env.AMADEUS_E2E_USER_DATA_DIR) {
  app.setPath('userData', resolve(process.env.AMADEUS_E2E_USER_DATA_DIR))
}

const gotSingleInstanceLock = app.requestSingleInstanceLock()

function writeRuntimeLog(message: string): void {
  mkdirSync(logDir, { recursive: true })
  appendFileSync(runtimeLogPath, `${new Date().toISOString()} ${message}\n`, 'utf8')
}

function bringWindowToFront(window: BrowserWindow | undefined): void {
  if (!window || window.isDestroyed()) {
    return
  }
  if (window.isMinimized()) {
    window.restore()
  }
  window.show()
  window.focus()
}

function isMainUiBlockingCompanion(): boolean {
  return Boolean(
    mainUiWindow
    && !mainUiWindow.isDestroyed()
    && mainUiWindow.isVisible()
    && !mainUiWindow.isMinimized(),
  )
}

function mainUiInteractionStatus(): Record<string, boolean> {
  if (!mainUiWindow) {
    return {
      exists: false,
      destroyed: false,
      visible: false,
      minimized: false,
      focused: false,
      fullscreen: false,
    }
  }
  if (mainUiWindow.isDestroyed()) {
    return {
      exists: true,
      destroyed: true,
      visible: false,
      minimized: false,
      focused: false,
      fullscreen: false,
    }
  }
  return {
    exists: true,
    destroyed: false,
    visible: mainUiWindow.isVisible(),
    minimized: mainUiWindow.isMinimized(),
    focused: mainUiWindow.isFocused(),
    fullscreen: mainUiWindow.isFullScreen(),
  }
}

function publishCompanionInteractionMode(): void {
  if (!companionWindow || companionWindow.isDestroyed()) {
    return
  }
  const mainUiBlocking = isMainUiBlockingCompanion()
  companionWindow.webContents.send('companion:interaction-mode', {
    interactive: !mainUiBlocking,
    reason: mainUiBlocking ? 'main-ui-visible' : 'main-ui-unavailable',
    mainUi: mainUiInteractionStatus(),
  })
}

function companionRendererDevUrl(): string {
  const baseUrl = process.env.ELECTRON_RENDERER_URL || 'http://localhost:5173/'
  return new URL('companion/index.html', baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`).toString()
}

function rendererQuery(sessionId = defaultCompanionSessionId): Record<string, string> {
  const query: Record<string, string> = {
    sessionId,
  }
  if (process.env.AMADEUS_E2E_AGENT_HTTP_URL) {
    query.agentHttpUrl = process.env.AMADEUS_E2E_AGENT_HTTP_URL
  }
  if (process.env.AMADEUS_E2E_AGENT_WS_URL) {
    query.agentWsUrl = process.env.AMADEUS_E2E_AGENT_WS_URL
  }
  if (process.env.AMADEUS_E2E_SKIP_LIVE2D === '1') {
    query.skipLive2d = '1'
  }
  if (process.env.AMADEUS_E2E_MOCK_LIVE2D === '1') {
    query.mockLive2d = '1'
  }
  if (process.env.AMADEUS_E2E_MOCK_AUDIO) {
    query.mockAudio = process.env.AMADEUS_E2E_MOCK_AUDIO
  }
  query.disableSkillPersistence = '1'
  return query
}

function mainUiQueryString(sessionId: string): string {
  const query = new URLSearchParams()
  query.set('sessionId', sessionId)
  if (process.env.AMADEUS_E2E_AGENT_HTTP_URL) {
    query.set('agentHttpUrl', process.env.AMADEUS_E2E_AGENT_HTTP_URL)
  }
  if (process.env.AMADEUS_E2E_AGENT_WS_URL) {
    query.set('agentWsUrl', process.env.AMADEUS_E2E_AGENT_WS_URL)
  }
  return query.toString()
}

function loadMainUi(window: BrowserWindow, sessionId: string): void {
  const search = mainUiQueryString(sessionId)
  if (process.env.AMADEUS_DESKTOP_DEV === '1') {
    const url = new URL(mainUiDevServerUrl)
    url.search = search
    void window.loadURL(url.toString())
  }
  else {
    void window.loadFile(join(projectRoot, 'apps/desktop-ui-next/dist/index.html'), { search })
  }
}

function startCompanionCursorTracking(window: BrowserWindow): void {
  if (companionCursorTimer) {
    clearInterval(companionCursorTimer)
  }
  companionLastCursor = undefined

  companionCursorTimer = setInterval(() => {
    if (window.isDestroyed()) {
      clearInterval(companionCursorTimer)
      companionCursorTimer = undefined
      companionLastCursor = undefined
      return
    }

    const cursor = screen.getCursorScreenPoint()
    if (
      companionLastCursor
      && Math.abs(cursor.x - companionLastCursor.x) < 2
      && Math.abs(cursor.y - companionLastCursor.y) < 2
    ) {
      return
    }
    companionLastCursor = cursor

    window.webContents.send('desktop:global-cursor', {
      cursor,
      window: window.getBounds(),
    })
  }, 80)
}

function createCompanionWindow(): BrowserWindow {
  const primaryDisplay = screen.getPrimaryDisplay()
  const { width, height } = primaryDisplay.workAreaSize

  const window = new BrowserWindow({
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
    title: 'Amadeus Companion',
    webPreferences: {
      preload: join(__dirname, '../preload/index.mjs'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  companionWindow = window
  window.setAlwaysOnTop(true, 'floating')
  startCompanionCursorTracking(window)

  window.webContents.on('console-message', (_event, level, message, line, sourceId) => {
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

  window.webContents.on('render-process-gone', (_event, details) => {
    writeRuntimeLog(`Renderer process gone: ${details.reason}`)
    console.error(`Renderer process gone: ${details.reason}`)
  })

  window.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedUrl) => {
    writeRuntimeLog(`Renderer failed to load ${validatedUrl}: ${errorCode} ${errorDescription}`)
    console.error(`Renderer failed to load ${validatedUrl}: ${errorCode} ${errorDescription}`)
    if (isE2eSmoke) {
      app.exit(1)
    }
  })

  window.on('closed', () => {
    if (companionWindow === window) {
      companionWindow = undefined
    }
    if (companionCursorTimer) {
      clearInterval(companionCursorTimer)
      companionCursorTimer = undefined
      companionLastCursor = undefined
    }
  })

  window.webContents.on('did-finish-load', () => {
    writeRuntimeLog('Renderer finished loading')
    publishCompanionInteractionMode()
    if (isE2eSmoke) {
      console.log('AMADEUS_E2E_SMOKE renderer-ready')
      setTimeout(() => app.quit(), 100)
      return
    }

    if (isE2eLive2D) {
      void runLive2DSwitchE2E(window)
      return
    }

    if (isE2eAudioFeedback) {
      void runAudioFeedbackE2E(window)
      return
    }

    if (isE2eOpenMainUi) {
      void runOpenMainUiE2E(window)
      return
    }

    if (isE2eCompanionHover) {
      void runCompanionHoverE2E(window)
      return
    }

    let checks = 0
    const timer = setInterval(() => {
      checks += 1
      void window.webContents.executeJavaScript('document.querySelector("#stage-status")?.textContent ?? ""')
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

  if (!isE2eSmoke && !isE2eRuntimeUi && !isE2eLive2D && !isE2eAudioFeedback && !isE2ePermissionPrompt && !isE2eMultiSkillSelect && process.env.AMADEUS_DESKTOP_DEV === '1') {
    const url = new URL(companionRendererDevUrl())
    url.searchParams.set('sessionId', defaultCompanionSessionId)
    void window.loadURL(url.toString())
  }
  else {
    void window.loadFile(join(__dirname, '../renderer/companion/index.html'), { query: rendererQuery() })
  }

  return window
}

function createMainUiWindow(sessionId = defaultCompanionSessionId): BrowserWindow {
  if (mainUiWindow && !mainUiWindow.isDestroyed()) {
    mainUiWindow.show()
    mainUiWindow.focus()
    publishCompanionInteractionMode()
    return mainUiWindow
  }

  const window = new BrowserWindow({
    width: 1040,
    height: 760,
    minWidth: 760,
    minHeight: 560,
    frame: true,
    transparent: false,
    resizable: true,
    minimizable: true,
    hasShadow: true,
    alwaysOnTop: false,
    skipTaskbar: false,
    backgroundColor: '#10121a',
    title: 'Amadeus Main UI',
    webPreferences: {
      preload: join(__dirname, '../preload/index.mjs'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  mainUiWindow = window
  window.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    const location = sourceId ? `${sourceId}:${line}` : 'main-ui-renderer'
    const text = `[main-ui:${level}] ${message} (${location})`
    writeRuntimeLog(text)
    if (level >= 2) {
      console.error(text)
    }
    else {
      console.log(text)
    }
  })
  window.on('closed', () => {
    if (mainUiWindow === window) {
      mainUiWindow = undefined
    }
    publishCompanionInteractionMode()
  })
  window.on('show', publishCompanionInteractionMode)
  window.on('hide', publishCompanionInteractionMode)
  window.on('minimize', publishCompanionInteractionMode)
  window.on('restore', publishCompanionInteractionMode)
  window.webContents.on('did-finish-load', () => {
    if (isE2eRealRuntime && !realRuntimeE2eStarted) {
      realRuntimeE2eStarted = true
      void runRealRuntimeE2E(window)
      return
    }
    if (isE2eRuntimeUi) {
      void runRuntimeUiE2E(window)
      return
    }
    if (isE2ePermissionPrompt) {
      void runPermissionPromptE2E(window)
    }
  })

  loadMainUi(window, sessionId)

  return window
}

async function runPermissionPromptE2E(mainWindow: BrowserWindow): Promise<void> {
  try {
    const expectAllow = JSON.stringify(isE2ePermissionAllow)
    const result = await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const expectAllow = ${expectAllow};
        const deadline = Date.now() + 10000;
        const byTestId = (id) => document.querySelector('[data-testid="' + id + '"]');
        const waitFor = (predicate, label) => {
          return new Promise((waitResolve, waitReject) => {
            const tick = () => {
              try {
                if (predicate()) {
                  waitResolve(undefined);
                  return;
                }
              }
              catch (error) {
                waitReject(error);
                return;
              }

              if (Date.now() > deadline) {
                waitReject(new Error('Timed out waiting for ' + label));
                return;
              }
              setTimeout(tick, 50);
            };
            tick();
          });
        };

        (async () => {
          await waitFor(() => byTestId('runtime-connection')?.dataset?.state === 'online', 'Vue runtime connection');
          const input = byTestId('chat-input');
          const sendButton = byTestId('chat-send');
          if (!(input instanceof HTMLTextAreaElement) || !(sendButton instanceof HTMLButtonElement)) {
            throw new Error('Vue chat controls are missing');
          }

          const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
          valueSetter?.call(input, 'e2e permission ping');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          await waitFor(() => !sendButton.disabled, 'Vue chat send enabled');
          sendButton.click();

          await waitFor(() => Boolean(byTestId('tool-permission')), 'Vue permission prompt visible');
          const promptText = byTestId('tool-permission')?.textContent ?? '';
          const actionButton = byTestId(expectAllow ? 'tool-permission-allow' : 'tool-permission-deny');
          if (!(actionButton instanceof HTMLButtonElement)) {
            throw new Error('Vue permission action is missing');
          }
          actionButton.click();
          await waitFor(() => !byTestId('tool-permission'), 'Vue permission prompt cleared');

          if (expectAllow) {
            if (!promptText.includes('Editing local file')) {
              throw new Error('Vue permission prompt did not render the tool name');
            }
          }

          resolve({
            promptText,
            approved: expectAllow,
            renderer: 'vue-main-ui',
          });
        })().catch(reject);
      }))()
    `)
    console.log(`AMADEUS_E2E_PERMISSION_PROMPT ${JSON.stringify(result)}`)
    setTimeout(() => app.quit(), 100)
  }
  catch (error) {
    console.error(`AMADEUS_E2E_PERMISSION_PROMPT failed: ${error instanceof Error ? error.message : String(error)}`)
    app.exit(1)
  }
}

function waitForMainUiLoad(mainWindow: BrowserWindow, timeoutMs = 10000): Promise<void> {
  return new Promise((resolveWait, rejectWait) => {
    const timeout = setTimeout(() => {
      cleanup()
      rejectWait(new Error('Timed out waiting for Main UI navigation'))
    }, timeoutMs)
    const onFinished = () => {
      cleanup()
      resolveWait()
    }
    const onFailed = (_event: Electron.Event, code: number, description: string, url: string) => {
      cleanup()
      rejectWait(new Error(`Main UI navigation failed ${code} ${description}: ${url}`))
    }
    const cleanup = () => {
      clearTimeout(timeout)
      mainWindow.webContents.removeListener('did-finish-load', onFinished)
      mainWindow.webContents.removeListener('did-fail-load', onFailed)
    }
    mainWindow.webContents.once('did-finish-load', onFinished)
    mainWindow.webContents.once('did-fail-load', onFailed)
  })
}

async function runRealRuntimeE2E(mainWindow: BrowserWindow): Promise<void> {
  try {
    const initial = await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const deadline = Date.now() + 15000;
        const byTestId = (id) => document.querySelector('[data-testid="' + id + '"]');
        const waitFor = (predicate, label) => new Promise((waitResolve, waitReject) => {
          const tick = () => {
            try {
              if (predicate()) {
                waitResolve(undefined);
                return;
              }
            }
            catch (error) {
              waitReject(error);
              return;
            }
            if (Date.now() > deadline) {
              waitReject(new Error('Timed out waiting for ' + label));
              return;
            }
            setTimeout(tick, 50);
          };
          tick();
        });

        (async () => {
          await waitFor(() => byTestId('runtime-connection')?.dataset?.state === 'online', 'real runtime connection');
          const input = byTestId('chat-input');
          const sendButton = byTestId('chat-send');
          if (!(input instanceof HTMLTextAreaElement) || !(sendButton instanceof HTMLButtonElement)) {
            throw new Error('Vue chat controls are missing');
          }
          const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
          setter?.call(input, 'real runtime e2e ping');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          await waitFor(() => !sendButton.disabled, 'real runtime send enabled');
          sendButton.click();
          await waitFor(() => byTestId('chat-log')?.textContent?.includes('Real runtime E2E reply'), 'real runtime reply');
          resolve({
            sessionId: byTestId('session-switcher-trigger')?.dataset?.sessionId ?? '',
            chat: byTestId('chat-log')?.textContent ?? '',
          });
        })().catch(reject);
      }))()
    `)

    const secondaryLoad = waitForMainUiLoad(mainWindow)
    await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const trigger = document.querySelector('[data-testid="session-switcher-trigger"]');
        if (!(trigger instanceof HTMLButtonElement)) throw new Error('Session switcher trigger is missing');
        trigger.click();
        const deadline = Date.now() + 5000;
        const tick = () => {
          const target = document.querySelector('[data-testid="session-select"][data-session-id="${e2eSecondarySessionId}"]');
          if (target instanceof HTMLButtonElement) {
            target.click();
            resolve(undefined);
            return;
          }
          if (Date.now() > deadline) {
            reject(new Error('Secondary session is missing'));
            return;
          }
          setTimeout(tick, 50);
        };
        tick();
      }))()
    `)
    await secondaryLoad
    const secondary = await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const deadline = Date.now() + 10000;
        const tick = () => {
          const connection = document.querySelector('[data-testid="runtime-connection"]');
          const trigger = document.querySelector('[data-testid="session-switcher-trigger"]');
          if (connection?.dataset?.state === 'online' && trigger?.dataset?.sessionId === '${e2eSecondarySessionId}') {
            resolve({
              sessionId: trigger.dataset.sessionId,
              chat: document.querySelector('[data-testid="chat-log"]')?.textContent ?? '',
            });
            return;
          }
          if (Date.now() > deadline) {
            reject(new Error('Timed out waiting for secondary session'));
            return;
          }
          setTimeout(tick, 50);
        };
        tick();
      }))()
    `)

    const companionLoad = waitForMainUiLoad(mainWindow)
    await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const trigger = document.querySelector('[data-testid="session-switcher-trigger"]');
        if (!(trigger instanceof HTMLButtonElement)) throw new Error('Session switcher trigger is missing');
        trigger.click();
        const deadline = Date.now() + 5000;
        const tick = () => {
          const target = document.querySelector('[data-testid="session-companion"]');
          if (target instanceof HTMLButtonElement) {
            target.click();
            resolve(undefined);
            return;
          }
          if (Date.now() > deadline) {
            reject(new Error('Companion session entry is missing'));
            return;
          }
          setTimeout(tick, 50);
        };
        tick();
      }))()
    `)
    await companionLoad
    const approval = await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const deadline = Date.now() + 15000;
        const byTestId = (id) => document.querySelector('[data-testid="' + id + '"]');
        const waitFor = (predicate, label) => new Promise((waitResolve, waitReject) => {
          const tick = () => {
            try {
              if (predicate()) {
                waitResolve(undefined);
                return;
              }
            }
            catch (error) {
              waitReject(error);
              return;
            }
            if (Date.now() > deadline) {
              waitReject(new Error('Timed out waiting for ' + label));
              return;
            }
            setTimeout(tick, 50);
          };
          tick();
        });

        (async () => {
          await waitFor(() => {
            return byTestId('runtime-connection')?.dataset?.state === 'online'
              && byTestId('session-switcher-trigger')?.dataset?.sessionId === 'companion:default';
          }, 'companion session restore');
          await waitFor(() => byTestId('chat-log')?.textContent?.includes('Real runtime E2E reply'), 'persisted chat restore');
          const tasksNav = document.querySelector('[data-testid="main-nav-item"][data-nav-key="tasks"]');
          if (!(tasksNav instanceof HTMLButtonElement)) throw new Error('Tasks navigation is missing');
          tasksNav.click();
          await waitFor(() => Boolean(byTestId('tasks-view')), 'tasks view');
          await waitFor(() => {
            return Array.from(document.querySelectorAll('[data-testid="task-detail"]'))
              .some((element) => element.getAttribute('data-task-title') === '${e2eReviewTaskTitle}');
          }, 'review task');
          const detail = Array.from(document.querySelectorAll('[data-testid="task-detail"]'))
            .find((element) => element.getAttribute('data-task-title') === '${e2eReviewTaskTitle}');
          if (!(detail instanceof HTMLButtonElement)) throw new Error('Review task detail button is missing');
          detail.click();
          await waitFor(() => Boolean(byTestId('task-approve')), 'review approval button');
          const approve = byTestId('task-approve');
          if (!(approve instanceof HTMLButtonElement)) throw new Error('Review approval button is missing');
          approve.click();
          await waitFor(() => byTestId('task-detail-status')?.textContent?.includes('已完成'), 'review approval completion');
          resolve({
            sessionId: byTestId('session-switcher-trigger')?.dataset?.sessionId ?? '',
            persistedChat: true,
            taskStatus: byTestId('task-detail-status')?.textContent?.trim() ?? '',
          });
        })().catch(reject);
      }))()
    `)

    console.log(`AMADEUS_E2E_REAL_RUNTIME ${JSON.stringify({ initial, secondary, approval, renderer: 'vue-main-ui' })}`)
    setTimeout(() => app.quit(), 100)
  }
  catch (error) {
    console.error(`AMADEUS_E2E_REAL_RUNTIME failed: ${error instanceof Error ? error.message : String(error)}`)
    app.exit(1)
  }
}

async function runAudioFeedbackE2E(mainWindow: BrowserWindow): Promise<void> {
  try {
    const expectAudioError = JSON.stringify(isE2eAudioError)
    const result = await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const expectAudioError = ${expectAudioError};
        const deadline = Date.now() + 8000;
        const text = (selector) => document.querySelector(selector)?.textContent ?? '';
        const connectionState = () => {
          const label = document.querySelector('#connection-label');
          return label?.getAttribute('aria-label') || label?.getAttribute('title') || label?.dataset?.state || label?.textContent || '';
        };
        const waitFor = (predicate, label) => {
          return new Promise((waitResolve, waitReject) => {
            const tick = () => {
              try {
                if (predicate()) {
                  waitResolve(undefined);
                  return;
                }
              }
              catch (error) {
                waitReject(error);
                return;
              }

              if (Date.now() > deadline) {
                waitReject(new Error('Timed out waiting for ' + label + '; voice=' + text('#voice-status')));
                return;
              }
              setTimeout(tick, 50);
            };
            tick();
          });
        };

        (async () => {
          await waitFor(() => connectionState() === 'Connected' || connectionState() === 'connected', 'runtime connection');
          const input = document.querySelector('#chat-input');
          const form = document.querySelector('#chat-form');
          if (!input || !form) {
            throw new Error('Chat form is missing');
          }

          input.value = 'e2e audio ping';
          input.dispatchEvent(new Event('input', { bubbles: true }));
          form.requestSubmit();

          await waitFor(() => text('#voice-status') === 'Playing runtime audio', 'runtime audio start');
          if (expectAudioError) {
            await new Promise((done) => setTimeout(done, 250));
          }
          else {
            await waitFor(() => text('#voice-status') === 'Voice idle', 'runtime audio end');
          }
          await new Promise((done) => setTimeout(done, 100));

          resolve({
            voice: text('#voice-status'),
            chat: text('#chat-log'),
          });
        })().catch(reject);
      }))()
    `)
    console.log(`AMADEUS_E2E_AUDIO_FEEDBACK ${JSON.stringify(result)}`)
    setTimeout(() => app.quit(), 100)
  }
  catch (error) {
    console.error(`AMADEUS_E2E_AUDIO_FEEDBACK failed: ${error instanceof Error ? error.message : String(error)}`)
    app.exit(1)
  }
}

async function runLive2DSwitchE2E(mainWindow: BrowserWindow): Promise<void> {
  try {
    const result = await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const deadline = Date.now() + 10000;
        const text = (selector) => document.querySelector(selector)?.textContent ?? '';
        const waitFor = (predicate, label) => {
          return new Promise((waitResolve, waitReject) => {
            const tick = () => {
              try {
                if (predicate()) {
                  waitResolve(undefined);
                  return;
                }
              }
              catch (error) {
                waitReject(error);
                return;
              }

              if (Date.now() > deadline) {
                waitReject(new Error('Timed out waiting for ' + label + '; model status=' + text('#live2d-model-status')));
                return;
              }
              setTimeout(tick, 50);
            };
            tick();
          });
        };

        (async () => {
          await waitFor(() => {
            return text('#debug-capabilities').includes('hiyori-free')
              && document.querySelector('#live2d-model-select')?.value === 'hiyori-free';
          }, 'initial Live2D model load');
          const select = document.querySelector('#live2d-model-select');
          if (!select) {
            throw new Error('Live2D model select is missing');
          }
          const options = Array.from(select.options).map((option) => option.value);
          if (!options.includes('hiyori-free') || !options.includes('hiyori-pro')) {
            throw new Error('Live2D model options missing: ' + options.join(','));
          }

          select.value = 'hiyori-pro';
          select.dispatchEvent(new Event('change', { bubbles: true }));
          await waitFor(() => {
            return text('#debug-capabilities').includes('hiyori-pro') && select.value === 'hiyori-pro';
          }, 'switched Live2D model load');

          resolve({
            modelStatus: text('#live2d-model-status'),
            selectValue: select.value,
            capabilities: text('#debug-capabilities'),
          });
        })().catch(reject);
      }))()
    `)
    console.log(`AMADEUS_E2E_LIVE2D_SWITCH ${JSON.stringify(result)}`)
    setTimeout(() => app.quit(), 100)
  }
  catch (error) {
    console.error(`AMADEUS_E2E_LIVE2D_SWITCH failed: ${error instanceof Error ? error.message : String(error)}`)
    app.exit(1)
  }
}

async function runRuntimeUiE2E(mainWindow: BrowserWindow): Promise<void> {
  try {
    const enableMultiSkillSelect = JSON.stringify(isE2eMultiSkillSelect)
    const result = await mainWindow.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const enableMultiSkillSelect = ${enableMultiSkillSelect};
        const deadline = Date.now() + 10000;
        const byTestId = (id) => document.querySelector('[data-testid="' + id + '"]');
        const nav = (key) => document.querySelector('[data-testid="main-nav-item"][data-nav-key="' + key + '"]');
        const waitFor = (predicate, label) => {
          return new Promise((waitResolve, waitReject) => {
            const tick = () => {
              try {
                if (predicate()) {
                  waitResolve(undefined);
                  return;
                }
              }
              catch (error) {
                waitReject(error);
                return;
              }

              if (Date.now() > deadline) {
                waitReject(new Error('Timed out waiting for ' + label));
                return;
              }
              setTimeout(tick, 50);
            };
            tick();
          });
        };

        (async () => {
          await waitFor(() => byTestId('runtime-connection')?.dataset?.state === 'online', 'Vue runtime connection');
          let selectedSkillIds = [];
          let skillsPage = '';

          if (enableMultiSkillSelect) {
            const skillsNav = nav('skills');
            if (!(skillsNav instanceof HTMLButtonElement)) {
              throw new Error('Vue skills navigation is missing');
            }
            skillsNav.click();
            await waitFor(() => document.querySelectorAll('[data-testid="skill-toggle"]').length >= 2, 'Vue skills loaded');
            const toggles = Array.from(document.querySelectorAll('[data-testid="skill-toggle"]'));
            toggles.forEach((toggle) => {
              if (toggle instanceof HTMLButtonElement) toggle.click();
            });
            selectedSkillIds = toggles.map((toggle) => toggle.getAttribute('data-skill-id')).filter(Boolean);
            skillsPage = document.body.textContent ?? '';

            const chatNav = nav('chat');
            if (!(chatNav instanceof HTMLButtonElement)) {
              throw new Error('Vue chat navigation is missing');
            }
            chatNav.click();
            await waitFor(() => Boolean(byTestId('chat-input')), 'Vue chat restored');
          }

          const input = byTestId('chat-input');
          const sendButton = byTestId('chat-send');
          if (!(input instanceof HTMLTextAreaElement) || !(sendButton instanceof HTMLButtonElement)) {
            throw new Error('Vue chat controls are missing');
          }

          const valueSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
          valueSetter?.call(input, 'e2e runtime ping');
          input.dispatchEvent(new Event('input', { bubbles: true }));
          await waitFor(() => !sendButton.disabled, 'Vue chat send enabled');
          sendButton.click();

          await waitFor(() => {
            return Array.from(document.querySelectorAll('[data-testid="chat-message"][data-role="assistant"]'))
              .some((element) => element.textContent?.includes('E2E runtime reply'));
          }, 'Vue assistant reply');

          resolve({
            connection: byTestId('runtime-connection')?.dataset?.state ?? '',
            selectedSkillIds,
            skillsPage,
            chat: byTestId('chat-log')?.textContent ?? '',
            renderer: 'vue-main-ui',
          });
        })().catch(reject);
      }))()
    `)
    console.log(`AMADEUS_E2E_RUNTIME_UI ${JSON.stringify(result)}`)
    setTimeout(() => app.quit(), 100)
  }
  catch (error) {
    console.error(`AMADEUS_E2E_RUNTIME_UI failed: ${error instanceof Error ? error.message : String(error)}`)
    app.exit(1)
  }
}

async function runOpenMainUiE2E(window: BrowserWindow): Promise<void> {
  try {
    const result = await window.webContents.executeJavaScript(`
      (() => new Promise((resolve, reject) => {
        const button = document.querySelector('#open-main-ui-button');
        if (!button) {
          reject(new Error('Open Main UI button is missing'));
          return;
        }
        button.click();
        setTimeout(() => resolve({ clicked: true }), 100);
      }))()
    `)
    const deadline = Date.now() + 5000
    while ((!mainUiWindow || mainUiWindow.isDestroyed() || !mainUiWindow.webContents.getURL().includes('sessionId=companion%3Adefault')) && Date.now() < deadline) {
      await new Promise((resolveWait) => setTimeout(resolveWait, 100))
    }

    if (!mainUiWindow || mainUiWindow.isDestroyed()) {
      throw new Error('Main UI window did not open')
    }

    const url = mainUiWindow.webContents.getURL()
    if (!url.includes('sessionId=companion%3Adefault')) {
      throw new Error(`Main UI did not inherit companion session: ${url}`)
    }

    const loadDeadline = Date.now() + 5000
    while (mainUiWindow.webContents.isLoading() && Date.now() < loadDeadline) {
      await new Promise((resolveWait) => setTimeout(resolveWait, 100))
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 1500))
    const connection = await mainUiWindow.webContents.executeJavaScript(`
      (() => {
        const label = document.querySelector('[data-testid="runtime-connection"]');
        return label?.dataset?.state || label?.textContent || '';
      })()
    `)

    console.log(`AMADEUS_E2E_OPEN_MAIN_UI ${JSON.stringify({ ...result, windowCount: BrowserWindow.getAllWindows().length, url, connection, renderer: 'vue-main-ui' })}`)
    setTimeout(() => app.quit(), 100)
  }
  catch (error) {
    console.error(`AMADEUS_E2E_OPEN_MAIN_UI failed: ${error instanceof Error ? error.message : String(error)}`)
    app.exit(1)
  }
}

async function runCompanionHoverE2E(window: BrowserWindow): Promise<void> {
  try {
    const bounds = window.getBounds()
    window.webContents.send('desktop:global-cursor', {
      cursor: { x: bounds.x - 20, y: bounds.y - 20 },
      window: bounds,
    })
    await new Promise((resolveWait) => setTimeout(resolveWait, 650))

    const before = await window.webContents.executeJavaScript(`
      (() => {
        const panel = document.querySelector('.companion-hover-panel');
        const stage = document.querySelector('#live2d-stage');
        if (!panel || !stage) {
          throw new Error('Companion hover panel or Live2D stage is missing');
        }
        const panelStyle = getComputedStyle(panel);
        const stageRect = stage.getBoundingClientRect();
        return {
          panelOpacity: Number(panelStyle.opacity),
          pointerEvents: panelStyle.pointerEvents,
          stageWidth: Math.round(stageRect.width),
          stageHeight: Math.round(stageRect.height),
        };
      })()
    `) as { panelOpacity: number, pointerEvents: string, stageWidth: number, stageHeight: number }

    if (before.stageWidth <= 0 || before.stageHeight <= 0) {
      throw new Error(`Live2D stage is not visible: ${JSON.stringify(before)}`)
    }
      if (before.panelOpacity > 0.05) {
      throw new Error(`Hover panel should be hidden by default: ${JSON.stringify(before)}`)
    }

    window.webContents.send('desktop:global-cursor', {
      cursor: { x: bounds.x + 210, y: bounds.y + 520 },
      window: bounds,
    })
    await new Promise((resolveWait) => setTimeout(resolveWait, 260))

    const after = await window.webContents.executeJavaScript(`
      (() => {
        const panel = document.querySelector('.companion-hover-panel');
        const input = document.querySelector('#chat-input');
        const button = document.querySelector('#open-main-ui-button');
        if (!panel || !input || !button) {
          throw new Error('Companion hover controls are missing');
        }
        const panelStyle = getComputedStyle(panel);
        return {
          panelOpacity: Number(panelStyle.opacity),
          pointerEvents: panelStyle.pointerEvents,
          inputVisible: input.getBoundingClientRect().width > 0 && input.getBoundingClientRect().height > 0,
          mainUiButtonVisible: button.getBoundingClientRect().width > 0 && button.getBoundingClientRect().height > 0,
        };
      })()
    `) as { panelOpacity: number, pointerEvents: string, inputVisible: boolean, mainUiButtonVisible: boolean }

      if (after.panelOpacity < 0.95 || !after.inputVisible || !after.mainUiButtonVisible) {
      throw new Error(`Hover panel did not become interactive: ${JSON.stringify(after)}`)
    }

    console.log(`AMADEUS_E2E_COMPANION_HOVER ${JSON.stringify({ before, after })}`)
    setTimeout(() => app.quit(), 100)
  }
  catch (error) {
    console.error(`AMADEUS_E2E_COMPANION_HOVER failed: ${error instanceof Error ? error.message : String(error)}`)
    app.exit(1)
  }
}

if (!gotSingleInstanceLock) {
  app.quit()
}
else {
  app.on('second-instance', () => {
    bringWindowToFront(mainUiWindow)
    bringWindowToFront(companionWindow)
    publishCompanionInteractionMode()
  })

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
      publishCompanionInteractionMode()
      return window?.isMinimized() ?? false
    })

    ipcMain.handle('window:toggle-fullscreen', (event) => {
      const window = BrowserWindow.fromWebContents(event.sender)
      if (!window) {
        return false
      }
      window.setFullScreen(!window.isFullScreen())
      return window.isFullScreen()
    })

    ipcMain.handle('window:is-fullscreen', (event) => {
      const window = BrowserWindow.fromWebContents(event.sender)
      return window?.isFullScreen() ?? false
    })

    ipcMain.handle('window:open-main-ui', (_event, sessionId?: string) => {
      createMainUiWindow(typeof sessionId === 'string' && sessionId.trim() ? sessionId : defaultCompanionSessionId)
      publishCompanionInteractionMode()
      return true
    })

    createCompanionWindow()
    if (isE2eRuntimeUi || isE2ePermissionPrompt || isE2eRealRuntime) {
      createMainUiWindow(defaultCompanionSessionId)
    }

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createCompanionWindow()
      }
    })
  })
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})
