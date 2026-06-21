import { Application } from '@pixi/app'
import type {
  AssistantState,
  CharacterBehaviorPayload,
} from '@amadeus-agent/amadeus/events'
import type { Live2DModel as Live2DModelClass } from 'pixi-live2d-display/cubism4'
import * as PIXI from 'pixi.js'

import { RuntimeUiController } from './runtime-ui'
import './styles.css'

window.PIXI = PIXI

const DEFAULT_MODEL_URL = 'https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display/test/assets/haru/haru_greeter_t03.model3.json'
const CUBISM_CORE_URL = 'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js'
const CUBISM_CORE_TIMEOUT_MS = 8000
const MOTION_PRIORITY_FORCE = 3
const LIVE2D_LOAD_TIMEOUT_MS = 15000

const stageElement = document.querySelector<HTMLDivElement>('#live2d-stage')
const statusElement = document.querySelector<HTMLDivElement>('#stage-status')
const chatForm = document.querySelector<HTMLFormElement>('#chat-form')
const chatInput = document.querySelector<HTMLInputElement>('#chat-input')
const chatLog = document.querySelector<HTMLDivElement>('#chat-log')
const pinButton = document.querySelector<HTMLButtonElement>('#pin-button')
const minimizeButton = document.querySelector<HTMLButtonElement>('#minimize-button')
const voiceButton = document.querySelector<HTMLButtonElement>('#voice-button')
const closeButton = document.querySelector<HTMLButtonElement>('#close-button')
const providerLabel = document.querySelector<HTMLElement>('#provider-label')
const connectionLabel = document.querySelector<HTMLElement>('#connection-label')
const memoryStatus = document.querySelector<HTMLSpanElement>('#memory-status')
const toolStatus = document.querySelector<HTMLDivElement>('#tool-status')
const toolConfigStatus = document.querySelector<HTMLDivElement>('#tool-config-status')
const toolPermission = document.querySelector<HTMLDivElement>('#tool-permission')
const toolPermissionText = document.querySelector<HTMLSpanElement>('#tool-permission-text')
const toolAllowButton = document.querySelector<HTMLButtonElement>('#tool-allow-button')
const toolDenyButton = document.querySelector<HTMLButtonElement>('#tool-deny-button')
const memoryReviewStatus = document.querySelector<HTMLSpanElement>('#memory-review-status')
const memoryReviewRunButton = document.querySelector<HTMLButtonElement>('#memory-review-run-button')
const memoryReviewList = document.querySelector<HTMLDivElement>('#memory-review-list')
const voiceStatus = document.querySelector<HTMLDivElement>('#voice-status')
const resetSessionButton = document.querySelector<HTMLButtonElement>('#reset-session-button')
const debugState = document.querySelector<HTMLSelectElement>('#debug-state')
const debugExpression = document.querySelector<HTMLSelectElement>('#debug-expression')
const debugMotion = document.querySelector<HTMLSelectElement>('#debug-motion')
const debugApply = document.querySelector<HTMLButtonElement>('#debug-apply')
const debugCapabilities = document.querySelector<HTMLDivElement>('#debug-capabilities')

let pinned = true
let live2dController: Live2DController | undefined

interface Live2DCoreModel {
  setParameterValueById: (id: string, value: number) => void
}

type Live2DModelConstructor = typeof Live2DModelClass
type Live2DModelInstance = Awaited<ReturnType<Live2DModelConstructor['from']>>

interface Live2DModelCapabilities {
  expressions: string[]
  motions: string[]
}

interface Live2DSettingsLike {
  json?: {
    FileReferences?: {
      Expressions?: Array<{ Name?: string; name?: string }>
      Motions?: Record<string, unknown[]>
    }
  }
  expressions?: Array<string | { Name?: string; name?: string }>
  motions?: Record<string, unknown[]>
}

interface Live2DInternalsLike {
  settings?: Live2DSettingsLike
  internalModel?: {
    settings?: Live2DSettingsLike
    motionManager?: {
      definitions?: Record<string, unknown[]>
    }
    expressionManager?: {
      definitions?: Array<string | { Name?: string; name?: string }>
    }
  }
}

const MOTION_ALIASES: Record<string, string[]> = {
  idle: ['Idle', 'idle', 'Start', 'start'],
  think: ['TapBody', 'tap_body', 'FlickHead', 'flick_head', 'Idle', 'idle'],
  talk: ['TapBody', 'tap_body', 'Idle', 'idle'],
  nod: ['TapBody', 'tap_body', 'FlickHead', 'flick_head'],
  shake_head: ['FlickHead', 'flick_head', 'TapBody', 'tap_body'],
  tilt_head: ['FlickHead', 'flick_head', 'TapBody', 'tap_body'],
  TapBody: ['TapBody', 'tap_body'],
}

