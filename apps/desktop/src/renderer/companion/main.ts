import { Application } from '@pixi/app'
import type {
  AudioLipsyncCuesPayload,
  AssistantState,
  CharacterBehaviorPayload,
} from '@amadeus-agent/amadeus/events'
import type { Live2DModel as Live2DModelClass } from 'pixi-live2d-display/cubism4'
import * as PIXI from 'pixi.js'

import { RuntimeUiController, type RuntimeAudioLike } from '../runtime-ui'
import './styles.css'

window.PIXI = PIXI
console.info('Amadeus companion renderer booting')

const runtimeQuery = new URLSearchParams(window.location.search)
const DEFAULT_MODEL_URL = 'https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display/test/assets/haru/haru_greeter_t03.model3.json'
const AGENT_HTTP_URL = runtimeQuery.get('agentHttpUrl') || import.meta.env.VITE_AGENT_HTTP_URL || 'http://127.0.0.1:8788'
const BASE_AGENT_WS_URL = runtimeQuery.get('agentWsUrl') || import.meta.env.VITE_AGENT_WS_URL || 'ws://127.0.0.1:8788/ws'
const SESSION_ID = runtimeQuery.get('sessionId') || import.meta.env.VITE_AMADEUS_SESSION_ID || 'companion:default'
const SKIP_LIVE2D = runtimeQuery.get('skipLive2d') === '1'
const MOCK_LIVE2D = runtimeQuery.get('mockLive2d') === '1'
const MOCK_AUDIO = runtimeQuery.get('mockAudio')
const DISABLE_SKILL_PERSISTENCE = runtimeQuery.get('disableSkillPersistence') === '1'
const CUBISM_CORE_URL = 'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js'
const CUBISM_CORE_TIMEOUT_MS = 8000
const MOTION_PRIORITY_FORCE = 3
const LIVE2D_LOAD_TIMEOUT_MS = 15000
const LIVE2D_CONFIG_TIMEOUT_MS = 2500
const LIVE2D_MAX_LOAD_RETRIES = 5
const DEFAULT_LIVE2D_DISPLAY_CONFIG = {
  scale: 0.92,
  offsetX: 0,
  offsetY: 0,
}
const COMPANION_PANEL_HIDE_DELAY_MS = 1500

function wsUrlForSurface(url: string, surface: string, sessionId: string): string {
  const parsed = new URL(url)
  parsed.searchParams.set('surface', surface)
  parsed.searchParams.set('sessionId', sessionId)
  return parsed.toString()
}

const stageElement = document.querySelector<HTMLDivElement>('#live2d-stage')
const statusElement = document.querySelector<HTMLDivElement>('#stage-status')
const chatForm = document.querySelector<HTMLFormElement>('#chat-form')
const chatInput = document.querySelector<HTMLInputElement>('#chat-input')
const chatLog = document.querySelector<HTMLDivElement>('#chat-log')
const skillsStatus = document.querySelector<HTMLSpanElement>('#skills-status')
const skillsSearchInput = document.querySelector<HTMLInputElement>('#skills-search-input')
const skillsList = document.querySelector<HTMLDivElement>('#skills-list')
const skillsRefreshButton = document.querySelector<HTMLButtonElement>('#skills-refresh-button')
const skillDetailTitle = document.querySelector<HTMLSpanElement>('#skill-detail-title')
const skillDetailBody = document.querySelector<HTMLDivElement>('#skill-detail-body')
const pinButton = document.querySelector<HTMLButtonElement>('#pin-button')
const minimizeButton = document.querySelector<HTMLButtonElement>('#minimize-button')
const voiceButton = document.querySelector<HTMLButtonElement>('#voice-button')
const openMainUiButton = document.querySelector<HTMLButtonElement>('#open-main-ui-button')
const closeButton = document.querySelector<HTMLButtonElement>('#close-button')
const providerLabel = document.querySelector<HTMLElement>('#provider-label')
const connectionLabel = document.querySelector<HTMLElement>('#connection-label')
const memoryStatus = document.querySelector<HTMLSpanElement>('#memory-status')
const toolStatus = document.querySelector<HTMLDivElement>('#tool-status')
const skillStatus = document.querySelector<HTMLDivElement>('#skill-status')
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
const live2dModelStatus = document.querySelector<HTMLSpanElement>('#live2d-model-status')
const live2dModelSelect = document.querySelector<HTMLSelectElement>('#live2d-model-select')
const live2dModelRefresh = document.querySelector<HTMLButtonElement>('#live2d-model-refresh')

