import type { ServerRuntimeEvent, TaskRecord, TaskSummary, TaskUpdatedPayload, TaskPlanPayload } from '@amadeus-agent/amadeus/events'
import { RuntimeUiController, type RuntimeAudioLike } from '../runtime-ui'
import './styles.css'

const runtimeQuery = new URLSearchParams(window.location.search)
const AGENT_HTTP_URL = runtimeQuery.get('agentHttpUrl') || import.meta.env.VITE_AGENT_HTTP_URL || 'http://127.0.0.1:8790'
const BASE_AGENT_WS_URL = runtimeQuery.get('agentWsUrl') || import.meta.env.VITE_AGENT_WS_URL || 'ws://127.0.0.1:8788/ws'
const SESSION_ID = runtimeQuery.get('sessionId') || import.meta.env.VITE_AMADEUS_SESSION_ID || 'companion:default'
const DISABLE_SKILL_PERSISTENCE = runtimeQuery.get('disableSkillPersistence') === '1'

function wsUrlForSurface(url: string, surface: string, sessionId: string): string {
  const parsed = new URL(url)
  parsed.searchParams.set('surface', surface)
  parsed.searchParams.set('sessionId', sessionId)
  return parsed.toString()
}

function query<T extends Element>(selector: string): T | null {
  return document.querySelector<T>(selector)
}

interface RuntimeConfigPayload {
  ok?: boolean
  api?: {
    provider?: string
    providerLabel?: string
    envVar?: string
    requiresApiKey?: boolean
    baseUrl?: string
    model?: string
    apiKeyConfigured?: boolean
    apiKeyPreview?: string
  }
  providers?: Array<{
    id: string
    label: string
    envVar: string
    baseUrl: string
    defaultModel: string
    requiresApiKey: boolean
    supportsStreaming: boolean
  }>
  runtime?: {
    context?: {
      maxTokens?: number
      compactionTriggerRatio?: number
      memoryItemLimit?: number
    }
    summary?: {
      triggerMessageCount?: number
    }
    memoryReview?: {
      triggerMessageCount?: number
    }
    desktop?: {
      companionLive2dScale?: number
      companionLive2dOffsetX?: number
      companionLive2dOffsetY?: number
    }
  }
  error?: string
}

type ProviderPayload = NonNullable<RuntimeConfigPayload['providers']>[number]

interface RolePayload {
  id: string
  name: string
  description: string
  persona: string
  style: string
  provider: string
  model: string
  live2dModel: string
  ttsVoice: string
  archived: boolean
  createdAt: string
  updatedAt: string
}

interface SessionPayload {
  id: string
  roleId: string
  title: string
  archived: boolean
  roleName: string
  messageCount: number
  createdAt: string
  updatedAt: string
}

interface SessionPlanResponse {
  ok?: boolean
  plan?: TaskPlanPayload
  error?: string
}

interface TasksResponse {
  ok?: boolean
  tasks?: TaskRecord[]
  summary?: TaskSummary
  error?: string
}

class MockAudio implements RuntimeAudioLike {
  private readonly listeners = new Map<string, Array<() => void>>()

  addEventListener(type: 'play' | 'ended' | 'error', listener: () => void): void {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener)
    this.listeners.set(type, listeners)
  }

  async play(): Promise<void> {
    this.emit('play')
    window.setTimeout(() => this.emit('ended'), 50)
  }

  pause(): void {}

  private emit(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener()
    }
  }
}

const closeButton = query<HTMLButtonElement>('#close-button')
const minimizeButton = query<HTMLButtonElement>('#minimize-button')
const fullscreenButton = query<HTMLButtonElement>('#fullscreen-button')
const settingsToggleButton = query<HTMLButtonElement>('#settings-toggle-button')
const settingsPanel = query<HTMLElement>('#settings-panel')
const chatForm = query<HTMLFormElement>('#chat-form')
const sessionSwitcher = query<HTMLElement>('#session-switcher')
const sessionMenuButton = query<HTMLButtonElement>('#session-menu-button')
const sessionMenu = query<HTMLElement>('#session-menu')
const currentSessionLabel = query<HTMLElement>('#current-session-label')
const sessionMenuList = query<HTMLElement>('#session-menu-list')
const newSessionMainButton = query<HTMLButtonElement>('#new-session-main-button')
const sessionPlanPanel = query<HTMLElement>('#session-plan-panel')
const sessionPlanSummary = query<HTMLElement>('#session-plan-summary')
const sessionPlanList = query<HTMLOListElement>('#session-plan-list')
const sessionTasksPanel = query<HTMLElement>('#session-tasks-panel')
const sessionTasksSummary = query<HTMLElement>('#session-tasks-summary')
const sessionTasksList = query<HTMLOListElement>('#session-tasks-list')
const settingsNavButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('.settings-nav-button'))
const settingsSections = Array.from(document.querySelectorAll<HTMLElement>('[data-settings-section]'))
const apiConfigForm = query<HTMLFormElement>('#api-config-form')
const apiConfigStatus = query<HTMLElement>('#api-config-status')
const roleSessionStatus = query<HTMLElement>('#role-session-status')
const roleList = query<HTMLElement>('#role-list')
const roleSessionForm = query<HTMLFormElement>('#role-session-form')
const roleEditorTitle = query<HTMLElement>('#role-editor-title')
const openNewRoleButton = query<HTMLButtonElement>('#open-new-role-button')
const closeRoleEditorButton = query<HTMLButtonElement>('#close-role-editor-button')
const roleSelect = query<HTMLSelectElement>('#role-select')
const newRoleNameInput = query<HTMLInputElement>('#new-role-name-input')
const roleDescriptionInput = query<HTMLInputElement>('#role-description-input')
const newRolePersonaInput = query<HTMLTextAreaElement>('#new-role-persona-input')
const roleStyleInput = query<HTMLInputElement>('#role-style-input')
const roleModelSelect = query<HTMLSelectElement>('#role-model-select')
const roleLive2dSelect = query<HTMLSelectElement>('#role-live2d-select')
const roleTtsSelect = query<HTMLSelectElement>('#role-tts-select')
const saveRoleButton = query<HTMLButtonElement>('#save-role-button')
const createRoleButton = query<HTMLButtonElement>('#create-role-button')
const live2dModelList = query<HTMLElement>('#live2d-model-list')
const ttsVoiceList = query<HTMLElement>('#tts-voice-list')
const modelProfileList = query<HTMLElement>('#model-profile-list')
const modelEditorTitle = query<HTMLElement>('#model-editor-title')
const closeModelEditorButton = query<HTMLButtonElement>('#close-model-editor-button')
const live2dEditor = query<HTMLElement>('#live2d-editor')
const live2dEditorTitle = query<HTMLElement>('#live2d-editor-title')
const live2dEditorDetail = query<HTMLElement>('#live2d-editor-detail')
const closeLive2dEditorButton = query<HTMLButtonElement>('#close-live2d-editor-button')
const ttsEditor = query<HTMLElement>('#tts-editor')
const ttsEditorTitle = query<HTMLElement>('#tts-editor-title')
const ttsEditorDetail = query<HTMLElement>('#tts-editor-detail')
const closeTtsEditorButton = query<HTMLButtonElement>('#close-tts-editor-button')
const apiProviderSelect = query<HTMLSelectElement>('#api-provider-select')
const apiProviderIdInput = query<HTMLInputElement>('#api-provider-id-input')
const apiProviderLabelInput = query<HTMLInputElement>('#api-provider-label-input')
const apiBaseUrlInput = query<HTMLInputElement>('#api-base-url-input')
const apiModelInput = query<HTMLInputElement>('#api-model-input')
const apiKeyInput = query<HTMLInputElement>('#api-key-input')
const apiEnvVarInput = query<HTMLInputElement>('#api-env-var-input')
const apiRequiresKeyInput = query<HTMLInputElement>('#api-requires-key-input')
const apiStreamingInput = query<HTMLInputElement>('#api-streaming-input')
const newModelButton = query<HTMLButtonElement>('#new-model-button')
const newLive2dButton = query<HTMLButtonElement>('#new-live2d-button')
const newTtsButton = query<HTMLButtonElement>('#new-tts-button')
const refreshLive2dButton = query<HTMLButtonElement>('#refresh-live2d-button')
const refreshTtsButton = query<HTMLButtonElement>('#refresh-tts-button')
const runtimeConfigForm = query<HTMLFormElement>('#runtime-config-form')
const runtimeConfigStatus = query<HTMLElement>('#runtime-config-status')
const configContextMaxTokens = query<HTMLInputElement>('#config-context-max-tokens')
const configCompactionRatio = query<HTMLInputElement>('#config-compaction-ratio')
const configMemoryItemLimit = query<HTMLInputElement>('#config-memory-item-limit')
const configSummaryTrigger = query<HTMLInputElement>('#config-summary-trigger')
const configReviewTrigger = query<HTMLInputElement>('#config-review-trigger')
const configLive2dScale = query<HTMLInputElement>('#config-live2d-scale')
const configLive2dOffsetX = query<HTMLInputElement>('#config-live2d-offset-x')
const configLive2dOffsetY = query<HTMLInputElement>('#config-live2d-offset-y')
let configLoaded = false
let cachedRoles: RolePayload[] = []
let cachedSessions: SessionPayload[] = []
let cachedProviders: RuntimeConfigPayload['providers'] = []
let cachedLive2dModels: Array<{ id: string, label?: string, active?: boolean }> = []
let cachedVoices: SpeechSynthesisVoice[] = []
const customSelects = new WeakMap<HTMLSelectElement, {
  root: HTMLDivElement
  button: HTMLButtonElement
  value: HTMLSpanElement
  list: HTMLDivElement
}>()

