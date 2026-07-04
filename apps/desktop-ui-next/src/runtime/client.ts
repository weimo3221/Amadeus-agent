import type {
  ScheduledJobUpdatedPayload,
  ServerRuntimeEvent,
  TaskPlanPayload,
  TaskUpdatedPayload,
} from '@amadeus-agent/amadeus/events'
import { BASE_AGENT_WS_URL, SESSION_ID, wsUrlForSurface } from './config'

export type ConnectionPhase = 'connecting' | 'connected' | 'disconnected'

export interface ToolPermissionPrompt {
  requestId: string
  toolName: string
  displayName: string
  reason: string
}

export interface RuntimeClientHandlers {
  onConnectionChange?: (phase: ConnectionPhase) => void
  onSessionId?: (sessionId: string) => void
  onAssistantDelta?: (text: string) => void
  onAssistantMessage?: (text: string) => void
  onToolStarted?: (toolName: string, displayName: string) => void
  onToolFinished?: (toolName: string, ok: boolean) => void
  onToolPermissionRequest?: (prompt: ToolPermissionPrompt) => void
  onToolPermissionResolved?: () => void
  onPlanUpdated?: (plan: TaskPlanPayload) => void
  onTaskUpdated?: (payload: TaskUpdatedPayload) => void
  onScheduledJobUpdated?: (payload: ScheduledJobUpdatedPayload) => void
  onError?: (message: string) => void
}

const RECONNECT_DELAY_MS = 1800

export class AgentRuntimeClient {
  sessionId: string
  private socket: WebSocket | null = null
  private reconnectTimer: number | null = null
  private closedByUser = false

  constructor(private readonly handlers: RuntimeClientHandlers = {}, sessionId: string = SESSION_ID) {
    this.sessionId = sessionId
  }

  connect(): void {
    this.closedByUser = false
    this.openSocket()
  }

  disconnect(): void {
    this.closedByUser = true
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.socket?.close()
    this.socket = null
  }

  private openSocket(): void {
    this.handlers.onConnectionChange?.('connecting')
    const url = wsUrlForSurface(BASE_AGENT_WS_URL, 'main-ui', this.sessionId)
    const socket = new WebSocket(url)
    this.socket = socket

    socket.addEventListener('open', () => {
      this.handlers.onConnectionChange?.('connected')
    })

    socket.addEventListener('message', (event) => {
      try {
        const parsed = JSON.parse(event.data as string) as ServerRuntimeEvent
        this.handleServerEvent(parsed)
      } catch {
        // ignore malformed frames
      }
    })

    socket.addEventListener('close', () => {
      this.handlers.onConnectionChange?.('disconnected')
      if (!this.closedByUser) {
        this.scheduleReconnect()
      }
    })

    socket.addEventListener('error', () => {
      this.handlers.onConnectionChange?.('disconnected')
    })
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.openSocket()
    }, RECONNECT_DELAY_MS)
  }

  private sendEvent<TPayload>(type: string, payload: TPayload): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return
    const event = {
      id: crypto.randomUUID(),
      type,
      sessionId: this.sessionId,
      timestamp: new Date().toISOString(),
      payload,
    }
    this.socket.send(JSON.stringify(event))
  }

  sendUserMessage(text: string, skills: string[] = []): void {
    this.sendEvent('user.message', {
      text,
      inputMode: 'text' as const,
      ...(skills.length ? { skills } : {}),
    })
  }

  respondToToolPermission(requestId: string, approved: boolean): void {
    this.sendEvent('tool.permission.response', { requestId, approved })
  }

  private handleServerEvent(event: ServerRuntimeEvent): void {
    switch (event.type) {
      case 'server.hello': {
        if (event.sessionId && event.sessionId !== this.sessionId) {
          this.sessionId = event.sessionId
        }
        this.handlers.onSessionId?.(this.sessionId)
        this.sendDesktopCapabilities()
        break
      }
      case 'assistant.delta':
        this.handlers.onAssistantDelta?.(event.payload.text)
        break
      case 'assistant.message':
        this.handlers.onAssistantMessage?.(event.payload.text)
        break
      case 'tool.started':
        this.handlers.onToolStarted?.(event.payload.toolName, event.payload.displayName)
        break
      case 'tool.finished':
        this.handlers.onToolFinished?.(event.payload.toolName, event.payload.ok)
        this.handlers.onToolPermissionResolved?.()
        break
      case 'tool.permission.request':
        this.handlers.onToolPermissionRequest?.({
          requestId: event.payload.requestId,
          toolName: event.payload.toolName,
          displayName: event.payload.displayName,
          reason: event.payload.reason,
        })
        break
      case 'task.plan.updated':
        this.handlers.onPlanUpdated?.(event.payload)
        break
      case 'task.updated':
        this.handlers.onTaskUpdated?.(event.payload)
        break
      case 'scheduled.updated':
        this.handlers.onScheduledJobUpdated?.(event.payload)
        break
      case 'error':
        this.handlers.onError?.(event.payload.message)
        break
      default:
        break
    }
  }

  private sendDesktopCapabilities(): void {
    this.sendEvent('desktop.capabilities', {
      desktop: { runtime: 'electron' as const, protocolVersion: 1 },
      live2d: { available: false, expressions: [], motions: [] },
      audio: { runtimeAudio: false, speechSynthesis: false, voiceCount: 0 },
    })
  }
}