let pinned = true
let live2dController: Live2DController | undefined
let live2dApp: Application | undefined
let live2dModel: Live2DModelInstance | undefined
let live2dResizeHandler: (() => void) | undefined
let live2dLoadRetryCount = 0
let live2dLoadRetryTimer: number | undefined
let live2dLoadToken = 0
let activeLive2DModelId = ''
let live2dDisplayConfig = { ...DEFAULT_LIVE2D_DISPLAY_CONFIG }
let companionPanelHideTimer: number | undefined
let companionPanelVisible = false

interface DesktopGlobalCursorPayload {
  cursor: { x: number; y: number }
  window: { x: number; y: number; width: number; height: number }
}

interface Live2DCoreModel {
  setParameterValueById: (id: string, value: number) => void
}

interface Live2DFocusModel {
  focus: (x: number, y: number, instant?: boolean) => void
}

type Live2DModelConstructor = typeof Live2DModelClass
type Live2DModelInstance = Awaited<ReturnType<Live2DModelConstructor['from']>>

interface Live2DModelCapabilities {
  expressions: string[]
  motions: string[]
}

interface Live2DModelManifest {
  displayName?: string
  defaults?: {
    expression?: string
    motion?: string
  }
  aliases?: {
    expressions?: Record<string, string[]>
    motions?: Record<string, string[]>
  }
}

interface Live2DResolvedModel {
  id: string
  path: string
  url: string
  manifest?: Live2DModelManifest
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

interface Live2DRuntimeConfig {
  ok?: boolean
  model?: Partial<Live2DResolvedModel>
  display?: Partial<Live2DDisplayConfig>
}

interface Live2DDisplayConfig {
  scale: number
  offsetX: number
  offsetY: number
}

interface Live2DModelListItem {
  id: string
  path: string
  url: string
  active: boolean
  manifest?: Live2DModelManifest
}

interface Live2DModelsResponse {
  ok?: boolean
  models?: Live2DModelListItem[]
  activeModel?: {
    id?: string
    path?: string
    url?: string
    manifest?: Live2DModelManifest
  }
}

interface Live2DSelectResponse {
  ok?: boolean
  model?: {
    id?: string
    path?: string
    url?: string
    manifest?: Live2DModelManifest
  }
  error?: string
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
  private mouthAnimationFrame: number | undefined
  private lipsyncCueTimers: number[] = []
  private audioContext: AudioContext | undefined
  private analyserNode: AnalyserNode | undefined
  private mediaSourceNode: MediaElementAudioSourceNode | undefined
  private readonly expressionAliases: Record<string, string[]>
  private readonly motionAliases: Record<string, string[]>
  readonly capabilities: Live2DModelCapabilities

  constructor(
    private readonly model: Live2DModelInstance,
    private readonly manifest: Live2DModelManifest = {},
  ) {
    this.expressionAliases = mergeAliasMap(EXPRESSION_ALIASES, manifest.aliases?.expressions)
    this.motionAliases = mergeAliasMap(MOTION_ALIASES, manifest.aliases?.motions)
    this.capabilities = this.readCapabilities()
  }

  dispose(): void {
    this.stopMouthLoop()
  }

  focus(pointerX: number, pointerY: number, width: number, height: number): void {
    const focusModel = this.model as unknown as Live2DFocusModel
    if (typeof focusModel.focus === 'function') {
      focusModel.focus(pointerX, pointerY)
      return
    }

    const x = Math.max(-18, Math.min(18, (pointerX / width - 0.5) * 24))
    const y = Math.max(-18, Math.min(18, (pointerY / height - 0.5) * 24))
    const coreModel = this.model.internalModel.coreModel as Live2DCoreModel
    coreModel.setParameterValueById('ParamAngleX', x)
    coreModel.setParameterValueById('ParamAngleY', -y)
  }

