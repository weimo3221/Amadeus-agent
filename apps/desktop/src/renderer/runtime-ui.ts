import type {
  AudioLipsyncCuesPayload,
  AudioPlaybackEndedPayload,
  AudioPlaybackErrorPayload,
  AudioPlaybackStartedPayload,
  AssistantState,
  CharacterBehaviorPayload,
  DesktopCapabilitiesPayload,
  HelloPayload,
  MemoryReviewCandidate,
  MemoryReviewJob,
  RuntimeEvent,
  ServerRuntimeEvent,
} from '@amadeus-agent/amadeus/events'

const WEB_SOCKET_OPEN = 1
const RAW_MESSAGE_DATASET_KEY = 'rawMessage'

export interface RuntimeSocketLike {
  readyState: number
  send(data: string): void
  addEventListener(type: 'open' | 'message' | 'close' | 'error', listener: (event: any) => void): void
}

export interface RuntimeAudioLike {
  addEventListener(type: 'play' | 'ended' | 'error', listener: () => void): void
  play(): Promise<void>
  pause(): void
}

export interface RuntimeSpeechSynthesisLike {
  paused: boolean
  speaking: boolean
  cancel(): void
  speak(utterance: SpeechSynthesisUtterance): void
  resume(): void
  getVoices(): SpeechSynthesisVoice[]
  addEventListener(type: 'voiceschanged', listener: () => void): void
}

export interface RuntimeLive2DAdapter {
  applyState(state: AssistantState): Promise<void> | void
  applyBehavior(behavior: CharacterBehaviorPayload): Promise<void> | void
  applyLipsyncCues?(payload: AudioLipsyncCuesPayload): void
  startRuntimeAudioLipsync?(audio: RuntimeAudioLike): boolean
  startMouthLoop(): void
  stopMouthLoop(): void
  getCapabilities?(): DesktopCapabilitiesPayload['live2d']
}

export interface RuntimeUiElements {
  statusElement: HTMLElement | null
  chatForm: HTMLFormElement | null
  chatInput: HTMLInputElement | null
  chatLog: HTMLDivElement | null
  skillsStatus: HTMLSpanElement | null
  skillsSearchInput: HTMLInputElement | null
  skillsList: HTMLDivElement | null
  skillsRefreshButton: HTMLButtonElement | null
  skillDetailTitle: HTMLSpanElement | null
  skillDetailBody: HTMLDivElement | null
  voiceButton: HTMLButtonElement | null
  providerLabel: HTMLElement | null
  connectionLabel: HTMLElement | null
  statusDot: HTMLSpanElement | null
  memoryStatus: HTMLSpanElement | null
  toolStatus: HTMLDivElement | null
  skillStatus: HTMLDivElement | null
  toolConfigStatus: HTMLDivElement | null
  toolPermission: HTMLDivElement | null
  toolPermissionText: HTMLSpanElement | null
  toolAllowButton: HTMLButtonElement | null
  toolDenyButton: HTMLButtonElement | null
  memoryReviewStatus: HTMLSpanElement | null
  memoryReviewRunButton: HTMLButtonElement | null
  memoryReviewList: HTMLDivElement | null
  voiceStatus: HTMLDivElement | null
  resetSessionButton: HTMLButtonElement | null
}

export interface RuntimeUiControllerOptions {
  elements: RuntimeUiElements
  wsUrl: string
  skillsUrl: string
  modelLabel: string
  createSocket(url: string): RuntimeSocketLike
  createAudio(url: string): RuntimeAudioLike
  createUtterance(text: string): SpeechSynthesisUtterance
  randomUUID(): string
  setTimeout(handler: () => void, timeout: number): number
  clearTimeout(id: number): void
  fetchImpl?: typeof fetch
  storage?: RuntimeStorageLike
  speechSynthesis?: RuntimeSpeechSynthesisLike
  live2d?: RuntimeLive2DAdapter
}

interface RuntimeSkillSummary {
  name: string
  identifier: string
  description: string
  category?: string | null
}

interface RuntimeSkillsListResponse {
  ok?: boolean
  skills?: unknown
}

export interface RuntimeStorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

const SELECTED_SKILLS_STORAGE_KEY = 'amadeus.desktop.selectedSkills'
const ACTIVE_SKILL_DETAIL_STORAGE_KEY = 'amadeus.desktop.activeSkillDetail'

export class RuntimeUiController {
  private socket: RuntimeSocketLike | undefined
  private sessionId: string
  private activeAssistantMessage: HTMLDivElement | undefined
  private pendingAssistantText = ''
  private lastAssistantSpeechText = ''
  private pendingToolPermissionRequestId: string | undefined
  private voiceEnabled = true
  private currentAudio: RuntimeAudioLike | undefined
  private pendingRuntimeCueAudioUrl: string | undefined
  private pendingSpeechFallbackTimer: number | undefined
  private currentUtterance: SpeechSynthesisUtterance | undefined
  private availableVoices: SpeechSynthesisVoice[] = []
  private memoryReviewPendingCount = 0
  private memoryReviewLastJobText = 'last job: none'
  private availableSkills: RuntimeSkillSummary[] = []
  private readonly selectedSkillIds = new Set<string>()
  private activeSkillDetailId: string | undefined
  private skillSearchQuery = ''