closeButton?.addEventListener('click', () => {
  void window.amadeus?.closeWindow()
})

minimizeButton?.addEventListener('click', () => {
  void window.amadeus?.minimizeWindow()
})

fullscreenButton?.addEventListener('click', async () => {
  const isFullscreen = await window.amadeus?.toggleFullscreen()
  setFullscreenButtonState(Boolean(isFullscreen))
})

void window.amadeus?.isFullscreen?.().then((isFullscreen) => {
  setFullscreenButtonState(Boolean(isFullscreen))
})

settingsToggleButton?.addEventListener('click', () => {
  const nextExpanded = settingsPanel?.hidden ?? false
  if (settingsPanel) {
    settingsPanel.hidden = !nextExpanded
  }
  settingsToggleButton.setAttribute('aria-expanded', String(nextExpanded))
  document.body.dataset.settingsOpen = String(nextExpanded)
  if (nextExpanded && !configLoaded) {
    void loadRuntimeConfig()
  }
})

for (const button of settingsNavButtons) {
  button.addEventListener('click', () => {
    activateSettingsSection(button.dataset.settingsTarget || 'model')
  })
}

sessionMenuButton?.addEventListener('click', (event) => {
  event.stopPropagation()
  toggleSessionMenu()
})

newSessionMainButton?.addEventListener('click', () => {
  void createSession()
})

document.addEventListener('click', (event) => {
  if (sessionSwitcher && !sessionSwitcher.contains(event.target as Node)) {
    closeSessionMenu()
  }
})

chatForm?.addEventListener('submit', () => {
  window.setTimeout(() => {
    void loadSessionLibrary()
  }, 900)
}, true)

apiConfigForm?.addEventListener('submit', (event) => {
  event.preventDefault()
  void saveApiConfig()
})

apiProviderSelect?.addEventListener('change', () => {
  const selectedProvider = findSelectedProvider()
  if (!selectedProvider) {
    return
  }
  populateModelEditor(selectedProvider)
  setConfigStatus(apiConfigStatus, `${selectedProvider.label} selected`)
})

newModelButton?.addEventListener('click', () => {
  const customId = uniqueProviderId('custom_model')
  populateModelEditor({
    id: customId,
    label: 'New Custom Model',
    envVar: `${customId.toUpperCase()}_API_KEY`,
    baseUrl: 'http://127.0.0.1:11434/v1',
    defaultModel: '',
    requiresApiKey: false,
    supportsStreaming: true,
  })
  if (apiProviderSelect) {
    apiProviderSelect.value = ''
  }
  openModelEditor('new')
  syncCustomSelects()
  setConfigStatus(apiConfigStatus, 'Editing new model')
})

runtimeConfigForm?.addEventListener('submit', (event) => {
  event.preventDefault()
  void saveRuntimeConfig()
})

roleSelect?.addEventListener('change', () => {
  renderRoleBindings()
})

openNewRoleButton?.addEventListener('click', () => {
  openRoleEditor('new')
})

createRoleButton?.addEventListener('click', () => {
  void createRole()
})

saveRoleButton?.addEventListener('click', () => {
  void saveRoleBindings()
})

closeRoleEditorButton?.addEventListener('click', () => {
  closeRoleEditor()
})

closeModelEditorButton?.addEventListener('click', () => {
  closeModelEditor()
})

closeLive2dEditorButton?.addEventListener('click', () => {
  if (live2dEditor) {
    live2dEditor.hidden = true
  }
})

closeTtsEditorButton?.addEventListener('click', () => {
  if (ttsEditor) {
    ttsEditor.hidden = true
  }
})

newLive2dButton?.addEventListener('click', () => {
  showLive2dCreateHelp()
})

newTtsButton?.addEventListener('click', () => {
  showTtsCreateHelp()
})

refreshLive2dButton?.addEventListener('click', () => {
  void loadLive2dModels()
})

refreshTtsButton?.addEventListener('click', () => {
  renderTtsVoices()
})

async function loadCurrentSessionRoleLabel(): Promise<void> {
  try {
    const sessions = await loadSessionLibrary()
    const activeSession = sessions.find((session) => session.id === SESSION_ID)
    if (activeSession?.roleName) {
      setCurrentRoleLabel(activeSession.roleName)
    }
  }
  catch {
    setCurrentRoleLabel('Amadeus')
  }
}