  setMouthOpen(value: number): void {
    const coreModel = this.model.internalModel.coreModel as Live2DCoreModel
    coreModel.setParameterValueById('ParamMouthOpenY', Math.max(0, Math.min(1, value)))
  }

  applyLipsyncCues(payload: AudioLipsyncCuesPayload): void {
    this.stopTimedMouthLoop()
    this.stopCueDrivenMouth()
    this.stopAudioDrivenAnimation()
    for (const cue of payload.cues) {
      const offsetMs = Math.max(0, Math.trunc(cue.offsetMs))
      const mouthOpen = typeof cue.mouthOpen === 'number' ? cue.mouthOpen : 0
      const timer = window.setTimeout(() => {
        this.setMouthOpen(mouthOpen)
      }, offsetMs)
      this.lipsyncCueTimers.push(timer)
    }

    const durationMs = typeof payload.durationMs === 'number' ? payload.durationMs : 0
    const tailTimer = window.setTimeout(() => {
      this.setMouthOpen(0)
    }, Math.max(durationMs, 0))
    this.lipsyncCueTimers.push(tailTimer)
  }

  startRuntimeAudioLipsync(audio: RuntimeAudioLike): boolean {
    if (!(audio instanceof HTMLMediaElement)) {
      return false
    }

    const AudioContextCtor = window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioContextCtor) {
      return false
    }

    try {
      this.stopAudioDrivenMouth()
      const audioContext = this.audioContext ?? new AudioContextCtor()
      this.audioContext = audioContext
      if (audioContext.state === 'suspended') {
        void audioContext.resume().catch(() => {})
      }

      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = 0.72

      const mediaSource = audioContext.createMediaElementSource(audio)
      mediaSource.connect(analyser)
      analyser.connect(audioContext.destination)

      this.mediaSourceNode = mediaSource
      this.analyserNode = analyser
      const samples = new Uint8Array(analyser.fftSize)
      const tick = () => {
        if (!this.analyserNode) {
          return
        }

        this.analyserNode.getByteTimeDomainData(samples)
        let energy = 0
        for (const sample of samples) {
          const normalized = (sample - 128) / 128
          energy += normalized * normalized
        }
        const rms = Math.sqrt(energy / samples.length)
        const mouthOpen = Math.min(1, Math.max(0, 0.05 + rms * 3.1))
        this.setMouthOpen(mouthOpen)
        this.mouthAnimationFrame = window.requestAnimationFrame(tick)
      }

      this.mouthAnimationFrame = window.requestAnimationFrame(tick)
      return true
    }
    catch (error) {
      console.warn('Live2D audio-driven lipsync unavailable', error)
      this.stopAudioDrivenMouth()
      return false
    }
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
    this.stopAudioDrivenMouth()
    this.stopCueDrivenMouth()
    this.stopTimedMouthLoop()
    this.setMouthOpen(0)
  }

  private stopAudioDrivenMouth(): void {
    this.stopAudioDrivenAnimation()
    this.mediaSourceNode?.disconnect()
    this.mediaSourceNode = undefined
    this.analyserNode?.disconnect()
    this.analyserNode = undefined
  }

  private stopAudioDrivenAnimation(): void {
    if (this.mouthAnimationFrame !== undefined) {
      window.cancelAnimationFrame(this.mouthAnimationFrame)
      this.mouthAnimationFrame = undefined
    }
  }

  private stopCueDrivenMouth(): void {
    for (const timer of this.lipsyncCueTimers) {
      window.clearTimeout(timer)
    }
    this.lipsyncCueTimers = []
  }

  private stopTimedMouthLoop(): void {
    if (this.mouthTimer !== undefined) {
      window.clearInterval(this.mouthTimer)
      this.mouthTimer = undefined
    }
  }