  constructor(private readonly options: RuntimeUiControllerOptions) {
    this.sessionId = options.randomUUID()
    this.restorePersistedSkillState()
  }

  bindControls(): void {
    const { elements } = this.options
    if (elements.providerLabel) {
      elements.providerLabel.textContent = elements.providerLabel.dataset.roleLabel || this.options.modelLabel
    }

    if (this.options.speechSynthesis) {
      this.refreshVoices()
      this.options.speechSynthesis.addEventListener('voiceschanged', () => {
        const voices = this.refreshVoices()
        this.setVoiceStatus(voices.length ? `Voice ready: ${voices.length} voices` : 'Voice unavailable: no system voices')
      })
    }
    else {
      this.setVoiceStatus('Voice unavailable: speechSynthesis is not supported')
    }

    elements.voiceButton?.addEventListener('click', () => {
      this.voiceEnabled = !this.voiceEnabled
      if (elements.voiceButton) {
        elements.voiceButton.textContent = this.voiceEnabled ? 'Voice On' : 'Voice Off'
        elements.voiceButton.title = this.voiceEnabled ? 'Disable voice output' : 'Enable voice output'
      }

      if (!this.voiceEnabled) {
        this.stopAllVoiceOutput()
        this.setVoiceStatus('Voice off')
        return
      }

      this.refreshVoices()
      this.setVoiceStatus('Voice on')
    })

    elements.resetSessionButton?.addEventListener('click', () => {
      if (!this.socket || this.socket.readyState !== WEB_SOCKET_OPEN) {
        this.appendMessage('assistant', 'Agent runtime is not connected. Cannot reset session.')
        return
      }

      elements.chatLog?.replaceChildren()
      this.activeAssistantMessage = undefined
      this.pendingAssistantText = ''
      this.lastAssistantSpeechText = ''
      this.stopAllVoiceOutput()
      this.setToolStatus('Tools idle')
      this.setSkillStatus('Skills idle')
      this.clearToolPermissionPrompt()
      this.setMemoryStatus('Memory: resetting...')
      this.sendEvent('session.reset', {})
    })

    elements.toolAllowButton?.addEventListener('click', () => {
      this.respondToToolPermission(true)
    })

    elements.toolDenyButton?.addEventListener('click', () => {
      this.respondToToolPermission(false)
    })

    elements.memoryReviewRunButton?.addEventListener('click', () => {
      if (!this.socket || this.socket.readyState !== WEB_SOCKET_OPEN) {
        this.setMemoryReviewStatus('Memory review unavailable: disconnected')
        return
      }

      this.setMemoryReviewStatus('Memory review running...')
      this.sendEvent('memory.review.run', { force: true })
    })

    elements.skillsRefreshButton?.addEventListener('click', () => {
      void this.loadAvailableSkills()
    })

    elements.skillsSearchInput?.addEventListener('input', () => {
      this.skillSearchQuery = elements.skillsSearchInput?.value.trim().toLowerCase() || ''
      this.renderSkillOptions()
      this.updateSkillsStatus()
    })

    elements.chatForm?.addEventListener('submit', (event) => {
      event.preventDefault()
      const text = elements.chatInput?.value.trim()
      if (!text) {
        return
      }

      this.appendMessage('user', text)
      this.activeAssistantMessage = undefined
      this.pendingAssistantText = ''
      this.lastAssistantSpeechText = ''
      this.stopAllVoiceOutput()

      if (!this.socket || this.socket.readyState !== WEB_SOCKET_OPEN) {
        this.appendMessage('assistant', 'Agent runtime is not connected. Start apps/server and try again.')
        if (elements.chatInput) {
          elements.chatInput.value = ''
        }
        return
      }

      const activeSkills = this.getSelectedSkills()
      this.sendEvent('user.message', {
        text,
        inputMode: 'text',
        ...(activeSkills.length ? { skills: activeSkills } : {}),
      })
      if (elements.chatInput) {
        elements.chatInput.value = ''
      }
    })
  }

  connectAgentRuntime(): void {
    this.setConnection('Connecting', false)
    this.socket = this.options.createSocket(this.options.wsUrl)

    this.socket.addEventListener('open', () => {
      this.setConnection('Connected', true)
      void this.loadAvailableSkills()
    })

    this.socket.addEventListener('message', (message) => {
      try {
        this.handleServerEvent(JSON.parse(String(message.data)) as ServerRuntimeEvent)
      }
      catch (error) {
        console.error(error)
      }
    })

    this.socket.addEventListener('close', () => {
      this.setConnection('Disconnected', false)
      this.options.setTimeout(() => this.connectAgentRuntime(), 1800)
    })

    this.socket.addEventListener('error', () => {
      this.setConnection('Runtime offline', false)
    })
  }

