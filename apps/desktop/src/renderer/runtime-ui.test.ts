import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import type { RuntimeEvent, ServerRuntimeEvent } from '@amadeus-agent/amadeus/events'

import {
  RuntimeUiController,
  type RuntimeAudioLike,
  type RuntimeLive2DAdapter,
  type RuntimeSocketLike,
  type RuntimeUiElements,
} from './runtime-ui'

class FakeElement {
  textContent = ''
  hidden = false
  title = ''
  className = ''
  scrollTop = 0
  scrollHeight = 0
  dataset: Record<string, string> = {}
  children: FakeElement[] = []
  private listeners = new Map<string, Array<(event: any) => void>>()

  addEventListener(type: string, listener: (event: any) => void): void {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener)
    this.listeners.set(type, listeners)
  }

  dispatch(type: string, event: Record<string, unknown> = {}): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }

  click(): void {
    this.dispatch('click')
  }

  submit(): void {
    this.dispatch('submit', { preventDefault() {} })
  }

  append(child: FakeElement): void {
    this.children.push(child)
    this.scrollHeight = this.children.length
  }

  replaceChildren(): void {
    this.children = []
    this.scrollHeight = 0
  }
}

class FakeInputElement extends FakeElement {
  value = ''
}

class FakeDocument {
  createElement(_tagName: string): FakeElement {
    return new FakeElement()
  }
}

class FakeSocket implements RuntimeSocketLike {
  readyState = 1
  readonly sent: Array<RuntimeEvent<string, unknown>> = []
  private listeners = new Map<string, Array<(event: any) => void>>()

  send(data: string): void {
    this.sent.push(JSON.parse(data) as RuntimeEvent<string, unknown>)
  }

  addEventListener(type: 'open' | 'message' | 'close' | 'error', listener: (event: any) => void): void {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener)
    this.listeners.set(type, listeners)
  }

  emit(type: 'open' | 'message' | 'close' | 'error', event: any = {}): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }

  emitServerEvent(event: ServerRuntimeEvent): void {
    this.emit('message', { data: JSON.stringify(event) })
  }
}

class FakeUtterance {
  lang = ''
  rate = 1
  pitch = 1
  volume = 1
  voice?: SpeechSynthesisVoice
  private listeners = new Map<string, Array<(event: any) => void>>()

  constructor(readonly text: string) {}

  addEventListener(type: string, listener: (event: any) => void): void {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener)
    this.listeners.set(type, listeners)
  }

  emit(type: string, event: Record<string, unknown> = {}): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }
}

class FakeSpeechSynthesis {
  paused = false
  speaking = false
  cancelCalls = 0
  resumeCalls = 0
  spoken: FakeUtterance[] = []

  cancel(): void {
    this.cancelCalls += 1
  }

  speak(utterance: SpeechSynthesisUtterance): void {
    this.spoken.push(utterance as unknown as FakeUtterance)
    this.speaking = true
  }

  resume(): void {
    this.resumeCalls += 1
  }

  getVoices(): SpeechSynthesisVoice[] {
    return [{ name: 'Test Voice', lang: 'en-US' } as SpeechSynthesisVoice]
  }

  addEventListener(_type: 'voiceschanged', _listener: () => void): void {}
}

class FakeAudio implements RuntimeAudioLike {
  playCalls = 0
  pauseCalls = 0
  private listeners = new Map<string, Array<() => void>>()

  constructor(readonly url: string) {}

  addEventListener(type: 'play' | 'ended' | 'error', listener: () => void): void {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener)
    this.listeners.set(type, listeners)
  }

  async play(): Promise<void> {
    this.playCalls += 1
    for (const listener of this.listeners.get('play') ?? []) {
      listener()
    }
  }

  pause(): void {
    this.pauseCalls += 1
  }

  emit(type: 'play' | 'ended' | 'error'): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener()
    }
  }
}

class FakeTimers {
  private nextId = 1
  private callbacks = new Map<number, () => void>()
  cleared: number[] = []