  async applyState(state: AssistantState): Promise<void> {
    if (state === 'idle') {
      await this.applyBehavior({
        emotion: 'neutral',
        expression: this.manifest.defaults?.expression ?? 'neutral',
        motion: this.manifest.defaults?.motion ?? 'idle',
        intensity: 0.35,
      })
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
    const candidates = this.expressionAliases[expression] ?? [expression]

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
    const candidates = this.motionAliases[motion] ?? [motion]

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

function mergeAliasMap(base: Record<string, string[]>, override: Record<string, string[]> | undefined): Record<string, string[]> {
  if (!override) {
    return base
  }

  return {
    ...base,
    ...Object.fromEntries(
      Object.entries(override).map(([key, values]) => [key, uniqueStrings([...values, ...(base[key] ?? [])])]),
    ),
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

function updateModelCapabilitySummary(model: Live2DResolvedModel, capabilities: Live2DModelCapabilities): void {
  if (!debugCapabilities) {
    return
  }

  const displayName = model.manifest?.displayName ? `${model.manifest.displayName} (${model.id})` : model.id
  debugCapabilities.textContent = [
    displayName,
    `${capabilities.expressions.length} expressions`,
    `${capabilities.motions.length} motion groups`,
    model.path,
  ].join(' · ')
}

function currentLive2DCapabilities() {
  return {
    available: Boolean(live2dController),
    modelId: activeLive2DModelId || undefined,
    expressions: live2dController?.capabilities.expressions ?? [],
    motions: live2dController?.capabilities.motions ?? [],
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
  if (MOCK_LIVE2D) {
    return
  }

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

function normalizeResolvedModel(model: Partial<Live2DResolvedModel> | undefined): Live2DResolvedModel | undefined {
  if (!model?.url) {
    return undefined
  }

  return {
    id: model.id || 'unknown',
    path: model.path || '',
    url: model.url,
    manifest: model.manifest,
  }
}

function normalizeLive2DDisplayConfig(value: Partial<Live2DDisplayConfig> | undefined): Live2DDisplayConfig {
  const scale = typeof value?.scale === 'number' && value.scale >= 0.25 && value.scale <= 2.5
    ? value.scale
    : DEFAULT_LIVE2D_DISPLAY_CONFIG.scale
  const offsetX = typeof value?.offsetX === 'number' ? value.offsetX : DEFAULT_LIVE2D_DISPLAY_CONFIG.offsetX
  const offsetY = typeof value?.offsetY === 'number' ? value.offsetY : DEFAULT_LIVE2D_DISPLAY_CONFIG.offsetY
  return { scale, offsetX, offsetY }
}

async function resolveLive2DModelConfig(): Promise<Live2DResolvedModel> {
  if (import.meta.env.VITE_LIVE2D_MODEL_URL) {
    return {
      id: 'env',
      path: '',
      url: import.meta.env.VITE_LIVE2D_MODEL_URL,
    }
  }

  try {
    setStatus('Resolving Live2D model...')
    const response = await withTimeout(
      fetch(`${AGENT_HTTP_URL}/live2d/config`),
      LIVE2D_CONFIG_TIMEOUT_MS,
      'Live2D model config loading',
    )
    if (!response.ok) {
      throw new Error(`Live2D config returned ${response.status}`)
    }

    const config = await response.json() as Live2DRuntimeConfig
    const model = normalizeResolvedModel(config.model)
    if (config.ok && model) {
      live2dDisplayConfig = normalizeLive2DDisplayConfig(config.display)
      console.info(`Using configured Live2D model ${model.id}: ${model.url}`)
      updateLive2DModelStatus(`Model: ${model.id} loading`)
      return model
    }
  }
  catch (error) {
    console.warn('Falling back to remote Live2D model', error)
    updateLive2DModelStatus('Model: remote fallback')
  }

  return {
    id: 'remote-fallback',
    path: '',
    url: DEFAULT_MODEL_URL,
  }
}

function updateLive2DModelStatus(text: string): void {
  if (live2dModelStatus) {
    live2dModelStatus.textContent = text
  }
}

async function loadLive2DModelOptions(): Promise<void> {
  if (!live2dModelSelect) {
    return
  }

  try {
    live2dModelRefresh?.setAttribute('disabled', 'true')
    const response = await fetch(`${AGENT_HTTP_URL}/live2d/models`)
    if (!response.ok) {
      throw new Error(`Live2D models returned ${response.status}`)
    }

    const payload = await response.json() as Live2DModelsResponse
    const models = Array.isArray(payload.models) ? payload.models : []
    live2dModelSelect.replaceChildren()
    if (models.length === 0) {
      live2dModelSelect.append(new Option('no local models', ''))
      live2dModelSelect.disabled = true
      updateLive2DModelStatus('Model: none available')
      return
    }

    for (const model of models) {
      const label = model.manifest?.displayName ? `${model.manifest.displayName} (${model.id})` : model.id
      live2dModelSelect.append(new Option(label, model.id, model.active, model.active))
    }
    live2dModelSelect.disabled = false
    const active = models.find((model) => model.active) ?? models.find((model) => model.id === payload.activeModel?.id)
    if (active) {
      live2dModelSelect.value = active.id
      updateLive2DModelStatus(`Model: ${active.id}`)
    }
  }
  catch (error) {
    console.warn('Failed to load Live2D model list', error)
    live2dModelSelect.replaceChildren(new Option('models unavailable', ''))
    live2dModelSelect.disabled = true
    updateLive2DModelStatus('Model: list unavailable')
  }
  finally {
    live2dModelRefresh?.removeAttribute('disabled')
  }
}

async function selectLive2DModel(modelId: string): Promise<void> {
  if (!modelId) {
    return
  }

  if (modelId === activeLive2DModelId) {
    updateLive2DModelStatus(`Model: ${modelId} already loaded`)
    return
  }

  const previousModelId = activeLive2DModelId
  updateLive2DModelStatus(`Model: switching to ${modelId}`)
  live2dModelSelect?.setAttribute('disabled', 'true')
  try {
    const response = await fetch(`${AGENT_HTTP_URL}/live2d/select`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modelId }),
    })
    const payload = await response.json().catch(() => undefined) as Live2DSelectResponse | undefined
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.error || `Live2D select returned ${response.status}`)
    }

    const selectedModel = normalizeResolvedModel(payload.model)
    if (!selectedModel) {
      throw new Error('Live2D select response did not include a model URL')
    }

    await loadLive2DModel(selectedModel)
    await loadLive2DModelOptions()
  }
  catch (error) {
    console.warn('Failed to switch Live2D model', error)
    updateLive2DModelStatus(`Model switch failed: ${modelId}`)
    if (previousModelId && previousModelId !== modelId) {
      await persistLive2DModelSelection(previousModelId).catch(() => undefined)
    }
    await loadLive2DModelOptions()
  }
  finally {
    live2dModelSelect?.removeAttribute('disabled')
  }
}

async function persistLive2DModelSelection(modelId: string): Promise<Live2DResolvedModel | undefined> {
  const response = await fetch(`${AGENT_HTTP_URL}/live2d/select`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ modelId }),
  })
  const payload = await response.json().catch(() => undefined) as Live2DSelectResponse | undefined
  if (!response.ok || !payload?.ok) {
    return undefined
  }
  return normalizeResolvedModel(payload.model)
}