  handleServerEvent(event: ServerRuntimeEvent): void {
    switch (event.type) {
      case 'server.hello':
        this.sessionId = event.sessionId
        if (this.options.elements.providerLabel) {
          this.options.elements.providerLabel.textContent = this.options.elements.providerLabel.dataset.roleLabel || event.payload.model
        }
        this.setMemoryStatus(`Memory: ${event.payload.memoryMessages} messages`)
        this.setToolConfigStatus(formatToolConfigStatus(event.payload))
        this.setConnection('Connected', true)
        this.reportDesktopCapabilities()
        this.requestMemoryReviewCandidates()
        break
      case 'memory.updated':
        this.setMemoryStatus(`Memory: ${event.payload.memoryMessages} messages`)
        break
      case 'memory.review.candidates':
        this.renderMemoryReviewCandidates(event.payload.candidates)
        break
      case 'memory.review.jobs':
        this.updateMemoryReviewJobs(event.payload.jobs)
        break
      case 'memory.review.updated':
        if (event.payload.job) {
          this.memoryReviewLastJobText = formatMemoryReviewJob(event.payload.job)
        }
        if (event.payload.error) {
          this.updateMemoryReviewStatus(`Memory review failed: ${event.payload.error}`)
          break
        }
        if (event.payload.accepted) {
          this.updateMemoryReviewStatus('Memory candidate accepted')
        }
        else if (event.payload.rejected) {
          this.updateMemoryReviewStatus('Memory candidate rejected')
        }
        else if (event.payload.reviewed) {
          this.updateMemoryReviewStatus(`Memory review generated ${event.payload.candidateCount ?? 0} candidates`)
        }
        else {
          this.updateMemoryReviewStatus(`Memory review skipped: ${event.payload.reason ?? 'not needed'}`)
        }
        this.requestMemoryReviewCandidates()
        break
      case 'assistant.delta':
        this.pendingAssistantText += event.payload.text
        this.appendAssistantDelta(event.payload.text)
        break
      case 'assistant.message':
        if (!this.activeAssistantMessage && event.payload.text) {
          this.activeAssistantMessage = this.appendMessage('assistant', event.payload.text)
        }
        this.finalizeAssistantMessage()
        this.lastAssistantSpeechText = event.payload.text || this.pendingAssistantText
        this.scheduleSpeechFallback(this.lastAssistantSpeechText)
        this.pendingAssistantText = ''
        break
      case 'assistant.state':
        this.setStatus(`State: ${event.payload.state}`, event.payload.state !== 'idle')
        void this.options.live2d?.applyState(event.payload.state)
        break
      case 'character.behavior':
        void this.options.live2d?.applyBehavior(event.payload)
        break
      case 'audio.lipsync-cues':
        if (event.payload.source === 'runtime_audio' && event.payload.audioUrl) {
          this.pendingRuntimeCueAudioUrl = event.payload.audioUrl
        }
        this.options.live2d?.applyLipsyncCues?.(event.payload)
        break
      case 'audio.tts-ready':
        this.playRuntimeAudio(event.payload.audioUrl, event.payload.durationMs ?? undefined)
        break
      case 'tool.started':
        this.setToolStatus(`Tool running: ${event.payload.displayName}`)
        break
      case 'skill.started':
        this.setSkillStatus('Skill activating')
        break
      case 'skill.finished':
        this.setSkillStatus(event.payload.ok
          ? 'Skill ready'
          : 'Skill unavailable')
        break
      case 'tool.finished':
        this.clearToolPermissionPrompt()
        this.setToolStatus(`Tool ${event.payload.ok ? 'finished' : 'failed'}: ${event.payload.toolName}`)
        break
      case 'tool.permission.request':
        this.setToolPermissionPrompt(event.payload.requestId, event.payload.reason)
        this.setToolStatus(`Tool needs permission: ${event.payload.displayName}`)
        break
      case 'error':
        this.activeAssistantMessage = undefined
        this.pendingAssistantText = ''
        this.clearToolPermissionPrompt()
        this.appendMessage('assistant', `Error: ${event.payload.message}`)
        this.setConnection('Error', false)
        void this.options.live2d?.applyState('error')
        break
    }
  }

  reportDesktopCapabilities(): void {
    if (!this.socket || this.socket.readyState !== WEB_SOCKET_OPEN) {
      return
    }

    this.sendEvent('desktop.capabilities', this.buildDesktopCapabilities())
  }

  private setStatus(message: string, visible = true): void {
    const { statusElement } = this.options.elements
    if (!statusElement) {
      return
    }

    statusElement.textContent = message
    statusElement.hidden = !visible
  }

  private setVoiceStatus(message: string): void {
    if (this.options.elements.voiceStatus) {
      this.options.elements.voiceStatus.textContent = message
    }
  }

  private setMemoryStatus(message: string): void {
    if (this.options.elements.memoryStatus) {
      this.options.elements.memoryStatus.textContent = message
    }
  }

  private setToolStatus(message: string): void {
    if (this.options.elements.toolStatus) {
      this.options.elements.toolStatus.textContent = message
    }
  }

  private setSkillStatus(message: string): void {
    if (this.options.elements.skillStatus) {
      this.options.elements.skillStatus.textContent = message
    }
  }

  private setToolConfigStatus(message: string): void {
    if (this.options.elements.toolConfigStatus) {
      this.options.elements.toolConfigStatus.textContent = message
    }
  }

  private setMemoryReviewStatus(message: string): void {
    if (this.options.elements.memoryReviewStatus) {
      this.options.elements.memoryReviewStatus.textContent = message
    }
  }

  private setSkillsStatus(message: string): void {
    if (this.options.elements.skillsStatus) {
      this.options.elements.skillsStatus.textContent = message
    }
  }