async function loadRuntimeConfig(): Promise<void> {
  setConfigStatus(apiConfigStatus, 'Loading')
  setConfigStatus(runtimeConfigStatus, 'Loading')
  setConfigStatus(roleSessionStatus, 'Loading')
  try {
    const [response] = await Promise.all([
      fetch(`${AGENT_HTTP_URL}/runtime/config`, { method: 'GET' }),
      loadRolesAndSessions(),
      loadLive2dModels(),
    ])
    const payload = await response.json() as RuntimeConfigPayload
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`)
    }

    renderRuntimeConfig(payload)
    renderTtsVoices()
    configLoaded = true
  }
  catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    setConfigStatus(apiConfigStatus, `Load failed: ${message}`)
    setConfigStatus(runtimeConfigStatus, `Load failed: ${message}`)
    setConfigStatus(roleSessionStatus, `Load failed: ${message}`)
  }
}

async function loadRolesAndSessions(): Promise<void> {
  const [rolesResponse, sessionsResponse] = await Promise.all([
    fetch(`${AGENT_HTTP_URL}/roles`, { method: 'GET' }),
    fetch(`${AGENT_HTTP_URL}/sessions`, { method: 'GET' }),
  ])
  const rolesPayload = await rolesResponse.json() as { ok?: boolean, roles?: RolePayload[], error?: string }
  const sessionsPayload = await sessionsResponse.json() as { ok?: boolean, sessions?: SessionPayload[], error?: string }
  if (!rolesResponse.ok || !rolesPayload.ok || !Array.isArray(rolesPayload.roles)) {
    throw new Error(rolesPayload.error || `HTTP ${rolesResponse.status}`)
  }
  if (!sessionsResponse.ok || !sessionsPayload.ok || !Array.isArray(sessionsPayload.sessions)) {
    throw new Error(sessionsPayload.error || `HTTP ${sessionsResponse.status}`)
  }
  cachedRoles = rolesPayload.roles
  cachedSessions = sessionsPayload.sessions
  renderSessionMenu()
  renderRoleOptions(cachedRoles)
  const activeSession = sessionsPayload.sessions.find((session) => session.id === SESSION_ID)
  const selectedRole = activeSession?.roleId || roleSelect?.value || rolesPayload.roles[0]?.id || ''
  if (roleSelect && selectedRole) {
    roleSelect.value = selectedRole
  }
  setConfigStatus(roleSessionStatus, `${rolesPayload.roles.length} roles`)
}

function renderRoleOptions(roles: RolePayload[]): void {
  if (!roleSelect) {
    return
  }
  const selected = roleSelect.value
  roleSelect.replaceChildren()
  for (const role of roles) {
    const option = document.createElement('option')
    option.value = role.id
    option.textContent = role.name
    roleSelect.append(option)
  }
  roleSelect.value = roles.some((role) => role.id === selected) ? selected : roles[0]?.id || ''
  renderRoleList()
  renderRoleBindings()
  syncCustomSelects()
}

function renderRoleList(): void {
  if (!roleList) {
    return
  }
  if (!cachedRoles.length) {
    roleList.textContent = 'No roles yet.'
    return
  }
  roleList.replaceChildren(...cachedRoles.map((role) => {
    const detail = [
      role.model ? `${role.provider || 'model'} · ${role.model}` : 'Global model',
      role.live2dModel ? `Live2D: ${role.live2dModel}` : 'Global Live2D',
      role.ttsVoice ? `TTS: ${role.ttsVoice}` : 'Global TTS',
    ].join(' / ')
    return resourceCard(role.name, detail, () => {
      if (roleSelect) {
        roleSelect.value = role.id
      }
      renderRoleBindings()
      openRoleEditor('edit')
    }, role.id === roleSelect?.value)
  }))
}

async function loadSessionLibrary(): Promise<SessionPayload[]> {
  const response = await fetch(`${AGENT_HTTP_URL}/sessions`, { method: 'GET' })
  const payload = await response.json() as { ok?: boolean, sessions?: SessionPayload[], error?: string }
  if (!response.ok || !payload.ok || !Array.isArray(payload.sessions)) {
    throw new Error(payload.error || `HTTP ${response.status}`)
  }
  cachedSessions = payload.sessions
  renderSessionMenu()
  return cachedSessions
}

async function loadSessionPlan(): Promise<void> {
  try {
    const response = await fetch(`${AGENT_HTTP_URL}/sessions/${encodeURIComponent(SESSION_ID)}/plan`, { method: 'GET' })
    const payload = await response.json() as SessionPlanResponse
    if (!response.ok || !payload.ok || !payload.plan) {
      throw new Error(payload.error || `HTTP ${response.status}`)
    }
    renderSessionPlan(payload.plan)
  }
  catch {
    renderSessionPlan()
  }
}

function handleRuntimePlanEvent(event: ServerRuntimeEvent): void {
  if (event.type !== 'task.plan.updated' || event.sessionId !== SESSION_ID) {
    return
  }
  renderSessionPlan(event.payload)
}

async function loadSessionTasks(): Promise<void> {
  try {
    const params = new URLSearchParams({ sessionId: SESSION_ID, activeOnly: 'true', limit: '20' })
    const response = await fetch(`${AGENT_HTTP_URL}/tasks?${params.toString()}`, { method: 'GET' })
    const payload = await response.json() as TasksResponse
    if (!response.ok || !payload.ok || !Array.isArray(payload.tasks)) {
      throw new Error(payload.error || `HTTP ${response.status}`)
    }
    renderSessionTasks(payload.tasks, payload.summary)
  }
  catch {
    renderSessionTasks([])
  }
}

function handleRuntimeTaskEvent(event: ServerRuntimeEvent): void {
  if (event.type !== 'task.updated' || event.sessionId !== SESSION_ID) {
    return
  }
  renderSessionTaskUpdate(event.payload)
}

function renderSessionPlan(plan?: TaskPlanPayload): void {
  if (!sessionPlanPanel || !sessionPlanSummary || !sessionPlanList) {
    return
  }

  const activeItems = (plan?.items ?? []).filter((item) => item.status === 'pending' || item.status === 'in_progress')
  if (!activeItems.length) {
    sessionPlanPanel.hidden = true
    sessionPlanList.replaceChildren()
    sessionPlanSummary.textContent = 'No active plan'
    return
  }

  sessionPlanPanel.hidden = false
  const inProgress = activeItems.filter((item) => item.status === 'in_progress').length
  const pending = activeItems.filter((item) => item.status === 'pending').length
  sessionPlanSummary.textContent = inProgress
    ? `${inProgress} in progress / ${pending} pending`
    : `${pending} pending`

  sessionPlanList.replaceChildren(...activeItems.map((item) => {
    const row = document.createElement('li')
    row.className = 'session-plan-item'
    row.dataset.status = item.status

    const marker = document.createElement('span')
    marker.className = 'session-plan-marker'
    marker.textContent = item.status === 'in_progress' ? '>' : ''
    marker.setAttribute('aria-hidden', 'true')

    const content = document.createElement('span')
    content.className = 'session-plan-content'
    content.textContent = item.content

    row.append(marker, content)
    return row
  }))
}

let cachedActiveTasks: TaskRecord[] = []

function renderSessionTaskUpdate(payload: TaskUpdatedPayload): void {
  const task = payload.task
  const activeStatuses = new Set<TaskRecord['status']>(['queued', 'running', 'blocked'])
  const nextTasks = cachedActiveTasks.filter((item) => item.id !== task.id)
  if (activeStatuses.has(task.status)) {
    nextTasks.unshift(task)
  }
  renderSessionTasks(nextTasks.slice(0, 20))
}

function renderSessionTasks(tasks: TaskRecord[], summary?: TaskSummary): void {
  if (!sessionTasksPanel || !sessionTasksSummary || !sessionTasksList) {
    return
  }

  const activeTasks = tasks.filter((task) => task.status === 'queued' || task.status === 'running' || task.status === 'blocked')
  cachedActiveTasks = activeTasks
  if (!activeTasks.length) {
    sessionTasksPanel.hidden = true
    sessionTasksList.replaceChildren()
    sessionTasksSummary.textContent = 'No active tasks'
    return
  }

  sessionTasksPanel.hidden = false
  const running = summary?.running ?? activeTasks.filter((task) => task.status === 'running').length
  const queued = summary?.queued ?? activeTasks.filter((task) => task.status === 'queued').length
  const blocked = summary?.blocked ?? activeTasks.filter((task) => task.status === 'blocked').length
  sessionTasksSummary.textContent = [
    running ? `${running} running` : '',
    queued ? `${queued} queued` : '',
    blocked ? `${blocked} blocked` : '',
  ].filter(Boolean).join(' / ') || `${activeTasks.length} active`

  sessionTasksList.replaceChildren(...activeTasks.map((task) => {
    const row = document.createElement('li')
    row.className = 'session-plan-item'
    row.dataset.status = task.status

    const marker = document.createElement('span')
    marker.className = 'session-plan-marker'
    marker.textContent = task.status === 'running' ? '>' : task.status === 'blocked' ? '!' : ''
    marker.setAttribute('aria-hidden', 'true')

    const content = document.createElement('span')
    content.className = 'session-plan-content'
    content.textContent = task.title

    row.append(marker, content)
    return row
  }))
}

function renderSessionMenu(): void {
  const activeSession = cachedSessions.find((session) => session.id === SESSION_ID)
  if (currentSessionLabel) {
    currentSessionLabel.textContent = activeSession?.title || 'Session'
  }
  if (activeSession && roleSelect) {
    roleSelect.value = activeSession.roleId
  }
  setCurrentRoleLabel(activeSession?.roleName || cachedRoles.find((role) => role.id === roleSelect?.value)?.name || 'Amadeus')
  if (!sessionMenuList) {
    return
  }
  if (!cachedSessions.length) {
    sessionMenuList.textContent = 'No sessions yet.'
    return
  }
  sessionMenuList.replaceChildren(...cachedSessions.map((session) => createSessionMenuItem(session)))
}

function createSessionMenuItem(session: SessionPayload): HTMLElement {
  const item = document.createElement('div')
  item.className = 'session-menu-item'
  item.dataset.active = String(session.id === SESSION_ID)

  const title = document.createElement('button')
  title.type = 'button'
  title.className = 'session-menu-title'
  title.textContent = session.title
  title.title = 'Click to switch. Double-click to rename.'
  title.addEventListener('click', () => {
    if (session.id !== SESSION_ID) {
      switchSession(session.id)
    }
  })
  title.addEventListener('dblclick', (event) => {
    event.preventDefault()
    startSessionRename(item, session)
  })

  const meta = document.createElement('span')
  meta.className = 'session-menu-meta'
  meta.textContent = `${session.roleName || 'Role'} · ${session.messageCount} messages`

  const deleteButton = document.createElement('button')
  deleteButton.type = 'button'
  deleteButton.className = 'session-delete-button'
  deleteButton.title = 'Delete session'
  deleteButton.setAttribute('aria-label', `Delete ${session.title}`)
  deleteButton.textContent = '×'
  deleteButton.addEventListener('click', (event) => {
    event.stopPropagation()
    void deleteSession(session.id)
  })

  const text = document.createElement('div')
  text.className = 'session-menu-text'
  text.append(title, meta)
  item.append(text, deleteButton)
  return item
}

function startSessionRename(item: HTMLElement, session: SessionPayload): void {
  const input = document.createElement('input')
  input.className = 'session-rename-input'
  input.value = session.title
  input.maxLength = 160
  const commit = (): void => {
    const nextTitle = input.value.trim()
    if (!nextTitle || nextTitle === session.title) {
      renderSessionMenu()
      return
    }
    void renameSession(session.id, nextTitle)
  }
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      input.blur()
    }
    if (event.key === 'Escape') {
      renderSessionMenu()
    }
  })
  input.addEventListener('blur', commit, { once: true })
  item.replaceChildren(input)
  input.focus()
  input.select()
}

function toggleSessionMenu(): void {
  if (!sessionMenu || !sessionMenuButton) {
    return
  }
  const open = sessionMenu.hidden
  sessionMenu.hidden = !open
  sessionMenuButton.setAttribute('aria-expanded', String(open))
  if (open) {
    void loadSessionLibrary()
  }
}

function closeSessionMenu(): void {
  if (sessionMenu) {
    sessionMenu.hidden = true
  }
  sessionMenuButton?.setAttribute('aria-expanded', 'false')
}

function activeSession(): SessionPayload | undefined {
  return cachedSessions.find((session) => session.id === SESSION_ID)
}

function currentRoleIdForNewSession(): string {
  return activeSession()?.roleId || roleSelect?.value || cachedRoles[0]?.id || 'amadeus'
}

function renderSessionOptions(sessions: SessionPayload[]): void {
  cachedSessions = sessions
  renderSessionMenu()
}

async function createRole(): Promise<void> {
  const name = newRoleNameInput?.value.trim() || ''
  if (!name) {
    setConfigStatus(roleSessionStatus, 'Role name required')
    return
  }
  setConfigStatus(roleSessionStatus, 'Creating role')
  const response = await fetch(`${AGENT_HTTP_URL}/roles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      description: roleDescriptionInput?.value.trim() || '',
      persona: newRolePersonaInput?.value.trim() || '',
      style: roleStyleInput?.value.trim() || '',
      provider: roleModelSelect?.selectedOptions[0]?.dataset.provider || '',
      model: roleModelSelect?.value || '',
      live2dModel: roleLive2dSelect?.value || '',
      ttsVoice: roleTtsSelect?.value || '',
    }),
  })
  const payload = await response.json() as { ok?: boolean, session?: SessionPayload, error?: string }
  if (!response.ok || !payload.ok || !payload.session?.id) {
    setConfigStatus(roleSessionStatus, `Create failed: ${payload.error || `HTTP ${response.status}`}`)
    return
  }
  closeRoleEditor()
  switchSession(payload.session.id)
}