function setStatus(message: string, visible = true): void {
  if (!statusElement) {
    return
  }

  statusElement.textContent = message
  statusElement.hidden = !visible
  document.body.dataset.live2dStatus = visible ? 'visible' : 'hidden'
}

function focusLive2DFromWindowPoint(windowX: number, windowY: number): void {
  if (!stageElement) {
    return
  }

  const rect = stageElement.getBoundingClientRect()
  live2dController?.focus(windowX - rect.left, windowY - rect.top, rect.width, rect.height)
}

function showCompanionPanel(): void {
  if (companionPanelHideTimer !== undefined) {
    window.clearTimeout(companionPanelHideTimer)
    companionPanelHideTimer = undefined
  }
  if (companionPanelVisible) {
    return
  }
  companionPanelVisible = true
  document.body.classList.add('companion-panel-open')
}

function hideCompanionPanel(): void {
  companionPanelHideTimer = undefined
  if (!companionPanelVisible) {
    return
  }
  companionPanelVisible = false
  document.body.classList.remove('companion-panel-open')
  chatInput?.blur()
}

function scheduleCompanionPanelHide(delayMs = COMPANION_PANEL_HIDE_DELAY_MS): void {
  if (companionPanelHideTimer !== undefined) {
    window.clearTimeout(companionPanelHideTimer)
  }
  companionPanelHideTimer = window.setTimeout(hideCompanionPanel, delayMs)
}