  private renderSkillDetailPlaceholder(title: string, body = ''): void {
    const { skillDetailTitle, skillDetailBody } = this.options.elements
    if (skillDetailTitle) {
      skillDetailTitle.textContent = title
    }
    if (skillDetailBody) {
      skillDetailBody.textContent = body
    }
  }

  private updateMemoryReviewStatus(prefix?: string): void {
    const pendingText = `${this.memoryReviewPendingCount} pending`
    const status = prefix ? `${prefix} | ${pendingText} | ${this.memoryReviewLastJobText}` : `Memory review: ${pendingText} | ${this.memoryReviewLastJobText}`
    this.setMemoryReviewStatus(status)
  }

  private setToolPermissionPrompt(requestId: string, message: string): void {
    this.pendingToolPermissionRequestId = requestId
    if (this.options.elements.toolPermissionText) {
      this.options.elements.toolPermissionText.textContent = message
    }
    if (this.options.elements.toolPermission) {
      this.options.elements.toolPermission.hidden = false
    }
  }

  private clearToolPermissionPrompt(): void {
    this.pendingToolPermissionRequestId = undefined
    if (this.options.elements.toolPermissionText) {
      this.options.elements.toolPermissionText.textContent = ''
    }
    if (this.options.elements.toolPermission) {
      this.options.elements.toolPermission.hidden = true
    }
  }

  private respondToToolPermission(approved: boolean): void {
    if (!this.pendingToolPermissionRequestId) {
      return
    }

    this.sendEvent('tool.permission.response', {
      requestId: this.pendingToolPermissionRequestId,
      approved,
    })
    this.setToolStatus(approved ? 'Tool permission approved' : 'Tool permission denied')
    this.clearToolPermissionPrompt()
  }

  private requestMemoryReviewCandidates(): void {
    if (!this.socket || this.socket.readyState !== WEB_SOCKET_OPEN) {
      return
    }

    this.sendEvent('memory.review.list', { status: 'pending' })
  }

  private renderMemoryReviewCandidates(candidates: MemoryReviewCandidate[]): void {
    const { memoryReviewList } = this.options.elements
    if (!memoryReviewList) {
      return
    }

    memoryReviewList.replaceChildren()
    this.memoryReviewPendingCount = candidates.length
    this.updateMemoryReviewStatus()
    for (const candidate of candidates.slice(0, 5)) {
      memoryReviewList.append(this.createMemoryReviewCandidateElement(candidate))
    }
  }

  private async loadAvailableSkills(): Promise<void> {
    const { skillsList, skillsRefreshButton } = this.options.elements
    const fetchImpl = this.options.fetchImpl ?? fetch
    if (!skillsList) {
      return
    }

    this.setSkillsStatus('Suggested skills: loading...')
    if (skillsRefreshButton) {
      skillsRefreshButton.disabled = true
    }

    try {
      const response = await fetchImpl(this.options.skillsUrl, { method: 'GET' })
      const payload = await response.json().catch(() => undefined) as RuntimeSkillsListResponse | undefined
      if (!response.ok || !payload?.ok || !Array.isArray(payload.skills)) {
        this.availableSkills = []
        this.selectedSkillIds.clear()
        skillsList.replaceChildren()
        this.setSkillsStatus('Suggested skills unavailable')
        return
      }

      this.availableSkills = payload.skills
        .map(normalizeRuntimeSkillSummary)
        .filter((skill): skill is RuntimeSkillSummary => skill !== undefined)

      const availableIdentifiers = new Set(this.availableSkills.map((skill) => skill.identifier))
      for (const identifier of Array.from(this.selectedSkillIds)) {
        if (!availableIdentifiers.has(identifier)) {
          this.selectedSkillIds.delete(identifier)
        }
      }
      if (this.activeSkillDetailId && !availableIdentifiers.has(this.activeSkillDetailId)) {
        this.activeSkillDetailId = undefined
      }

      this.renderSkillOptions()
      this.updateSkillsStatus()
      this.persistSelectedSkills()
      if (this.availableSkills.length) {
        const detailId = this.activeSkillDetailId
          ?? this.getSelectedSkills()[0]
          ?? this.availableSkills[0]?.identifier
        if (detailId) {
          void this.showSkillDetail(detailId)
        }
      }
      else {
        this.renderSkillDetailPlaceholder('Skill Preview', 'No installed skills')
      }
    }
    catch {
      this.availableSkills = []
      this.selectedSkillIds.clear()
      this.activeSkillDetailId = undefined
      skillsList.replaceChildren()
      this.setSkillsStatus('Suggested skills unavailable')
      this.renderSkillDetailPlaceholder('Skill Preview', 'Skills unavailable')
    }
    finally {
      if (skillsRefreshButton) {
        skillsRefreshButton.disabled = false
      }
    }
  }

  private renderSkillOptions(): void {
    const { skillsList } = this.options.elements
    if (!skillsList) {
      return
    }

    skillsList.replaceChildren()
    if (!this.availableSkills.length) {
      const empty = document.createElement('div')
      empty.className = 'skills-empty'
      empty.textContent = 'No installed skills'
      skillsList.append(empty)
      return
    }

    const visibleSkills = this.getVisibleSkills()
    if (!visibleSkills.length) {
      const empty = document.createElement('div')
      empty.className = 'skills-empty'
      empty.textContent = 'No matching skills'
      skillsList.append(empty)
      return
    }

    for (const skill of visibleSkills) {
      skillsList.append(this.createSkillOptionElement(skill))
    }
  }