async function saveRoleBindings(): Promise<void> {
  const roleId = roleSelect?.value || ''
  if (!roleId) {
    setConfigStatus(roleSessionStatus, 'Select a role')
    return
  }
  setConfigStatus(roleSessionStatus, 'Saving role')
  const response = await fetch(`${AGENT_HTTP_URL}/roles/${encodeURIComponent(roleId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: newRoleNameInput?.value.trim() || undefined,
      description: roleDescriptionInput?.value.trim() || '',
      persona: newRolePersonaInput?.value.trim() || '',
      style: roleStyleInput?.value.trim() || '',
      provider: roleModelSelect?.selectedOptions[0]?.dataset.provider || '',
      model: roleModelSelect?.value || '',
      live2dModel: roleLive2dSelect?.value || '',
      ttsVoice: roleTtsSelect?.value || '',
    }),
  })
  const payload = await response.json() as { ok?: boolean, role?: RolePayload, error?: string }
  if (!response.ok || !payload.ok || !payload.role) {
    setConfigStatus(roleSessionStatus, `Save failed: ${payload.error || `HTTP ${response.status}`}`)
    return
  }
  cachedRoles = cachedRoles.map((role) => role.id === payload.role?.id ? payload.role : role)
  setCurrentRoleLabel(payload.role.name)
  renderRoleOptions(cachedRoles)
  renderRoleBindings()
  setConfigStatus(roleSessionStatus, 'Role saved')
}

async function createSession(): Promise<void> {
  const roleId = currentRoleIdForNewSession()
  if (!roleId) {
    setConfigStatus(roleSessionStatus, 'Select a role')
    return
  }
  const response = await fetch(`${AGENT_HTTP_URL}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ roleId }),
  })
  const payload = await response.json() as { ok?: boolean, session?: SessionPayload, error?: string }
  if (!response.ok || !payload.ok || !payload.session?.id) {
    setConfigStatus(roleSessionStatus, `Create failed: ${payload.error || `HTTP ${response.status}`}`)
    return
  }
  closeSessionMenu()
  switchSession(payload.session.id)
}