function isCursorInsideWindow(payload: DesktopGlobalCursorPayload): boolean {
  const { cursor, window: windowBounds } = payload
  return cursor.x >= windowBounds.x
    && cursor.x < windowBounds.x + windowBounds.width
    && cursor.y >= windowBounds.y
    && cursor.y < windowBounds.y + windowBounds.height
}

function handleGlobalCursor(payload: DesktopGlobalCursorPayload): void {
  focusLive2DFromWindowPoint(payload.cursor.x - payload.window.x, payload.cursor.y - payload.window.y)

  if (isCursorInsideWindow(payload)) {
    showCompanionPanel()
    return
  }

  scheduleCompanionPanelHide()
}

function bindGlobalCursorTracking(): void {
  window.amadeus?.onGlobalCursor?.(handleGlobalCursor)
}

function scheduleLive2DRetry(reason: string): void {
  if (live2dLoadRetryCount >= LIVE2D_MAX_LOAD_RETRIES) {
    setStatus(`Live2D failed: ${reason}`)
    return
  }

  live2dLoadRetryCount += 1
  const delayMs = Math.min(5000, 750 * live2dLoadRetryCount)
  setStatus(`Live2D retrying (${live2dLoadRetryCount}/${LIVE2D_MAX_LOAD_RETRIES})...`)
  if (live2dLoadRetryTimer) {
    window.clearTimeout(live2dLoadRetryTimer)
  }
  live2dLoadRetryTimer = window.setTimeout(() => {
    live2dLoadRetryTimer = undefined
    void retryLive2DLoad()
  }, delayMs)
}

async function retryLive2DLoad(): Promise<void> {
  try {
    await loadLive2DModel(await resolveLive2DModelConfig())
  }
  catch (error) {
    console.error(error)
    const message = error instanceof Error ? error.message : 'Unknown Live2D loading error'
    scheduleLive2DRetry(message)
  }
}

async function bootLive2D(): Promise<void> {
  if (!stageElement) {
    return
  }

  live2dApp = new Application({
    resizeTo: stageElement,
    autoStart: true,
    antialias: true,
    backgroundAlpha: 0,
  })

  stageElement.append(live2dApp.view as HTMLCanvasElement)
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

  try {
    await loadLive2DModel(await resolveLive2DModelConfig())
    live2dLoadRetryCount = 0
  }
  catch (error) {
    console.error(error)
    const message = error instanceof Error ? error.message : 'Unknown Live2D loading error'
    scheduleLive2DRetry(message)
  }
}

