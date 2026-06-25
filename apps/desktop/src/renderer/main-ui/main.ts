import { RuntimeUiController, type RuntimeAudioLike } from '../runtime-ui'
import './styles.css'

const runtimeQuery = new URLSearchParams(window.location.search)
const AGENT_HTTP_URL = runtimeQuery.get('agentHttpUrl') || import.meta.env.VITE_AGENT_HTTP_URL || 'http://127.0.0.1:8788'
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

closeButton?.addEventListener('click', () => {
  void window.amadeus?.closeWindow()
})

minimizeButton?.addEventListener('click', () => {
  void window.amadeus?.minimizeWindow()
})

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
})

runtimeUi.bindControls()
runtimeUi.connectAgentRuntime()