async function renameSession(sessionId: string, title: string): Promise<void> {
  if (!sessionId) {
    setConfigStatus(roleSessionStatus, 'Select a session')
    return
  }
  if (!title) {
    setConfigStatus(roleSessionStatus, 'Session name required')
    return
  }
  setConfigStatus(roleSessionStatus, 'Renaming session')
  const response = await fetch(`${AGENT_HTTP_URL}/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  const payload = await response.json() as { ok?: boolean, session?: SessionPayload, error?: string }
  if (!response.ok || !payload.ok || !payload.session) {
    setConfigStatus(roleSessionStatus, `Rename failed: ${payload.error || `HTTP ${response.status}`}`)
    return
  }
  cachedSessions = cachedSessions.map((session) => session.id === payload.session?.id ? payload.session : session)
  renderSessionMenu()
  setConfigStatus(roleSessionStatus, 'Session renamed')
}

async function deleteSession(sessionId: string): Promise<void> {
  const response = await fetch(`${AGENT_HTTP_URL}/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
  const payload = await response.json() as { ok?: boolean, error?: string }
  if (!response.ok || !payload.ok) {
    setConfigStatus(roleSessionStatus, `Delete failed: ${payload.error || `HTTP ${response.status}`}`)
    return
  }
  const sessions = await loadSessionLibrary()
  if (sessionId !== SESSION_ID) {
    return
  }
  const nextSession = sessions.find((session) => session.id !== sessionId)
  if (nextSession) {
    switchSession(nextSession.id)
    return
  }
  await createSession()
}

function switchSession(sessionId: string): void {
  const nextUrl = new URL(window.location.href)
  nextUrl.searchParams.set('sessionId', sessionId)
  window.location.href = nextUrl.toString()
}

async function loadLive2dModels(): Promise<void> {
  try {
    const response = await fetch(`${AGENT_HTTP_URL}/live2d/models`, { method: 'GET' })
    const payload = await response.json() as { ok?: boolean, models?: Array<{ id: string, label?: string, active?: boolean }>, error?: string }
    if (!response.ok || !payload.ok || !Array.isArray(payload.models)) {
      throw new Error(payload.error || `HTTP ${response.status}`)
    }
    cachedLive2dModels = payload.models
    renderLive2dModels()
    renderRoleBindings()
  }
  catch (error) {
    if (live2dModelList) {
      live2dModelList.textContent = `Load failed: ${error instanceof Error ? error.message : String(error)}`
    }
  }
}

function renderLive2dModels(): void {
  if (roleLive2dSelect) {
    roleLive2dSelect.replaceChildren(new Option('Use global Live2D', ''))
    for (const model of cachedLive2dModels) {
      roleLive2dSelect.append(new Option(model.label || model.id, model.id))
    }
  }
  if (live2dModelList) {
    live2dModelList.replaceChildren(...cachedLive2dModels.map((model) => resourceCard(
      model.label || model.id,
      model.active ? 'Active global model' : 'Available local model',
      () => {
        showLive2dDetail(model)
      },
      model.active,
    )))
  }
  syncCustomSelects()
}

function renderTtsVoices(): void {
  cachedVoices = window.speechSynthesis?.getVoices?.() || []
  if (roleTtsSelect) {
    roleTtsSelect.replaceChildren(new Option('Use global TTS voice', ''))
    for (const voice of cachedVoices) {
      roleTtsSelect.append(new Option(`${voice.name} · ${voice.lang}`, voice.name))
    }
  }
  if (ttsVoiceList) {
    ttsVoiceList.replaceChildren(...cachedVoices.map((voice) => resourceCard(
      voice.name,
      voice.lang,
      () => {
        showTtsDetail(voice)
      },
      voice.name === roleTtsSelect?.value,
    )))
  }
  renderRoleBindings()
  syncCustomSelects()
}

window.speechSynthesis?.addEventListener?.('voiceschanged', renderTtsVoices)

function renderRuntimeConfig(payload: RuntimeConfigPayload): void {
  renderProviderOptions(payload)
  const activeProvider = cachedProviders?.find((provider) => provider.id === payload.api?.provider)
  populateModelEditor(activeProvider
    ? { ...activeProvider, defaultModel: payload.api?.model || activeProvider.defaultModel, baseUrl: payload.api?.baseUrl || activeProvider.baseUrl }
    : {
      id: payload.api?.provider || '',
      label: payload.api?.providerLabel || payload.api?.provider || '',
      envVar: payload.api?.envVar || 'API_KEY',
      baseUrl: payload.api?.baseUrl || '',
      defaultModel: payload.api?.model || '',
      requiresApiKey: payload.api?.requiresApiKey !== false,
      supportsStreaming: true,
    })
  if (apiKeyInput) {
    apiKeyInput.value = ''
    apiKeyInput.placeholder = payload.api?.requiresApiKey === false
      ? 'No API key required for this provider'
      : payload.api?.apiKeyConfigured
      ? `${payload.api.envVar || 'API key'} configured: ${payload.api.apiKeyPreview || '********'}`
      : `Paste ${payload.api?.envVar || 'API'} key`
  }
  setConfigStatus(apiConfigStatus, payload.api?.requiresApiKey === false
    ? `${payload.api.providerLabel || payload.api.provider || 'Provider'} ready`
    : payload.api?.apiKeyConfigured
    ? `${payload.api.providerLabel || payload.api.provider || 'Provider'} ready`
    : `${payload.api?.providerLabel || payload.api?.provider || 'Provider'} key missing`)

  setNumberInput(configContextMaxTokens, payload.runtime?.context?.maxTokens)
  setNumberInput(configCompactionRatio, payload.runtime?.context?.compactionTriggerRatio)
  setNumberInput(configMemoryItemLimit, payload.runtime?.context?.memoryItemLimit)
  setNumberInput(configSummaryTrigger, payload.runtime?.summary?.triggerMessageCount)
  setNumberInput(configReviewTrigger, payload.runtime?.memoryReview?.triggerMessageCount)
  setNumberInput(configLive2dScale, payload.runtime?.desktop?.companionLive2dScale)
  setNumberInput(configLive2dOffsetX, payload.runtime?.desktop?.companionLive2dOffsetX)
  setNumberInput(configLive2dOffsetY, payload.runtime?.desktop?.companionLive2dOffsetY)
  setConfigStatus(runtimeConfigStatus, 'Loaded')
}

async function saveApiConfig(): Promise<void> {
  setConfigStatus(apiConfigStatus, 'Saving')
  const provider = apiProviderIdInput?.value.trim() || apiProviderSelect?.value || ''
  const apiKey = apiKeyInput?.value.trim() || ''
  const baseUrl = apiBaseUrlInput?.value.trim() || ''
  const model = apiModelInput?.value.trim() || ''
  const api = {
    ...(provider ? { provider } : {}),
    ...(apiProviderLabelInput?.value.trim() ? { label: apiProviderLabelInput.value.trim() } : {}),
    ...(baseUrl ? { baseUrl } : {}),
    ...(model ? { model } : {}),
    ...(apiEnvVarInput?.value.trim() ? { envVar: apiEnvVarInput.value.trim() } : {}),
    requiresApiKey: Boolean(apiRequiresKeyInput?.checked),
    streaming: Boolean(apiStreamingInput?.checked),
    ...(apiKey ? { apiKey } : {}),
  }
  try {
    const payload = await postRuntimeConfig({ api })
    renderRuntimeConfig(payload)
    closeModelEditor()
    setConfigStatus(apiConfigStatus, 'Saved')
  }
  catch (error) {
    setConfigStatus(apiConfigStatus, `Save failed: ${error instanceof Error ? error.message : String(error)}`)
  }
}

async function saveRuntimeConfig(): Promise<void> {
  setConfigStatus(runtimeConfigStatus, 'Saving')
  const runtime = {
    context: {
      maxTokens: readNumberInput(configContextMaxTokens),
      compactionTriggerRatio: readNumberInput(configCompactionRatio),
      memoryItemLimit: readNumberInput(configMemoryItemLimit),
    },
    summary: {
      triggerMessageCount: readNumberInput(configSummaryTrigger),
    },
    memoryReview: {
      triggerMessageCount: readNumberInput(configReviewTrigger),
    },
    desktop: {
      companionLive2dScale: readNumberInput(configLive2dScale),
      companionLive2dOffsetX: readNumberInput(configLive2dOffsetX),
      companionLive2dOffsetY: readNumberInput(configLive2dOffsetY),
    },
  }
  try {
    const payload = await postRuntimeConfig({ runtime })
    renderRuntimeConfig(payload)
    setConfigStatus(runtimeConfigStatus, 'Saved and reloaded')
  }
  catch (error) {
    setConfigStatus(runtimeConfigStatus, `Save failed: ${error instanceof Error ? error.message : String(error)}`)
  }
}

async function postRuntimeConfig(body: unknown): Promise<RuntimeConfigPayload> {
  const response = await fetch(`${AGENT_HTTP_URL}/runtime/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await response.json() as RuntimeConfigPayload
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`)
  }
  return payload
}