const EXPRESSION_ALIASES: Record<string, string[]> = {
  neutral: ['neutral', 'default', 'normal', ''],
  smile: ['smile', 'happy', '01'],
  serious: ['serious', 'focused', 'angry', '02'],
  confused: ['confused', 'surprised', '03'],
  curious: ['curious', 'surprised', '04'],
}

class Live2DController {
  private lastMotion = ''
  private lastExpression = ''
  private mouthTimer: number | undefined
  readonly capabilities: Live2DModelCapabilities

  constructor(private readonly model: Live2DModelInstance) {
    this.capabilities = this.readCapabilities()
  }

  focus(pointerX: number, pointerY: number, width: number, height: number): void {
    const x = (pointerX / width - 0.5) * 30
    const y = (pointerY / height - 0.5) * 30
    const coreModel = this.model.internalModel.coreModel as Live2DCoreModel
    coreModel.setParameterValueById('ParamAngleX', x)
    coreModel.setParameterValueById('ParamAngleY', -y)
  }

  setMouthOpen(value: number): void {
    const coreModel = this.model.internalModel.coreModel as Live2DCoreModel
    coreModel.setParameterValueById('ParamMouthOpenY', Math.max(0, Math.min(1, value)))
  }

  startMouthLoop(): void {
    this.stopMouthLoop()
    const startedAt = performance.now()
    this.mouthTimer = window.setInterval(() => {
      const elapsed = performance.now() - startedAt
      const fast = Math.sin(elapsed / 72)
      const slow = Math.sin(elapsed / 173)
      const value = 0.18 + Math.abs(fast * 0.55) + Math.abs(slow * 0.18)
      this.setMouthOpen(value)
    }, 50)
  }

  stopMouthLoop(): void {
    if (this.mouthTimer !== undefined) {
      window.clearInterval(this.mouthTimer)
      this.mouthTimer = undefined
    }
    this.setMouthOpen(0)
  }

  async applyState(state: AssistantState): Promise<void> {
    if (state === 'idle') {
      await this.applyBehavior({ emotion: 'neutral', expression: 'neutral', motion: 'idle', intensity: 0.35 })
      return
    }

    if (state === 'thinking' || state === 'tool-running') {
      await this.applyBehavior({ emotion: 'focused', expression: 'serious', motion: 'think', intensity: 0.65 })
      return
    }

    if (state === 'speaking') {
      await this.applyBehavior({ emotion: 'neutral', expression: 'smile', motion: 'talk', intensity: 0.55 })
      return
    }

    if (state === 'error') {
      await this.applyBehavior({ emotion: 'confused', expression: 'confused', motion: 'shake_head', intensity: 0.75 })
    }
  }

  async applyBehavior(behavior: CharacterBehaviorPayload): Promise<void> {
    await Promise.all([
      this.applyExpression(behavior.expression),
      this.applyMotion(behavior.motion),
    ])
  }

  async applyDebugSelection(expression: string, motion: string): Promise<void> {
    await Promise.all([
      this.applyExpressionDirect(expression),
      this.applyMotionDirect(motion),
    ])
  }

  private async applyExpression(expression: string): Promise<void> {
    if (expression === this.lastExpression) {
      return
    }

    this.lastExpression = expression
    const candidates = EXPRESSION_ALIASES[expression] ?? [expression]

    for (const candidate of candidates) {
      try {
        const applied = candidate ? await this.model.expression(candidate) : await this.model.expression()
        if (applied) {
          return
        }
      }
      catch {
        // Some models do not define named expressions. Try the next alias.
      }
    }
  }

  private async applyExpressionDirect(expression: string): Promise<void> {
    if (expression === this.lastExpression) {
      return
    }

    this.lastExpression = expression

    try {
      if (expression === 'default') {
        await this.model.expression()
        return
      }

      await this.model.expression(expression)
    }
    catch {
      // Debug selections are best-effort because not every model exposes expressions consistently.
    }
  }

  private async applyMotion(motion: string): Promise<void> {
    if (motion === this.lastMotion && motion !== 'talk') {
      return
    }

    this.lastMotion = motion
    const candidates = MOTION_ALIASES[motion] ?? [motion]

    for (const candidate of candidates) {
      try {
        const applied = await this.model.motion(candidate, undefined, MOTION_PRIORITY_FORCE)
        if (applied) {
          return
        }
      }
      catch {
        // Models use different motion group names. Try the next alias.
      }
    }
  }