  setTimeout(handler: () => void, _timeout: number): number {
    const id = this.nextId++
    this.callbacks.set(id, handler)
    return id
  }

  clearTimeout(id: number): void {
    this.cleared.push(id)
    this.callbacks.delete(id)
  }

  runAll(): void {
    const callbacks = Array.from(this.callbacks.values())
    this.callbacks.clear()
    for (const callback of callbacks) {
      callback()
    }
  }
}

function makeEvent<TType extends ServerRuntimeEvent['type']>(
  type: TType,
  payload: Extract<ServerRuntimeEvent, { type: TType }>['payload'],
  sessionId = 'session-1',
): Extract<ServerRuntimeEvent, { type: TType }> {
  return {
    id: `${type}-event`,
    type,
    sessionId,
    timestamp: '2026-06-19T00:00:00.000Z',
    payload,
  } as Extract<ServerRuntimeEvent, { type: TType }>
}

function createHarness() {
  globalThis.document = new FakeDocument() as unknown as Document
  const elements = {
    statusElement: new FakeElement(),
    chatForm: new FakeElement(),
    chatInput: new FakeInputElement(),
    chatLog: new FakeElement(),
    voiceButton: new FakeElement(),
    providerLabel: new FakeElement(),
    connectionLabel: new FakeElement(),
    statusDot: new FakeElement(),
    memoryStatus: new FakeElement(),
    toolStatus: new FakeElement(),
    toolConfigStatus: new FakeElement(),
    toolPermission: new FakeElement(),
    toolPermissionText: new FakeElement(),
    toolAllowButton: new FakeElement(),
    toolDenyButton: new FakeElement(),
    memoryReviewStatus: new FakeElement(),
    memoryReviewRunButton: new FakeElement(),
    memoryReviewList: new FakeElement(),
    voiceStatus: new FakeElement(),
    resetSessionButton: new FakeElement(),
  }
  const timers = new FakeTimers()
  const socket = new FakeSocket()
  const speech = new FakeSpeechSynthesis()
  const audios: FakeAudio[] = []
  const live2dStats = {
    startRuntimeAudioLipsyncCalls: 0,
    startMouthLoopCalls: 0,
    stopMouthLoopCalls: 0,
    applyLipsyncCuesCalls: 0,
    lastLipsyncCueCount: 0,
    runtimeAudioLipsyncResult: false,
  }
  const live2d: RuntimeLive2DAdapter = {
    applyState: () => {},
    applyBehavior: () => {},
    applyLipsyncCues: (payload) => {
      live2dStats.applyLipsyncCuesCalls += 1
      live2dStats.lastLipsyncCueCount = payload.cues.length
    },
    startRuntimeAudioLipsync: () => {
      live2dStats.startRuntimeAudioLipsyncCalls += 1
      return live2dStats.runtimeAudioLipsyncResult
    },
    startMouthLoop: () => {
      live2dStats.startMouthLoopCalls += 1
    },
    stopMouthLoop: () => {
      live2dStats.stopMouthLoopCalls += 1
    },
    getCapabilities: () => ({
      available: true,
      modelId: 'hiyori-free',
      expressions: ['smile'],
      motions: ['Idle'],
    }),
  }
  const controller = new RuntimeUiController({
    elements: elements as unknown as RuntimeUiElements,
    wsUrl: 'ws://runtime/ws',
    modelLabel: 'initial-model',
    createSocket: () => socket,
    createAudio: (url) => {
      const audio = new FakeAudio(url)
      audios.push(audio)
      return audio
    },
    createUtterance: (text) => new FakeUtterance(text) as unknown as SpeechSynthesisUtterance,
    randomUUID: () => 'client-event-id',
    setTimeout: (handler, timeout) => timers.setTimeout(handler, timeout),
    clearTimeout: (id) => timers.clearTimeout(id),
    speechSynthesis: speech as unknown as FakeSpeechSynthesis,
    live2d,
  })

  controller.bindControls()
  return { controller, elements, socket, timers, speech, audios, live2dStats }
}