function setNumberInput(input: HTMLInputElement | null, value: number | undefined): void {
  if (input && typeof value === 'number') {
    input.value = String(value)
  }
}

function renderProviderOptions(payload: RuntimeConfigPayload): void {
  if (!apiProviderSelect || !Array.isArray(payload.providers)) {
    return
  }
  cachedProviders = payload.providers
  apiProviderSelect.replaceChildren()
  for (const provider of payload.providers) {
    const option = document.createElement('option')
    option.value = provider.id
    option.textContent = provider.label
    option.dataset.baseUrl = provider.baseUrl
    option.dataset.defaultModel = provider.defaultModel
    option.dataset.envVar = provider.envVar
    option.dataset.requiresApiKey = String(provider.requiresApiKey)
    option.dataset.supportsStreaming = String(provider.supportsStreaming)
    apiProviderSelect.append(option)
  }
  apiProviderSelect.value = payload.api?.provider || payload.providers[0]?.id || ''
  renderModelProfileList(payload.api?.provider || '')
  renderRoleModelOptions()
  renderRoleBindings()
  syncCustomSelects()
}

function renderModelProfileList(activeProviderId: string): void {
  if (!modelProfileList || !cachedProviders) {
    return
  }
  modelProfileList.replaceChildren(...cachedProviders.map((provider) => resourceCard(
    provider.label,
    `${provider.id} / ${provider.defaultModel}`,
    () => {
      if (apiProviderSelect) {
        apiProviderSelect.value = provider.id
      }
      populateModelEditor(provider)
      openModelEditor('edit')
    },
    provider.id === activeProviderId,
  )))
}

function findSelectedProvider(): ProviderPayload | undefined {
  const option = apiProviderSelect?.selectedOptions[0]
  if (!option) {
    return undefined
  }
  return {
    id: option.value,
    label: option.textContent || option.value,
    envVar: option.dataset.envVar || 'API_KEY',
    baseUrl: option.dataset.baseUrl || '',
    defaultModel: option.dataset.defaultModel || '',
    requiresApiKey: option.dataset.requiresApiKey !== 'false',
    supportsStreaming: option.dataset.supportsStreaming !== 'false',
  }
}

function populateModelEditor(provider: ProviderPayload): void {
  if (apiProviderIdInput) {
    apiProviderIdInput.value = provider.id
  }
  if (apiProviderLabelInput) {
    apiProviderLabelInput.value = provider.label
  }
  if (apiBaseUrlInput) {
    apiBaseUrlInput.value = provider.baseUrl
  }
  if (apiModelInput) {
    apiModelInput.value = provider.defaultModel
  }
  if (apiEnvVarInput) {
    apiEnvVarInput.value = provider.envVar
  }
  if (apiRequiresKeyInput) {
    apiRequiresKeyInput.checked = provider.requiresApiKey
  }
  if (apiStreamingInput) {
    apiStreamingInput.checked = provider.supportsStreaming
  }
  if (apiKeyInput) {
    apiKeyInput.value = ''
    apiKeyInput.placeholder = provider.requiresApiKey
      ? `${provider.envVar} API key`
      : 'No API key required for this provider'
  }
}