  private async applyMotionDirect(motion: string): Promise<void> {
    if (motion === this.lastMotion) {
      return
    }

    this.lastMotion = motion

    try {
      await this.model.motion(motion, undefined, MOTION_PRIORITY_FORCE)
    }
    catch {
      // Debug selections are best-effort because not every model exposes motion groups consistently.
    }
  }

  private readCapabilities(): Live2DModelCapabilities {
    const internals = this.model as unknown as Live2DInternalsLike
    const settings = internals.settings ?? internals.internalModel?.settings
    const fileReferences = settings?.json?.FileReferences
    const motionDefinitions = internals.internalModel?.motionManager?.definitions
    const expressionDefinitions = internals.internalModel?.expressionManager?.definitions

    const motions = uniqueStrings([
      ...Object.keys(fileReferences?.Motions ?? {}),
      ...Object.keys(settings?.motions ?? {}),
      ...Object.keys(motionDefinitions ?? {}),
    ])

    const expressions = uniqueStrings([
      ...extractExpressionNames(fileReferences?.Expressions),
      ...extractExpressionNames(settings?.expressions),
      ...extractExpressionNames(expressionDefinitions),
    ])

    return {
      expressions: expressions.length ? expressions : ['default'],
      motions: motions.length ? motions : ['TapBody', 'Idle'],
    }
  }
}

function uniqueStrings(values: Array<string | undefined>): string[] {
  return Array.from(new Set(values.filter((value): value is string => Boolean(value && value.trim()))))
}

function extractExpressionNames(values: Live2DSettingsLike['expressions']): string[] {
  if (!values) {
    return []
  }

  return values
    .map((value) => {
      if (typeof value === 'string') {
        return value
      }

      return value.Name ?? value.name
    })
    .filter((value): value is string => Boolean(value && value.trim()))
}

function fillSelect(select: HTMLSelectElement | null, values: string[], fallback: string): void {
  if (!select) {
    return
  }

  select.replaceChildren()
  for (const value of values.length ? values : [fallback]) {
    const option = document.createElement('option')
    option.value = value
    option.textContent = value
    select.append(option)
  }
}

function updateDebugCapabilities(capabilities: Live2DModelCapabilities): void {
  fillSelect(debugExpression, capabilities.expressions, 'default')
  fillSelect(debugMotion, capabilities.motions, 'TapBody')

  if (debugCapabilities) {
    debugCapabilities.textContent = `${capabilities.expressions.length} expressions, ${capabilities.motions.length} motion groups`
  }
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, label: string): Promise<T> {
  let timeoutId: number | undefined
  const timeout = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => {
      reject(new Error(`${label} timed out after ${timeoutMs}ms`))
    }, timeoutMs)
  })

  try {
    return await Promise.race([promise, timeout])
  }
  finally {
    if (timeoutId !== undefined) {
      window.clearTimeout(timeoutId)
    }
  }
}