async function loadLive2DModel(modelConfig: Live2DResolvedModel): Promise<void> {
  if (!stageElement || !live2dApp) {
    return
  }

  const loadToken = live2dLoadToken + 1
  live2dLoadToken = loadToken
  updateLive2DModelStatus(`Model: ${modelConfig.id} loading`)
  setStatus(`Loading Live2D model: ${modelConfig.id}`)
  console.info(`Loading Live2D model ${modelConfig.id} from ${modelConfig.url}`)

  await loadCubismCore()
  const modelPromise = MOCK_LIVE2D
    ? createMockLive2DModel(modelConfig.url)
    : import('pixi-live2d-display/cubism4').then(({ Live2DModel }) => Live2DModel.from(modelConfig.url))
  const nextModel = await withTimeout(
    modelPromise,
    LIVE2D_LOAD_TIMEOUT_MS,
    'Live2D model loading',
  )
  if (loadToken !== live2dLoadToken) {
    destroyLive2DModel(nextModel)
    return
  }

  nextModel.anchor.set(0.5, 0.5)
  const previousModel = live2dModel
  const previousController = live2dController
  const previousResizeHandler = live2dResizeHandler

  live2dApp.stage.addChild(nextModel)
  live2dModel = nextModel
  live2dController = new Live2DController(nextModel, modelConfig.manifest)
  activeLive2DModelId = modelConfig.id

  const fitModel = (): void => {
    const bounds = stageElement.getBoundingClientRect()
    const scale = Math.min(bounds.width / nextModel.width, bounds.height / nextModel.height) * live2dDisplayConfig.scale
    nextModel.scale.set(scale)
    nextModel.x = bounds.width / 2 + live2dDisplayConfig.offsetX
    nextModel.y = bounds.height / 2 + live2dDisplayConfig.offsetY
  }
  live2dResizeHandler = fitModel
  fitModel()
  window.addEventListener('resize', fitModel)

  if (previousResizeHandler) {
    window.removeEventListener('resize', previousResizeHandler)
  }
  if (previousModel) {
    live2dApp.stage.removeChild(previousModel)
    previousController?.dispose()
    destroyLive2DModel(previousModel)
  }

  updateDebugCapabilities(live2dController.capabilities)
  updateModelCapabilitySummary(modelConfig, live2dController.capabilities)
  updateLive2DModelStatus(`Model: ${modelConfig.id} loaded`)
  if (live2dModelSelect && live2dModelSelect.value !== modelConfig.id) {
    live2dModelSelect.value = modelConfig.id
  }
  runtimeUi.reportDesktopCapabilities()
  void live2dController.applyState('idle')
  live2dLoadRetryCount = 0
  setStatus('Live2D ready', false)
}

function destroyLive2DModel(model: Live2DModelInstance): void {
  try {
    ;(model as unknown as { destroy: (options?: unknown) => void }).destroy({ children: true })
  }
  catch {
    // Destroy is best-effort; Pixi can retain shared textures for later model reloads.
  }
}

async function createMockLive2DModel(_url: string): Promise<Live2DModelInstance> {
  const model = new PIXI.Container() as unknown as Live2DModelInstance
  const mock = model as unknown as {
    anchor: { set: (x: number, y?: number) => void }
    width: number
    height: number
    settings: Live2DSettingsLike
    internalModel: {
      coreModel: Live2DCoreModel
      settings: Live2DSettingsLike
      motionManager: { definitions: Record<string, unknown[]> }
      expressionManager: { definitions: Array<{ Name: string }> }
    }
    expression: (name?: string) => Promise<boolean>
    motion: (name: string, index?: number, priority?: number) => Promise<boolean>
  }
  const settings: Live2DSettingsLike = {
    json: {
      FileReferences: {
        Expressions: [{ Name: 'neutral' }, { Name: 'smile' }],
        Motions: {
          Idle: [],
          TapBody: [],
        },
      },
    },
  }

  mock.anchor = { set: () => {} }
  mock.width = 320
  mock.height = 480
  mock.settings = settings
  mock.internalModel = {
    coreModel: {
      setParameterValueById: () => {},
    },
    settings,
    motionManager: {
      definitions: {
        Idle: [],
        TapBody: [],
      },
    },
    expressionManager: {
      definitions: [{ Name: 'neutral' }, { Name: 'smile' }],
    },
  }
  mock.expression = async () => true
  mock.motion = async () => true
  return model
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

  openMainUiButton?.addEventListener('click', () => {
    void window.amadeus?.openMainUi(SESSION_ID)
  })

  debugApply?.addEventListener('click', () => {
    const state = (debugState?.value || 'idle') as AssistantState
    const expression = debugExpression?.value || 'neutral'
    const motion = debugMotion?.value || 'idle'
    void live2dController?.applyState(state)
    void live2dController?.applyDebugSelection(expression, motion)
  })

  live2dModelSelect?.addEventListener('change', () => {
    const modelId = live2dModelSelect.value
    void selectLive2DModel(modelId)
  })

  live2dModelRefresh?.addEventListener('click', () => {
    void loadLive2DModelOptions()
  })
}