function uniqueProviderId(prefix: string): string {
  const existing = new Set((cachedProviders || []).map((provider) => provider.id))
  let index = 1
  let candidate = prefix
  while (existing.has(candidate)) {
    index += 1
    candidate = `${prefix}_${index}`
  }
  return candidate
}

function readNumberInput(input: HTMLInputElement | null): number | undefined {
  if (!input || input.value.trim() === '') {
    return undefined
  }
  return Number(input.value)
}

function setConfigStatus(element: HTMLElement | null, text: string): void {
  if (element) {
    element.textContent = text
  }
}

function openRoleEditor(mode: 'edit' | 'new'): void {
  if (!roleSessionForm) {
    return
  }
  roleSessionForm.hidden = false
  if (roleEditorTitle) {
    roleEditorTitle.textContent = mode === 'new' ? 'New Role' : 'Edit Role'
  }
  if (saveRoleButton) {
    saveRoleButton.hidden = mode === 'new'
  }
  if (createRoleButton) {
    createRoleButton.hidden = mode !== 'new'
  }
  if (mode === 'new') {
    clearRoleEditor()
  }
  else {
    renderRoleBindings()
  }
  syncCustomSelects()
}

function closeRoleEditor(): void {
  if (roleSessionForm) {
    roleSessionForm.hidden = true
  }
}

function clearRoleEditor(): void {
  if (newRoleNameInput) {
    newRoleNameInput.value = ''
  }
  if (roleDescriptionInput) {
    roleDescriptionInput.value = ''
  }
  if (newRolePersonaInput) {
    newRolePersonaInput.value = ''
  }
  if (roleStyleInput) {
    roleStyleInput.value = ''
  }
  if (roleModelSelect) {
    roleModelSelect.value = ''
  }
  if (roleLive2dSelect) {
    roleLive2dSelect.value = ''
  }
  if (roleTtsSelect) {
    roleTtsSelect.value = ''
  }
}

function openModelEditor(mode: 'edit' | 'new'): void {
  if (!apiConfigForm) {
    return
  }
  apiConfigForm.hidden = false
  if (modelEditorTitle) {
    modelEditorTitle.textContent = mode === 'new' ? 'New Model' : 'Edit Model'
  }
  syncCustomSelects()
}

function closeModelEditor(): void {
  if (apiConfigForm) {
    apiConfigForm.hidden = true
  }
}

function closeResourceEditors(): void {
  closeRoleEditor()
  closeModelEditor()
  if (live2dEditor) {
    live2dEditor.hidden = true
  }
  if (ttsEditor) {
    ttsEditor.hidden = true
  }
  closeOtherCustomSelects(document.createElement('div'))
}

function showLive2dDetail(model: { id: string, label?: string, active?: boolean }): void {
  if (live2dEditorTitle) {
    live2dEditorTitle.textContent = model.label || model.id
  }
  if (live2dEditorDetail) {
    live2dEditorDetail.textContent = `${model.active ? '当前全局模型。' : '本地可用模型。'}ID: ${model.id}。如果要给某个 Role 使用它，请进入 Role 的 Edit 面板进行绑定。`
  }
  if (live2dEditor) {
    live2dEditor.hidden = false
  }
}

function showLive2dCreateHelp(): void {
  if (live2dEditorTitle) {
    live2dEditorTitle.textContent = 'New Live2D'
  }
  if (live2dEditorDetail) {
    live2dEditorDetail.textContent = 'Live2D 是文件型资源。把 model3.json 及相关贴图/动作文件放到 models/live2d 下的新目录里，然后点击 Refresh Live2D，这里就会出现新的模型气泡。'
  }
  if (live2dEditor) {
    live2dEditor.hidden = false
  }
}

function showTtsDetail(voice: SpeechSynthesisVoice): void {
  if (ttsEditorTitle) {
    ttsEditorTitle.textContent = voice.name
  }
  if (ttsEditorDetail) {
    ttsEditorDetail.textContent = `Language: ${voice.lang || 'unknown'}。${voice.default ? '系统默认 voice。' : '系统可用 voice。'}如果要给某个 Role 使用它，请进入 Role 的 Edit 面板进行绑定。`
  }
  if (ttsEditor) {
    ttsEditor.hidden = false
  }
}

function showTtsCreateHelp(): void {
  if (ttsEditorTitle) {
    ttsEditorTitle.textContent = 'New TTS'
  }
  if (ttsEditorDetail) {
    ttsEditorDetail.textContent = '当前 TTS 列表来自系统 / 浏览器 voice。安装新的系统 voice，或后续接入后端 TTS provider 后，再点击 Refresh TTS，这里就会出现新的声音气泡。'
  }
  if (ttsEditor) {
    ttsEditor.hidden = false
  }
}

function enhanceSettingsSelects(): void {
  for (const select of document.querySelectorAll<HTMLSelectElement>('.config-form select')) {
    enhanceSelect(select)
  }
}

function enhanceSelect(select: HTMLSelectElement): void {
  if (customSelects.has(select)) {
    syncCustomSelect(select)
    return
  }

  const root = document.createElement('div')
  root.className = 'amadeus-select'
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'amadeus-select-button'
  button.setAttribute('aria-haspopup', 'listbox')
  button.setAttribute('aria-expanded', 'false')
  const value = document.createElement('span')
  value.className = 'amadeus-select-value'
  const caret = document.createElement('span')
  caret.className = 'amadeus-select-caret'
  caret.textContent = '⌄'
  const list = document.createElement('div')
  list.className = 'amadeus-select-list'
  list.setAttribute('role', 'listbox')

  button.append(value, caret)
  root.append(button, list)
  select.classList.add('native-select-hidden')
  select.insertAdjacentElement('afterend', root)

  customSelects.set(select, { root, button, value, list })
  button.addEventListener('click', (event) => {
    event.stopPropagation()
    closeOtherCustomSelects(root)
    const open = root.dataset.open !== 'true'
    root.dataset.open = String(open)
    button.setAttribute('aria-expanded', String(open))
  })
  document.addEventListener('click', (event) => {
    if (!root.contains(event.target as Node)) {
      root.dataset.open = 'false'
      button.setAttribute('aria-expanded', 'false')
    }
  })
  select.addEventListener('change', () => syncCustomSelect(select))
  new MutationObserver(() => syncCustomSelect(select)).observe(select, { childList: true, subtree: true })
  syncCustomSelect(select)
}

function closeOtherCustomSelects(activeRoot: HTMLDivElement): void {
  for (const root of document.querySelectorAll<HTMLElement>('.amadeus-select[data-open="true"]')) {
    if (root !== activeRoot) {
      root.dataset.open = 'false'
      root.querySelector<HTMLButtonElement>('.amadeus-select-button')?.setAttribute('aria-expanded', 'false')
    }
  }
}