function loadScript(src: string): Promise<void> {
  const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`)
  if (existing?.dataset.loaded === 'true') {
    return Promise.resolve()
  }

  return new Promise((resolve, reject) => {
    const script = existing ?? document.createElement('script')
    script.src = src
    script.async = true

    script.addEventListener('load', () => {
      script.dataset.loaded = 'true'
      resolve()
    }, { once: true })

    script.addEventListener('error', () => {
      reject(new Error(`Could not load script: ${src}`))
    }, { once: true })

    if (!existing) {
      document.head.append(script)
    }
  })
}

async function loadCubismCore(): Promise<void> {
  if ('Live2DCubismCore' in window) {
    return
  }

  setStatus('Loading Cubism runtime...')
  await withTimeout(
    loadScript(CUBISM_CORE_URL),
    CUBISM_CORE_TIMEOUT_MS,
    'Cubism runtime loading',
  )
}

function setStatus(message: string, visible = true): void {
  if (!statusElement) {
    return
  }

  statusElement.textContent = message
  statusElement.hidden = !visible
}

async function bootLive2D(): Promise<void> {
  if (!stageElement) {
    return
  }

  const app = new Application({
    resizeTo: stageElement,
    autoStart: true,
    antialias: true,
    backgroundAlpha: 0,
  })

  stageElement.append(app.view as HTMLCanvasElement)

  const modelUrl = import.meta.env.VITE_LIVE2D_MODEL_URL || DEFAULT_MODEL_URL

  try {
    await loadCubismCore()
    const { Live2DModel } = await import('pixi-live2d-display/cubism4')
    setStatus('Loading Live2D model...')
    const model = await withTimeout(
      Live2DModel.from(modelUrl),
      LIVE2D_LOAD_TIMEOUT_MS,
      'Live2D model loading',
    )
    model.anchor.set(0.5, 0.5)
    app.stage.addChild(model)
    live2dController = new Live2DController(model)
    updateDebugCapabilities(live2dController.capabilities)

    const fitModel = (): void => {
      const bounds = stageElement.getBoundingClientRect()
      const scale = Math.min(bounds.width / model.width, bounds.height / model.height) * 0.92
      model.scale.set(scale)
      model.x = bounds.width / 2
      model.y = bounds.height / 2
    }

    fitModel()
    window.addEventListener('resize', fitModel)

    stageElement.addEventListener('pointermove', (event) => {
      const rect = stageElement.getBoundingClientRect()
      live2dController?.focus(event.clientX - rect.left, event.clientY - rect.top, rect.width, rect.height)
    })

    stageElement.addEventListener('click', () => {
      void live2dController?.applyBehavior({
        emotion: 'curious',
        expression: 'curious',
        motion: 'TapBody',
        intensity: 0.6,
      })
    })

    void live2dController.applyState('idle')
    setStatus('Live2D ready', false)
  }
  catch (error) {
    console.error(error)
    const message = error instanceof Error ? error.message : 'Unknown Live2D loading error'
    setStatus(`Live2D failed: ${message}`)
  }
}

function bootControls(): void {
  if (pinButton) {
    pinButton.textContent = pinned ? 'Unpin' : 'Pin'
    pinButton.title = pinned ? 'Keep window no longer always on top' : 'Keep window always on top'
  }

  pinButton?.addEventListener('click', async () => {
    pinned = !pinned
    await window.amadeus?.setAlwaysOnTop(pinned)
    pinButton.textContent = pinned ? 'Unpin' : 'Pin'
    pinButton.title = pinned ? 'Keep window no longer always on top' : 'Keep window always on top'
  })

  closeButton?.addEventListener('click', () => {
    void window.amadeus?.closeWindow()
  })

  minimizeButton?.addEventListener('click', () => {
    pinned = false
    if (pinButton) {
      pinButton.textContent = 'Pin'
      pinButton.title = 'Keep window always on top'
    }
    void window.amadeus?.minimizeWindow()
  })

  debugApply?.addEventListener('click', () => {
    const state = (debugState?.value || 'idle') as AssistantState
    const expression = debugExpression?.value || 'neutral'
    const motion = debugMotion?.value || 'idle'
    void live2dController?.applyState(state)
    void live2dController?.applyDebugSelection(expression, motion)
  })
}

const runtimeUi = new RuntimeUiController({
  elements: {
    statusElement,
    chatForm,
    chatInput,
    chatLog,
    voiceButton,
    providerLabel,
    connectionLabel,
    statusDot: document.querySelector<HTMLSpanElement>('#status-dot'),
    memoryStatus,
    toolStatus,
    toolConfigStatus,
    toolPermission,
    toolPermissionText,
    toolAllowButton,
    toolDenyButton,
    memoryReviewStatus,
    memoryReviewRunButton,
    memoryReviewList,
    voiceStatus,
    resetSessionButton,
  },
  wsUrl: import.meta.env.VITE_AGENT_WS_URL || 'ws://127.0.0.1:8788/ws',
  modelLabel: import.meta.env.VITE_OPENAI_MODEL || 'deepseek-v4-flash',
  createSocket: (url) => new WebSocket(url),
  createAudio: (url) => new Audio(url),
  createUtterance: (text) => new SpeechSynthesisUtterance(text),
  randomUUID: () => crypto.randomUUID(),
  setTimeout: (handler, timeout) => window.setTimeout(handler, timeout),
  clearTimeout: (id) => window.clearTimeout(id),
  speechSynthesis: 'speechSynthesis' in window ? window.speechSynthesis : undefined,
  live2d: {
    applyState: (state) => live2dController?.applyState(state),
    applyBehavior: (behavior) => live2dController?.applyBehavior(behavior),
    startMouthLoop: () => live2dController?.startMouthLoop(),
    stopMouthLoop: () => live2dController?.stopMouthLoop(),
  },
})

bootControls()
runtimeUi.bindControls()
runtimeUi.connectAgentRuntime()
void bootLive2D()