  private createSkillOptionElement(skill: RuntimeSkillSummary): HTMLDivElement {
    const item = document.createElement('div')
    item.className = 'skill-option'
    item.title = `${skill.identifier}${skill.description ? `\n${skill.description}` : ''}`
    if (this.activeSkillDetailId === skill.identifier) {
      item.dataset.active = 'true'
    }

    const checkbox = document.createElement('input')
    checkbox.type = 'checkbox'
    checkbox.value = skill.identifier
    checkbox.checked = this.selectedSkillIds.has(skill.identifier)
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) {
        this.selectedSkillIds.add(skill.identifier)
      }
      else {
        this.selectedSkillIds.delete(skill.identifier)
      }
      this.persistSelectedSkills()
      this.updateSkillsStatus()
      void this.showSkillDetail(skill.identifier)
    })

    const summary = document.createElement('button')
    summary.type = 'button'
    summary.className = 'skill-option-summary'
    summary.addEventListener('click', () => {
      void this.showSkillDetail(skill.identifier)
    })

    const title = document.createElement('span')
    title.className = 'skill-option-title'
    title.textContent = skill.identifier

    const description = document.createElement('span')
    description.className = 'skill-option-description'
    description.textContent = skill.description || skill.name

    summary.append(title)
    summary.append(description)
    item.append(checkbox)
    item.append(summary)
    return item
  }

  private getSelectedSkills(): string[] {
    return this.availableSkills
      .filter((skill) => this.selectedSkillIds.has(skill.identifier))
      .map((skill) => skill.identifier)
  }

  private getVisibleSkills(): RuntimeSkillSummary[] {
    if (!this.skillSearchQuery) {
      return this.availableSkills
    }

    return this.availableSkills.filter((skill) => {
      const haystacks = [
        skill.identifier,
        skill.name,
        skill.description,
        skill.category ?? '',
      ]
      return haystacks.some((value) => value.toLowerCase().includes(this.skillSearchQuery))
    })
  }

  private async showSkillDetail(identifier: string): Promise<void> {
    const summary = this.availableSkills.find((skill) => skill.identifier === identifier)
    if (!summary) {
      this.renderSkillDetailPlaceholder('Skill Preview', 'Skill not found')
      return
    }

    this.activeSkillDetailId = identifier
    this.persistActiveSkillDetail()
    this.renderSkillOptions()
    this.renderSkillDetailPlaceholder(
      summary.identifier,
      summary.description || summary.name || 'No summary available.',
    )
  }

  private updateSkillsStatus(): void {
    if (!this.availableSkills.length) {
      this.setSkillsStatus('Suggested skills: none installed')
      return
    }

    const selectedCount = this.selectedSkillIds.size
    const visibleCount = this.getVisibleSkills().length
    const visibleText = this.skillSearchQuery
      ? `${visibleCount}/${this.availableSkills.length} shown`
      : `${this.availableSkills.length} available`
    this.setSkillsStatus(
      selectedCount > 0
        ? `Suggested skills: ${visibleText}, ${selectedCount} selected`
        : `Suggested skills: ${visibleText}`,
    )
  }

  private restorePersistedSkillState(): void {
    const storage = this.options.storage ?? readWindowStorage()
    if (!storage) {
      return
    }

    try {
      const rawSelected = storage.getItem(SELECTED_SKILLS_STORAGE_KEY)
      if (rawSelected) {
        const parsed = JSON.parse(rawSelected) as unknown
        if (Array.isArray(parsed)) {
          for (const item of parsed) {
            if (typeof item === 'string' && item.trim()) {
              this.selectedSkillIds.add(item)
            }
          }
        }
      }

      const activeDetail = storage.getItem(ACTIVE_SKILL_DETAIL_STORAGE_KEY)
      if (activeDetail && activeDetail.trim()) {
        this.activeSkillDetailId = activeDetail
      }
    }
    catch {
      this.selectedSkillIds.clear()
      this.activeSkillDetailId = undefined
    }
  }

  private persistSelectedSkills(): void {
    const storage = this.options.storage ?? readWindowStorage()
    if (!storage) {
      return
    }

    const selected = this.getSelectedSkills()
    if (!selected.length) {
      storage.removeItem(SELECTED_SKILLS_STORAGE_KEY)
      return
    }

    storage.setItem(SELECTED_SKILLS_STORAGE_KEY, JSON.stringify(selected))
  }

  private persistActiveSkillDetail(): void {
    const storage = this.options.storage ?? readWindowStorage()
    if (!storage) {
      return
    }

    if (!this.activeSkillDetailId) {
      storage.removeItem(ACTIVE_SKILL_DETAIL_STORAGE_KEY)
      return
    }

    storage.setItem(ACTIVE_SKILL_DETAIL_STORAGE_KEY, this.activeSkillDetailId)
  }

  private updateMemoryReviewJobs(jobs: MemoryReviewJob[]): void {
    const [latestJob] = jobs
    this.memoryReviewLastJobText = latestJob ? formatMemoryReviewJob(latestJob) : 'last job: none'
    this.updateMemoryReviewStatus()
  }

  private createMemoryReviewCandidateElement(candidate: MemoryReviewCandidate): HTMLDivElement {
    const item = document.createElement('div')
    item.className = 'memory-review-candidate'

    const content = document.createElement('div')
    content.className = 'memory-review-content'
    const labels = candidate.safetyLabels?.length ? ` [${candidate.safetyLabels.join(', ')}]` : ''
    const retention = candidate.retentionType ? ` ${candidate.retentionType}` : ''
    content.textContent = `${candidate.scope}${retention} ${Math.round(candidate.confidence * 100)}%${labels}: ${candidate.content}`
    content.title = [
      candidate.reason,
      candidate.scopeReason ? `Scope: ${candidate.scopeReason}` : undefined,
      candidate.content,
    ].filter(Boolean).join('\n')

    const actions = document.createElement('div')
    actions.className = 'memory-review-actions'

    const acceptButton = document.createElement('button')
    acceptButton.type = 'button'
    acceptButton.textContent = 'Accept'
    acceptButton.addEventListener('click', () => {
      this.setMemoryReviewStatus('Accepting memory candidate...')
      this.sendEvent('memory.review.accept', { candidateId: candidate.candidateId })
    })

    const rejectButton = document.createElement('button')
    rejectButton.type = 'button'
    rejectButton.textContent = 'Reject'
    rejectButton.addEventListener('click', () => {
      this.setMemoryReviewStatus('Rejecting memory candidate...')
      this.sendEvent('memory.review.reject', { candidateId: candidate.candidateId })
    })

    actions.append(acceptButton)
    actions.append(rejectButton)
    item.append(content)
    item.append(actions)
    return item
  }

  private appendMessage(role: 'user' | 'assistant', text: string): HTMLDivElement | undefined {
    const { chatLog } = this.options.elements
    if (!chatLog) {
      return undefined
    }

    const item = document.createElement('div')
    item.className = `message ${role}`
    item.dataset[RAW_MESSAGE_DATASET_KEY] = text
    renderMarkdownInto(item, text)
    chatLog.append(item)
    chatLog.scrollTop = chatLog.scrollHeight
    return item
  }

  private finalizeAssistantMessage(): void {
    const message = this.activeAssistantMessage
    this.activeAssistantMessage = undefined
    if (!message) {
      return
    }

    if (message.classList) {
      message.classList.add('message-complete')
    }
    else {
      message.className = `${message.className} message-complete`.trim()
    }

    message.addEventListener?.('animationend', () => {
      message.remove()
    }, { once: true })
  }

  private appendAssistantDelta(text: string): void {
    if (!this.activeAssistantMessage) {
      this.activeAssistantMessage = this.appendMessage('assistant', '')
    }

    if (!this.activeAssistantMessage) {
      return
    }

    const currentText = this.activeAssistantMessage.dataset[RAW_MESSAGE_DATASET_KEY] ?? ''
    this.activeAssistantMessage.dataset[RAW_MESSAGE_DATASET_KEY] = currentText + text
    renderMarkdownInto(this.activeAssistantMessage, currentText + text)
    if (this.options.elements.chatLog) {
      this.options.elements.chatLog.scrollTop = this.options.elements.chatLog.scrollHeight
    }
  }

  private setConnection(text: string, connected: boolean): void {
    if (this.options.elements.connectionLabel) {
      this.options.elements.connectionLabel.textContent = ''
      this.options.elements.connectionLabel.dataset.connected = String(connected)
      this.options.elements.connectionLabel.dataset.state = text.toLowerCase()
      this.options.elements.connectionLabel.title = text
      this.options.elements.connectionLabel.setAttribute('aria-label', text)
    }

    if (this.options.elements.statusDot) {
      this.options.elements.statusDot.dataset.connected = String(connected)
    }
  }

  private sendEvent<TType extends string, TPayload>(type: TType, payload: TPayload): void {
    const event: RuntimeEvent<TType, TPayload> = {
      id: this.options.randomUUID(),
      type,
      sessionId: this.sessionId,
      timestamp: new Date().toISOString(),
      payload,
    }
    this.socket?.send(JSON.stringify(event))
  }

  private buildDesktopCapabilities(): DesktopCapabilitiesPayload {
    const live2d = this.options.live2d?.getCapabilities?.() ?? {
      available: false,
      expressions: [],
      motions: [],
    }
    const voices = this.availableVoices.length ? this.availableVoices : this.refreshVoices()
    return {
      desktop: {
        runtime: 'electron',
        protocolVersion: 1,
      },
      live2d,
      audio: {
        runtimeAudio: true,
        speechSynthesis: Boolean(this.options.speechSynthesis),
        voiceCount: voices.length,
      },
    }
  }

  private refreshVoices(): SpeechSynthesisVoice[] {
    const speechSynthesis = this.options.speechSynthesis
    if (!speechSynthesis) {
      this.setVoiceStatus('Voice unavailable: speechSynthesis is not supported')
      return []
    }

    this.availableVoices = speechSynthesis.getVoices()
    if (!this.availableVoices.length) {
      this.setVoiceStatus('Voice waiting for system voices')
    }
    return this.availableVoices
  }

  private pickVoice(lang: string): SpeechSynthesisVoice | undefined {
    const voices = this.availableVoices.length ? this.availableVoices : this.refreshVoices()
    const normalizedLang = lang.toLowerCase()
    return (
      voices.find((voice) => voice.lang.toLowerCase() === normalizedLang)
      ?? voices.find((voice) => voice.lang.toLowerCase().startsWith(normalizedLang.split('-')[0]))
      ?? voices[0]
    )
  }

  private speak(text: string): void {
    const speechSynthesis = this.options.speechSynthesis
    if (!this.voiceEnabled || !speechSynthesis) {
      this.setVoiceStatus(this.voiceEnabled ? 'Voice unavailable' : 'Voice off')
      return
    }

    const normalizedText = text.trim()
    if (!normalizedText) {
      return
    }

    speechSynthesis.cancel()
    this.options.live2d?.stopMouthLoop()

    this.refreshVoices()
    const utterance = this.options.createUtterance(normalizedText)
    utterance.lang = /[\u4E00-\u9FFF]/.test(normalizedText) ? 'zh-CN' : 'en-US'
    const voice = this.pickVoice(utterance.lang)
    if (voice) {
      utterance.voice = voice
      utterance.lang = voice.lang
    }
    utterance.rate = 1
    utterance.pitch = 1.05
    utterance.volume = 1

    utterance.addEventListener('start', () => {
      this.setVoiceStatus(`Speaking with ${utterance.voice?.name ?? utterance.lang}`)
      void this.options.live2d?.applyState('speaking')
      this.options.live2d?.startMouthLoop()
    })

    utterance.addEventListener('end', () => {
      this.setVoiceStatus('Voice idle')
      this.options.live2d?.stopMouthLoop()
      void this.options.live2d?.applyState('idle')
      this.currentUtterance = undefined
    })

    utterance.addEventListener('error', (event) => {
      this.setVoiceStatus(`Voice error: ${event.error}`)
      this.options.live2d?.stopMouthLoop()
      void this.options.live2d?.applyState('idle')
      this.currentUtterance = undefined
    })

    this.currentUtterance = utterance
    this.setVoiceStatus(`Queued voice (${utterance.voice?.name ?? utterance.lang})`)
    speechSynthesis.speak(utterance)
    speechSynthesis.resume()

    this.options.setTimeout(() => {
      if (speechSynthesis.paused) {
        speechSynthesis.resume()
      }
      if (speechSynthesis.speaking) {
        this.options.live2d?.startMouthLoop()
      }
    }, 250)
  }

  private cancelSpeechFallback(): void {
    if (this.pendingSpeechFallbackTimer !== undefined) {
      this.options.clearTimeout(this.pendingSpeechFallbackTimer)
      this.pendingSpeechFallbackTimer = undefined
    }
  }

  private scheduleSpeechFallback(text: string): void {
    this.cancelSpeechFallback()
    this.pendingSpeechFallbackTimer = this.options.setTimeout(() => {
      this.pendingSpeechFallbackTimer = undefined
      this.speak(text)
    }, 300)
  }

  private stopRuntimeAudio(): void {
    this.currentAudio?.pause()
    this.options.live2d?.stopMouthLoop()
    this.currentAudio = undefined
  }

  private stopAllVoiceOutput(): void {
    this.cancelSpeechFallback()
    this.stopRuntimeAudio()
    this.options.speechSynthesis?.cancel()
    this.currentUtterance = undefined
    this.options.live2d?.stopMouthLoop()
  }

  private playRuntimeAudio(audioUrl: string, durationMs?: number): void {
    if (!this.voiceEnabled) {
      this.setVoiceStatus('Voice off')
      return
    }

    this.cancelSpeechFallback()
    this.stopRuntimeAudio()
    this.options.speechSynthesis?.cancel()
    this.options.live2d?.stopMouthLoop()
    if (this.pendingRuntimeCueAudioUrl && this.pendingRuntimeCueAudioUrl !== audioUrl) {
      this.pendingRuntimeCueAudioUrl = undefined
    }

    const audio = this.options.createAudio(audioUrl)
    this.currentAudio = audio

    audio.addEventListener('play', () => {
      this.setVoiceStatus('Playing runtime audio')
      this.sendAudioPlaybackStarted({
        source: 'runtime_audio',
        audioUrl,
        durationMs,
        runtimeCuesActive: this.pendingRuntimeCueAudioUrl === audioUrl,
      })
      void this.options.live2d?.applyState('speaking')
      const startedAudioDrivenLipsync = this.options.live2d?.startRuntimeAudioLipsync?.(audio) ?? false
      if (!startedAudioDrivenLipsync && this.pendingRuntimeCueAudioUrl !== audioUrl) {
        this.options.live2d?.startMouthLoop()
      }
    })

    audio.addEventListener('ended', () => {
      this.setVoiceStatus('Voice idle')
      this.sendAudioPlaybackEnded({ source: 'runtime_audio', audioUrl })
      this.options.live2d?.stopMouthLoop()
      void this.options.live2d?.applyState('idle')
      this.currentAudio = undefined
      this.pendingRuntimeCueAudioUrl = undefined
    })

    audio.addEventListener('error', () => {
      this.setVoiceStatus('Runtime audio failed; using system voice')
      this.sendAudioPlaybackError({ source: 'runtime_audio', audioUrl, reason: 'audio_element_error' })
      this.options.live2d?.stopMouthLoop()
      this.currentAudio = undefined
      this.pendingRuntimeCueAudioUrl = undefined
      this.speak(this.lastAssistantSpeechText)
    })

    void audio.play().catch(() => {
      this.setVoiceStatus('Runtime audio blocked; using system voice')
      this.sendAudioPlaybackError({ source: 'runtime_audio', audioUrl, reason: 'play_rejected' })
      this.currentAudio = undefined
      this.pendingRuntimeCueAudioUrl = undefined
      this.speak(this.lastAssistantSpeechText)
    })
  }

  private sendAudioPlaybackStarted(payload: AudioPlaybackStartedPayload): void {
    this.sendEvent('audio.playback-started', payload)
  }

  private sendAudioPlaybackEnded(payload: AudioPlaybackEndedPayload): void {
    this.sendEvent('audio.playback-ended', payload)
  }

  private sendAudioPlaybackError(payload: AudioPlaybackErrorPayload): void {
    this.sendEvent('audio.playback-error', payload)
  }
}