function syncCustomSelects(): void {
  for (const select of document.querySelectorAll<HTMLSelectElement>('.config-form select')) {
    syncCustomSelect(select)
  }
}

function syncCustomSelect(select: HTMLSelectElement): void {
  const customSelect = customSelects.get(select)
  if (!customSelect) {
    return
  }
  const options = Array.from(select.options)
  const selectedOption = select.selectedOptions[0] || options[select.selectedIndex] || options[0]
  customSelect.value.textContent = selectedOption?.textContent || 'Select'
  customSelect.list.replaceChildren(...options.map((option, index) => {
    const item = document.createElement('button')
    item.type = 'button'
    item.className = 'amadeus-select-option'
    item.dataset.active = String(option === selectedOption)
    item.setAttribute('role', 'option')
    item.setAttribute('aria-selected', String(option === selectedOption))
    item.textContent = option.textContent || option.value
    item.addEventListener('click', (event) => {
      event.stopPropagation()
      select.selectedIndex = index
      customSelect.root.dataset.open = 'false'
      customSelect.button.setAttribute('aria-expanded', 'false')
      select.dispatchEvent(new Event('change', { bubbles: true }))
      syncCustomSelect(select)
    })
    return item
  }))
}

function setFullscreenButtonState(isFullscreen: boolean): void {
  if (!fullscreenButton) {
    return
  }
  fullscreenButton.dataset.fullscreen = String(isFullscreen)
  fullscreenButton.setAttribute('aria-pressed', String(isFullscreen))
  fullscreenButton.title = isFullscreen ? 'Exit fullscreen' : 'Fullscreen'
}

function activateSettingsSection(target: string): void {
  closeResourceEditors()
  for (const button of settingsNavButtons) {
    button.classList.toggle('active', button.dataset.settingsTarget === target)
  }
  for (const section of settingsSections) {
    const active = section.dataset.settingsSection === target
    section.classList.toggle('active', active)
    section.hidden = !active
  }
}

function setCurrentRoleLabel(roleName: string): void {
  const providerLabel = query<HTMLElement>('#provider-label')
  if (!providerLabel) {
    return
  }
  providerLabel.dataset.roleLabel = roleName
  providerLabel.textContent = roleName
}

function renderRoleModelOptions(): void {
  if (!roleModelSelect) {
    return
  }
  roleModelSelect.replaceChildren(new Option('Use global model', ''))
  for (const provider of cachedProviders || []) {
    const option = new Option(`${provider.label} · ${provider.defaultModel}`, provider.defaultModel)
    option.dataset.provider = provider.id
    roleModelSelect.append(option)
  }
}

function renderRoleBindings(): void {
  const role = cachedRoles.find((item) => item.id === roleSelect?.value)
  if (!role) {
    return
  }
  if (newRoleNameInput) {
    newRoleNameInput.value = role.name || ''
  }
  if (roleDescriptionInput) {
    roleDescriptionInput.value = role.description || ''
  }
  if (newRolePersonaInput) {
    newRolePersonaInput.value = role.persona || ''
  }
  if (roleStyleInput) {
    roleStyleInput.value = role.style || ''
  }
  if (roleModelSelect) {
    const selected = Array.from(roleModelSelect.options).find((option) => option.value === role.model && option.dataset.provider === role.provider)
    roleModelSelect.value = selected?.value || ''
  }
  if (roleLive2dSelect) {
    roleLive2dSelect.value = role.live2dModel || ''
  }
  if (roleTtsSelect) {
    roleTtsSelect.value = role.ttsVoice || ''
  }
  renderRoleList()
  setCurrentRoleLabel(role.name)
  syncCustomSelects()
}

function resourceCard(label: string, detail: string, onClick?: () => void, active = false): HTMLElement {
  const item = document.createElement('div')
  item.className = 'resource-card'
  item.dataset.active = String(active)
  const content = document.createElement('div')
  content.className = 'resource-card-content'
  const title = document.createElement('strong')
  title.textContent = label
  const meta = document.createElement('span')
  meta.textContent = detail
  content.append(title, meta)
  item.append(content)
  if (onClick) {
    const action = document.createElement('button')
    action.type = 'button'
    action.className = 'resource-edit-button'
    action.textContent = 'Edit'
    action.addEventListener('click', onClick)
    item.append(action)
  }
  return item
}

const runtimeUi = new RuntimeUiController({
  elements: {
    statusElement: query('#stage-status'),
    chatForm: query('#chat-form'),
    chatInput: query('#chat-input'),
    chatLog: query('#chat-log'),
    skillsStatus: query('#skills-status'),
    skillsSearchInput: query('#skills-search-input'),
    skillsList: query('#skills-list'),
    skillsRefreshButton: query('#skills-refresh-button'),
    skillDetailTitle: query('#skill-detail-title'),
    skillDetailBody: query('#skill-detail-body'),
    voiceButton: query('#voice-button'),
    providerLabel: query('#provider-label'),
    connectionLabel: query('#connection-label'),
    statusDot: query('#status-dot'),
    memoryStatus: query('#memory-status'),
    toolStatus: query('#tool-status'),
    skillStatus: query('#skill-status'),
    toolConfigStatus: query('#tool-config-status'),
    toolPermission: query('#tool-permission'),
    toolPermissionText: query('#tool-permission-text'),
    toolAllowButton: query('#tool-allow-button'),
    toolDenyButton: query('#tool-deny-button'),
    memoryReviewStatus: query('#memory-review-status'),
    memoryReviewRunButton: query('#memory-review-run-button'),
    memoryReviewList: query('#memory-review-list'),
    voiceStatus: query('#voice-status'),
    resetSessionButton: query('#reset-session-button'),
  },
  wsUrl: wsUrlForSurface(BASE_AGENT_WS_URL, 'main-ui', SESSION_ID),
  skillsUrl: `${AGENT_HTTP_URL}/skills/list`,
  modelLabel: 'Runtime',
  createSocket: (url) => new WebSocket(url),
  createAudio: () => new MockAudio(),
  createUtterance: (text) => new SpeechSynthesisUtterance(text),
  randomUUID: () => crypto.randomUUID(),
  setTimeout: (handler, timeout) => window.setTimeout(handler, timeout),
  clearTimeout: (id) => window.clearTimeout(id),
  fetchImpl: fetch.bind(window),
  storage: DISABLE_SKILL_PERSISTENCE ? undefined : window.localStorage,
  speechSynthesis: window.speechSynthesis,
  onServerEvent(event) {
    handleRuntimePlanEvent(event)
    handleRuntimeTaskEvent(event)
  },
})

async function bootstrapMainUi(): Promise<void> {
  enhanceSettingsSelects()
  setCurrentRoleLabel('Amadeus')
  await loadCurrentSessionRoleLabel()
  await Promise.all([
    loadSessionPlan(),
    loadSessionTasks(),
  ])
  runtimeUi.bindControls()
  runtimeUi.connectAgentRuntime()
}

void bootstrapMainUi()