describe('Runtime UI controller', () => {
  it('connects to the runtime and renders server.hello diagnostics', () => {
    const { controller, elements, socket } = createHarness()

    controller.connectAgentRuntime()
    socket.emit('open')
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'deepseek-v4-flash',
      memoryMessages: 7,
      toolPermissions: [
        { name: 'get_current_time', displayName: 'Time', enabled: true, permission: 'allow' },
        { name: 'roll_dice', displayName: 'Dice', enabled: false, permission: 'ask' },
      ],
    }))

    assert.equal(elements.connectionLabel.textContent, 'Connected')
    assert.equal(elements.statusDot.dataset.connected, 'true')
    assert.equal(elements.providerLabel.textContent, 'deepseek-v4-flash')
    assert.equal(elements.memoryStatus.textContent, 'Memory: 7 messages')
    assert.equal(elements.toolConfigStatus.textContent, 'Tools: get_current_time allow, roll_dice off')
    assert.equal(socket.sent.at(-1)?.type, 'memory.review.list')
    assert.equal(socket.sent.at(-2)?.type, 'desktop.capabilities')
    assert.deepEqual(socket.sent.at(-2)?.payload, {
      desktop: {
        runtime: 'electron',
        protocolVersion: 1,
      },
      live2d: {
        available: true,
        modelId: 'hiyori-free',
        expressions: ['smile'],
        motions: ['Idle'],
      },
      audio: {
        runtimeAudio: true,
        speechSynthesis: true,
        voiceCount: 1,
      },
    })
  })

  it('renders memory review candidates and sends accept or reject actions', () => {
    const { controller, elements, socket } = createHarness()

    controller.connectAgentRuntime()
    socket.emit('open')
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'deepseek-v4-flash',
      memoryMessages: 0,
      toolPermissions: [],
    }))
    socket.emitServerEvent(makeEvent('memory.review.candidates', {
      status: 'pending',
      candidateCount: 1,
      candidates: [{
        candidateId: 42,
        sessionId: 'session-1',
        scope: 'project',
        content: 'Memory review candidates require human approval.',
        confidence: 0.92,
        reason: 'Project policy.',
        sourceMessageStartId: 1,
        sourceMessageEndId: 2,
        status: 'pending',
        memoryItemId: 0,
      }],
    }))

    assert.equal(elements.memoryReviewStatus.textContent, 'Memory review: 1 pending | last job: none')
    assert.equal(elements.memoryReviewList.children.length, 1)

    const candidate = elements.memoryReviewList.children[0]
    const actions = candidate.children[1]
    actions.children[0].click()
    actions.children[1].click()

    assert.equal(socket.sent.at(-2)?.type, 'memory.review.accept')
    assert.deepEqual(socket.sent.at(-2)?.payload, { candidateId: 42 })
    assert.equal(socket.sent.at(-1)?.type, 'memory.review.reject')
    assert.deepEqual(socket.sent.at(-1)?.payload, { candidateId: 42 })
  })

  it('runs manual memory review from the review panel', () => {
    const { controller, elements, socket } = createHarness()

    controller.connectAgentRuntime()
    socket.emit('open')
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'deepseek-v4-flash',
      memoryMessages: 0,
      toolPermissions: [],
    }))

    elements.memoryReviewRunButton.click()

    assert.equal(elements.memoryReviewStatus.textContent, 'Memory review running...')
    assert.equal(socket.sent.at(-1)?.type, 'memory.review.run')
    assert.deepEqual(socket.sent.at(-1)?.payload, { force: true })
  })

  it('renders assistant deltas and schedules speech fallback for assistant.message', () => {
    const { controller, elements, timers, speech } = createHarness()

    controller.handleServerEvent(makeEvent('assistant.delta', { text: 'hel' }))
    controller.handleServerEvent(makeEvent('assistant.delta', { text: 'lo' }))
    controller.handleServerEvent(makeEvent('assistant.message', { text: 'hello' }))

    assert.equal(elements.chatLog.children.length, 1)
    assert.equal(elements.chatLog.children[0].textContent, 'hello')
    assert.equal(speech.spoken.length, 0)

    timers.runAll()

    assert.equal(speech.spoken.length, 1)
    assert.equal(speech.spoken[0].text, 'hello')
  })

  it('shows permission prompts and sends Allow or Deny responses', () => {
    const { controller, elements, socket } = createHarness()
    controller.connectAgentRuntime()
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'model',
      memoryMessages: 0,
      toolPermissions: [],
    }))

    controller.handleServerEvent(makeEvent('tool.permission.request', {
      requestId: 'request-allow',
      toolName: 'search_files',
      displayName: 'Searching local files',
      reason: 'Allow file search?',
    }))
    elements.toolAllowButton.click()

    assert.equal(elements.toolPermission.hidden, true)
    assert.equal(elements.toolStatus.textContent, 'Tool permission approved')
    assert.deepEqual(socket.sent.at(-1)?.payload, {
      requestId: 'request-allow',
      approved: true,
    })

    controller.handleServerEvent(makeEvent('tool.permission.request', {
      requestId: 'request-deny',
      toolName: 'roll_dice',
      displayName: 'Rolling dice',
      reason: 'Allow dice?',
    }))
    elements.toolDenyButton.click()

    assert.equal(elements.toolPermission.hidden, true)
    assert.equal(elements.toolStatus.textContent, 'Tool permission denied')
    assert.deepEqual(socket.sent.at(-1)?.payload, {
      requestId: 'request-deny',
      approved: false,
    })
  })

  it('sends user messages over WebSocket from the chat form', () => {
    const { controller, elements, socket } = createHarness()
    controller.connectAgentRuntime()
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'model',
      memoryMessages: 0,
      toolPermissions: [],
    }))
    elements.chatInput.value = 'hello'

    elements.chatForm.submit()

    assert.equal(elements.chatLog.children[0].textContent, 'hello')
    assert.equal(elements.chatInput.value, '')
    assert.equal(socket.sent.at(-1)?.type, 'user.message')
    assert.deepEqual(socket.sent.at(-1)?.payload, {
      text: 'hello',
      inputMode: 'text',
    })
  })

  it('uses runtime audio and cancels speechSynthesis fallback after audio.tts-ready', () => {
    const { controller, elements, socket, timers, speech, audios, live2dStats } = createHarness()

    controller.connectAgentRuntime()
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'model',
      memoryMessages: 0,
      toolPermissions: [],
    }))
    controller.handleServerEvent(makeEvent('assistant.message', { text: 'hello with audio' }))
    controller.handleServerEvent(makeEvent('audio.tts-ready', {
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 1000,
    }))
    timers.runAll()

    assert.equal(audios.length, 1)
    assert.equal(audios[0].url, 'http://runtime/audio.wav')
    assert.equal(audios[0].playCalls, 1)
    assert.equal(speech.spoken.length, 0)
    assert.equal(speech.cancelCalls, 1)
    assert.equal(elements.voiceStatus.textContent, 'Playing runtime audio')
    assert.equal(live2dStats.startRuntimeAudioLipsyncCalls, 1)
    assert.equal(live2dStats.startMouthLoopCalls, 1)
    assert.equal(socket.sent.at(-1)?.type, 'audio.playback-started')
    assert.deepEqual(socket.sent.at(-1)?.payload, {
      source: 'runtime_audio',
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 1000,
      runtimeCuesActive: false,
    })
  })

  it('prefers audio-driven Live2D lipsync over the timed mouth loop when available', () => {
    const { controller, socket, live2dStats } = createHarness()
    live2dStats.runtimeAudioLipsyncResult = true

    controller.connectAgentRuntime()
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'model',
      memoryMessages: 0,
      toolPermissions: [],
    }))
    controller.handleServerEvent(makeEvent('assistant.message', { text: 'hello with audio' }))
    controller.handleServerEvent(makeEvent('audio.tts-ready', {
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 1000,
    }))

    assert.equal(live2dStats.startRuntimeAudioLipsyncCalls, 1)
    assert.equal(live2dStats.startMouthLoopCalls, 0)
  })

  it('reports runtime audio ended and error playback feedback', () => {
    const { controller, socket, audios } = createHarness()

    controller.connectAgentRuntime()
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'model',
      memoryMessages: 0,
      toolPermissions: [],
    }))
    controller.handleServerEvent(makeEvent('assistant.message', { text: 'hello with audio' }))
    controller.handleServerEvent(makeEvent('audio.tts-ready', {
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 1000,
    }))
    audios[0].emit('ended')

    assert.equal(socket.sent.at(-1)?.type, 'audio.playback-ended')
    assert.deepEqual(socket.sent.at(-1)?.payload, {
      source: 'runtime_audio',
      audioUrl: 'http://runtime/audio.wav',
    })

    controller.handleServerEvent(makeEvent('audio.tts-ready', {
      audioUrl: 'http://runtime/broken.wav',
      durationMs: 1000,
    }))
    audios[1].emit('error')

    assert.equal(socket.sent.at(-1)?.type, 'audio.playback-error')
    assert.deepEqual(socket.sent.at(-1)?.payload, {
      source: 'runtime_audio',
      audioUrl: 'http://runtime/broken.wav',
      reason: 'audio_element_error',
    })
  })

  it('applies runtime-provided lipsync cues to Live2D when they arrive', () => {
    const { controller, socket, live2dStats } = createHarness()

    controller.connectAgentRuntime()
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'model',
      memoryMessages: 0,
      toolPermissions: [],
    }))
    controller.handleServerEvent(makeEvent('audio.lipsync-cues', {
      source: 'runtime_audio',
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 320,
      cues: [
        { offsetMs: 0, mouthOpen: 0.2 },
        { offsetMs: 90, mouthOpen: 0.75 },
        { offsetMs: 180, mouthOpen: 0.1 },
      ],
    }))

    assert.equal(live2dStats.applyLipsyncCuesCalls, 1)
    assert.equal(live2dStats.lastLipsyncCueCount, 3)
  })

  it('marks runtime playback as cue-driven when phoneme cues arrive before audio playback', () => {
    const { controller, socket, live2dStats } = createHarness()

    controller.connectAgentRuntime()
    socket.emitServerEvent(makeEvent('server.hello', {
      name: 'amadeus-agent-server',
      model: 'model',
      memoryMessages: 0,
      toolPermissions: [],
    }))
    controller.handleServerEvent(makeEvent('audio.lipsync-cues', {
      source: 'runtime_audio',
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 320,
      cues: [
        { offsetMs: 0, mouthOpen: 0.2, viseme: 'E', phoneme: 'e' },
        { offsetMs: 90, mouthOpen: 0.75, viseme: 'O', phoneme: 'o' },
      ],
    }))
    controller.handleServerEvent(makeEvent('assistant.message', { text: 'hello with audio' }))
    controller.handleServerEvent(makeEvent('audio.tts-ready', {
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 1000,
    }))

    assert.equal(socket.sent.at(-1)?.type, 'audio.playback-started')
    assert.deepEqual(socket.sent.at(-1)?.payload, {
      source: 'runtime_audio',
      audioUrl: 'http://runtime/audio.wav',
      durationMs: 1000,
      runtimeCuesActive: true,
    })
    assert.equal(live2dStats.startMouthLoopCalls, 0)
  })

  it('clears permission prompts and updates tool status on tool.finished', () => {
    const { controller, elements } = createHarness()
    controller.handleServerEvent(makeEvent('tool.permission.request', {
      requestId: 'request-1',
      toolName: 'roll_dice',
      displayName: 'Rolling dice',
      reason: 'Allow dice?',
    }))

    controller.handleServerEvent(makeEvent('tool.finished', {
      toolName: 'roll_dice',
      ok: false,
    }))

    assert.equal(elements.toolPermission.hidden, true)
    assert.equal(elements.toolPermissionText.textContent, '')
    assert.equal(elements.toolStatus.textContent, 'Tool failed: roll_dice')
  })
})