function createMockRuntimeAudio(_url: string, mode: string): RuntimeAudioLike {
  const listeners = new Map<string, Array<() => void>>()
  let paused = false
  const emit = (type: string): void => {
    for (const listener of listeners.get(type) ?? []) {
      listener()
    }
  }

  return {
    addEventListener(type: 'play' | 'ended' | 'error', listener: () => void): void {
      listeners.set(type, [...(listeners.get(type) ?? []), listener])
    },
    async play(): Promise<void> {
      if (mode === 'reject') {
        throw new Error('mock audio play rejected')
      }

      paused = false
      window.setTimeout(() => emit('play'), 0)
      window.setTimeout(() => {
        if (paused) {
          return
        }
        emit(mode === 'error' ? 'error' : 'ended')
      }, 150)
    },
    pause(): void {
      paused = true
    },
  }
}

const runtimeUi = new RuntimeUiController({
  elements: {
    statusElement: null,
    chatForm,
    chatInput,
    chatLog,
    skillsStatus,
    skillsSearchInput,
    skillsList,
    skillsRefreshButton,
    skillDetailTitle,
    skillDetailBody,
    voiceButton,
    providerLabel,
    connectionLabel,
    statusDot: document.querySelector<HTMLSpanElement>('#status-dot'),
    memoryStatus,
    toolStatus,
    skillStatus,
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
  wsUrl: wsUrlForSurface(BASE_AGENT_WS_URL, 'companion', SESSION_ID),
  skillsUrl: `${AGENT_HTTP_URL}/skills/list`,
  modelLabel: import.meta.env.VITE_OPENAI_MODEL || 'deepseek-v4-flash',
  createSocket: (url) => new WebSocket(url),
  createAudio: (url) => {
    if (MOCK_AUDIO) {
      return createMockRuntimeAudio(url, MOCK_AUDIO)
    }
    const audio = new Audio(url)
    audio.crossOrigin = 'anonymous'
    return audio
  },
  createUtterance: (text) => new SpeechSynthesisUtterance(text),
  randomUUID: () => crypto.randomUUID(),
  setTimeout: (handler, timeout) => window.setTimeout(handler, timeout),
  clearTimeout: (id) => window.clearTimeout(id),
  fetchImpl: (input, init) => window.fetch(input, init),
  storage: DISABLE_SKILL_PERSISTENCE ? undefinedStorage() : window.localStorage,
  speechSynthesis: 'speechSynthesis' in window ? window.speechSynthesis : undefined,
  live2d: {
    applyState: (state) => live2dController?.applyState(state),
    applyBehavior: (behavior) => live2dController?.applyBehavior(behavior),
    applyLipsyncCues: (payload) => live2dController?.applyLipsyncCues(payload),
    startRuntimeAudioLipsync: (audio) => live2dController?.startRuntimeAudioLipsync(audio) ?? false,
    startMouthLoop: () => live2dController?.startMouthLoop(),
    stopMouthLoop: () => live2dController?.stopMouthLoop(),
    getCapabilities: () => currentLive2DCapabilities(),
  },
})

function undefinedStorage() {
  return {
    getItem() {
      return null
    },
    setItem() {},
    removeItem() {},
  }
}

bootControls()
bindGlobalCursorTracking()
runtimeUi.bindControls()
runtimeUi.connectAgentRuntime()
if (SKIP_LIVE2D) {
  updateLive2DModelStatus('Model: skipped for E2E')
  setStatus('Live2D skipped for E2E', false)
}
else {
  void loadLive2DModelOptions()
  void bootLive2D()
}