function formatToolConfigStatus(payload: HelloPayload): string {
  const summary = payload.toolPermissions
    .map((tool) => `${tool.name} ${tool.enabled ? tool.permission : 'off'}`)
    .join(', ')

  return `Tools: ${summary || 'none'}`
}

function formatMemoryReviewJob(job: MemoryReviewJob): string {
  const countText = job.status === 'completed'
    ? `${job.savedCandidateCount}/${job.proposedCandidateCount} saved`
    : job.reason || job.error || 'no detail'
  const durationText = job.durationMs ? `, ${job.durationMs}ms` : ''
  return `last job: ${job.status} (${job.trigger}, ${countText}${durationText})`
}

function renderMarkdownInto(element: HTMLElement, markdown: string): void {
  element.innerHTML = renderMarkdown(markdown)
}

function renderMarkdown(markdown: string): string {
  const normalized = markdown.replace(/\r\n?/g, '\n')
  const blocks: string[] = []
  let cursor = 0

  for (const match of normalized.matchAll(/```([^\n`]*)\n?([\s\S]*?)```/g)) {
    const matchIndex = match.index ?? 0
    const before = normalized.slice(cursor, matchIndex)
    blocks.push(renderMarkdownBlocks(before))
    const language = sanitizeClassName(match[1]?.trim() ?? '')
    const code = escapeHtml(match[2] ?? '')
    const languageClass = language ? ` class="language-${language}"` : ''
    blocks.push(`<pre><code${languageClass}>${code}</code></pre>`)
    cursor = matchIndex + match[0].length
  }

  blocks.push(renderMarkdownBlocks(normalized.slice(cursor)))
  return blocks.join('')
}

function renderMarkdownBlocks(markdown: string): string {
  const lines = markdown.split('\n')
  const html: string[] = []
  let paragraph: string[] = []
  let listItems: string[] = []

  const flushParagraph = (): void => {
    if (!paragraph.length) {
      return
    }
    html.push(`<p>${paragraph.map(renderInlineMarkdown).join('<br>')}</p>`)
    paragraph = []
  }

  const flushList = (): void => {
    if (!listItems.length) {
      return
    }
    html.push(`<ul>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join('')}</ul>`)
    listItems = []
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      flushParagraph()
      flushList()
      continue
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1].length
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`)
      continue
    }

    const listItem = /^[-*]\s+(.+)$/.exec(trimmed)
    if (listItem) {
      flushParagraph()
      listItems.push(listItem[1])
      continue
    }

    if (trimmed.startsWith('> ')) {
      flushParagraph()
      flushList()
      html.push(`<blockquote>${renderInlineMarkdown(trimmed.slice(2))}</blockquote>`)
      continue
    }

    flushList()
    paragraph.push(trimmed)
  }

  flushParagraph()
  flushList()
  return html.join('')
}

function renderInlineMarkdown(text: string): string {
  const codeSegments: string[] = []
  let escaped = escapeHtml(text).replace(/`([^`]+)`/g, (_match, code: string) => {
    const index = codeSegments.push(`<code>${code}</code>`) - 1
    return `\u0000CODE${index}\u0000`
  })

  escaped = escaped
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
    .replace(/_([^_\n]+)_/g, '<em>$1</em>')

  return escaped.replace(/\u0000CODE(\d+)\u0000/g, (_match, index: string) => codeSegments[Number(index)] ?? '')
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function sanitizeClassName(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 32)
}

function normalizeRuntimeSkillSummary(value: unknown): RuntimeSkillSummary | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined
  }

  const skill = value as Record<string, unknown>
  if (
    typeof skill.name !== 'string'
    || typeof skill.identifier !== 'string'
    || typeof skill.description !== 'string'
  ) {
    return undefined
  }

  return {
    name: skill.name,
    identifier: skill.identifier,
    description: skill.description,
    category: typeof skill.category === 'string' ? skill.category : undefined,
  }
}

function readWindowStorage(): RuntimeStorageLike | undefined {
  if (typeof window === 'undefined' || !('localStorage' in window)) {
    return undefined
  }
  return window.localStorage
}
