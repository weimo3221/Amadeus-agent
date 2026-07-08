<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmInput from '@/components/ui/AmInput.vue'
import AmSelect from '@/components/ui/AmSelect.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmTabs from '@/components/ui/AmTabs.vue'
import AmTag from '@/components/ui/AmTag.vue'
import type { ToolTone } from '@/types'
import type { Live2dBehavior } from '@/runtime/http'

interface BehaviorFormEntry {
  emotion: string
  expression: string
  motion: string
  intensity: string
}

const {
  state,
  saveApiConfig,
  saveAudioConfig,
  saveLive2dBehaviors,
  importLive2d,
  selectLive2d,
  refreshMcpDiagnostics,
  refreshEmbeddingConfig,
  deployEmbedding,
  cancelEmbedding,
} =
  useRuntime()

const activeTab = ref('model')

const tabs = [
  { value: 'model', label: '模型', icon: 'ph:cpu-duotone' },
  { value: 'memory', label: '记忆', icon: 'ph:brain-duotone' },
  { value: 'live2d', label: '形象', icon: 'ph:sparkle-duotone' },
  { value: 'voice', label: '语音', icon: 'ph:waveform-duotone' },
  { value: 'mcp', label: 'MCP', icon: 'ph:plugs-connected-duotone' },
]

function flash(target: { value: boolean }) {
  target.value = true
  setTimeout(() => (target.value = false), 2200)
}

function shortDateTime(iso?: string): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/* ---------------- 模型 ---------------- */
const apiProvider = ref('')
const apiBaseUrl = ref('')
const apiModel = ref('')
const apiMaxTokens = ref('')
const apiStreaming = ref(true)
const apiThinkingEnabled = ref(false)
const apiReasoningEffort = ref<'low' | 'medium' | 'high'>('medium')
const apiKey = ref('')
const apiSaving = ref(false)
const apiFlash = ref(false)

const reasoningEffortOptions = [
  { label: '低（low）', value: 'low' },
  { label: '中（medium）', value: 'medium' },
  { label: '高（high）', value: 'high' },
]

const providerOptions = computed(() =>
  (state.runtimeConfig?.presets ?? state.providerPresets).map((p) => ({
    label: p.label,
    value: p.id,
  })),
)

const activePreset = computed(
  () => (state.runtimeConfig?.presets ?? state.providerPresets).find((p) => p.id === apiProvider.value) ?? null,
)

function loadApiForm() {
  const api = state.runtimeConfig?.api
  if (!api) return
  apiProvider.value = api.provider
  apiBaseUrl.value = api.baseUrl
  apiModel.value = api.model
  apiMaxTokens.value = api.maxTokens ? String(api.maxTokens) : ''
  apiStreaming.value = api.streaming
  apiThinkingEnabled.value = api.thinkingEnabled
  apiReasoningEffort.value = api.reasoningEffort
  apiKey.value = ''
}

watch(() => state.runtimeConfig, loadApiForm, { immediate: true })

function onApiProviderChange(value: string) {
  apiProvider.value = value
  const api = state.runtimeConfig?.api
  if (api && api.provider === value) {
    apiBaseUrl.value = api.baseUrl
    apiModel.value = api.model
    apiMaxTokens.value = api.maxTokens ? String(api.maxTokens) : ''
    apiStreaming.value = api.streaming
    apiThinkingEnabled.value = api.thinkingEnabled
    apiReasoningEffort.value = api.reasoningEffort
    return
  }
  const profile = state.runtimeConfig?.providers.find((p) => p.id === value)
  const preset = state.runtimeConfig?.presets.find((p) => p.id === value)
  apiBaseUrl.value = profile?.baseUrl || preset?.baseUrl || ''
  apiModel.value = profile?.defaultModel || preset?.defaultModel || ''
  apiMaxTokens.value = profile?.maxTokens ? String(profile.maxTokens) : ''
  apiStreaming.value = profile?.supportsStreaming ?? preset?.supportsStreaming ?? true
  apiThinkingEnabled.value = profile?.thinkingEnabled ?? false
  apiReasoningEffort.value = profile?.reasoningEffort ?? 'medium'
}

async function saveModel() {
  apiSaving.value = true
  const maxTokens = apiMaxTokens.value.trim() ? Number(apiMaxTokens.value.trim()) : 0
  const ok = await saveApiConfig({
    provider: apiProvider.value,
    baseUrl: apiBaseUrl.value.trim() || undefined,
    model: apiModel.value.trim() || undefined,
    maxTokens: Number.isFinite(maxTokens) ? maxTokens : 0,
    streaming: apiStreaming.value,
    thinkingEnabled: apiThinkingEnabled.value,
    reasoningEffort: apiReasoningEffort.value,
    ...(apiKey.value.trim() ? { apiKey: apiKey.value.trim() } : {}),
  })
  apiSaving.value = false
  if (ok) {
    apiKey.value = ''
    flash(apiFlash)
  }
}

/* ---------------- 记忆 Embedding ---------------- */
const embeddingLocalDir = ref('')
const embeddingDeploying = ref(false)
const embeddingRefreshing = ref(false)
const embeddingCancelling = ref(false)
const embeddingFlash = ref(false)
const embeddingError = ref('')
let embeddingPollTimer: number | null = null

const embedding = computed(() => state.embeddingConfig?.embedding ?? null)
const embeddingDeployment = computed(() => embedding.value?.deployment ?? null)
const embeddingDeploymentActive = computed(() => embeddingDeployment.value?.active === true)
const embeddingStatusTone = computed<ToolTone>(() => {
  if (embeddingDeploymentActive.value) return 'info'
  if (embeddingDeployment.value?.status === 'failed') return 'danger'
  if (embedding.value?.deployed) return 'success'
  if (embedding.value?.configured || embedding.value?.dependenciesInstalled || embedding.value?.modelInstalled) return 'warning'
  return 'neutral'
})

const embeddingStatusLabel = computed(() => {
  if (!embedding.value) return '未加载'
  if (embeddingDeploymentActive.value) return '部署中'
  if (embeddingDeployment.value?.status === 'failed') return '部署失败'
  if (embedding.value.deployed) return '已部署'
  if (!embedding.value.configured) return '未配置'
  return '待部署'
})

const embeddingPhaseLabel = computed(() => {
  const phase = embeddingDeployment.value?.phase || 'idle'
  const labels: Record<string, string> = {
    idle: '空闲',
    queued: '排队',
    dependencies: '检查依赖',
    installing_dependencies: '安装依赖',
    downloading_model: '下载模型',
    verifying: '校验文件',
    ready: '就绪',
    cancelling: '取消中',
    cancelled: '已取消',
    failed: '失败',
  }
  return labels[phase] ?? phase
})

const embeddingDependencyRows = computed(() =>
  Object.entries(embedding.value?.dependencyModules ?? {}).map(([name, installed]) => ({
    name,
    installed,
  })),
)

function loadEmbeddingForm() {
  const current = embedding.value
  if (!current) return
  if (!embeddingLocalDir.value || !embeddingDeploymentActive.value) {
    embeddingLocalDir.value = current.localDir
  }
}

watch(() => state.embeddingConfig, loadEmbeddingForm, { immediate: true })

function syncEmbeddingPolling() {
  if (activeTab.value === 'memory' && embeddingDeploymentActive.value) {
    startEmbeddingPolling()
    return
  }
  stopEmbeddingPolling()
}

watch(activeTab, syncEmbeddingPolling)
watch(embeddingDeploymentActive, syncEmbeddingPolling)
onUnmounted(stopEmbeddingPolling)

function startEmbeddingPolling() {
  if (embeddingPollTimer !== null) return
  embeddingPollTimer = window.setInterval(() => {
    void refreshEmbeddingConfig()
  }, 1800)
}

function stopEmbeddingPolling() {
  if (embeddingPollTimer === null) return
  window.clearInterval(embeddingPollTimer)
  embeddingPollTimer = null
}

async function refreshEmbedding() {
  embeddingRefreshing.value = true
  await refreshEmbeddingConfig()
  embeddingRefreshing.value = false
}

async function runEmbeddingDeploy(force = false) {
  embeddingDeploying.value = true
  embeddingError.value = ''
  const ok = await deployEmbedding(embeddingLocalDir.value.trim() || undefined, force)
  embeddingDeploying.value = false
  if (!ok) {
    embeddingError.value = '部署请求失败，请查看 Python runtime 日志。'
    return
  }
  startEmbeddingPolling()
  flash(embeddingFlash)
}

async function cancelEmbeddingDeployAction() {
  embeddingCancelling.value = true
  embeddingError.value = ''
  const ok = await cancelEmbedding()
  embeddingCancelling.value = false
  if (!ok) {
    embeddingError.value = '取消部署失败，请稍后刷新状态。'
    return
  }
  await refreshEmbeddingConfig()
}

/* ---------------- 语音 TTS ---------------- */
const ttsProvider = ref('')
const macosVoice = ref('')
const macosRate = ref('')
const gpt = reactive({
  baseUrl: '',
  endpoint: '',
  textLang: '',
  promptLang: '',
  promptText: '',
  refAudioPath: '',
  timeoutSeconds: '',
  streamingMode: false,
})
const ttsSaving = ref(false)
const ttsFlash = ref(false)

const ttsProviderOptions = computed(() =>
  (state.audioConfig?.providerTypes ?? []).map((t) => ({ label: t.label, value: t.id })),
)

const voiceOptions = computed(() => [
  { label: '系统默认音色', value: '' },
  ...(state.audioConfig?.voices ?? []).map((v) => ({
    label: v.locale ? `${v.label} · ${v.locale}` : v.label,
    value: v.id,
  })),
])

function loadAudioForm() {
  const audio = state.audioConfig
  if (!audio) return
  ttsProvider.value = audio.activeProvider
  macosVoice.value = audio.macos.voice
  macosRate.value = audio.macos.rate
  gpt.baseUrl = audio.gptSovits.baseUrl
  gpt.endpoint = audio.gptSovits.endpoint
  gpt.textLang = audio.gptSovits.textLang
  gpt.promptLang = audio.gptSovits.promptLang
  gpt.promptText = audio.gptSovits.promptText
  gpt.refAudioPath = audio.gptSovits.refAudioPath
  gpt.timeoutSeconds = audio.gptSovits.timeoutSeconds
  gpt.streamingMode = audio.gptSovits.streamingMode
}

watch(() => state.audioConfig, loadAudioForm, { immediate: true })

const showMacos = computed(
  () => ttsProvider.value === 'macos_say' || ttsProvider.value === 'auto',
)
const showGptSovits = computed(
  () => ttsProvider.value === 'gpt_sovits' || ttsProvider.value === 'auto',
)

async function saveVoice() {
  ttsSaving.value = true
  const ok = await saveAudioConfig({
    provider: ttsProvider.value,
    macos: { voice: macosVoice.value, rate: macosRate.value.trim() },
    gptSovits: {
      baseUrl: gpt.baseUrl.trim(),
      endpoint: gpt.endpoint.trim(),
      textLang: gpt.textLang.trim(),
      promptLang: gpt.promptLang.trim(),
      promptText: gpt.promptText,
      refAudioPath: gpt.refAudioPath.trim(),
      timeoutSeconds: gpt.timeoutSeconds.trim(),
      streamingMode: gpt.streamingMode,
    },
  })
  ttsSaving.value = false
  if (ok) flash(ttsFlash)
}

/* ---------------- Live2D 形象 ---------------- */
const importSourceDir = ref('')
const importModelId = ref('')
const importActivate = ref(true)
const importing = ref(false)
const importError = ref('')
const importFlash = ref(false)

const behaviorForm = reactive<Record<string, BehaviorFormEntry>>({})
const behaviorSaving = ref(false)
const behaviorFlash = ref(false)

const behaviorStates = computed(() => state.live2dBehaviors?.states ?? [])
const expressionSuggestions = computed(() => state.live2dBehaviors?.suggestions.expressions ?? [])
const motionSuggestions = computed(() => state.live2dBehaviors?.suggestions.motions ?? [])

function loadBehaviorForm() {
  const behaviors = state.live2dBehaviors?.audioPlaybackBehaviors
  if (!behaviors) return
  for (const key of Object.keys(behaviorForm)) delete behaviorForm[key]
  for (const [key, value] of Object.entries(behaviors)) {
    behaviorForm[key] = {
      emotion: value.emotion ?? '',
      expression: value.expression ?? '',
      motion: value.motion ?? '',
      intensity: typeof value.intensity === 'number' ? String(value.intensity) : '0.5',
    }
  }
}

watch(() => state.live2dBehaviors, loadBehaviorForm, { immediate: true })

async function runImport() {
  if (!importSourceDir.value.trim()) return
  importing.value = true
  importError.value = ''
  const result = await importLive2d(importSourceDir.value.trim(), {
    modelId: importModelId.value.trim() || undefined,
    activate: importActivate.value,
  })
  importing.value = false
  if ('error' in result) {
    importError.value = result.error
    return
  }
  importSourceDir.value = ''
  importModelId.value = ''
  flash(importFlash)
}

async function chooseModel(id: string) {
  await selectLive2d(id)
}

async function saveBehaviors() {
  behaviorSaving.value = true
  const payload: Record<string, Live2dBehavior> = {}
  for (const [key, value] of Object.entries(behaviorForm)) {
    const intensity = Number(value.intensity)
    payload[key] = {
      emotion: value.emotion.trim() || undefined,
      expression: value.expression.trim() || undefined,
      motion: value.motion.trim() || undefined,
      intensity: Number.isFinite(intensity) ? Math.min(1, Math.max(0, intensity)) : undefined,
    }
  }
  const ok = await saveLive2dBehaviors(payload)
  behaviorSaving.value = false
  if (ok) flash(behaviorFlash)
}

/* ---------------- MCP 诊断 ---------------- */
const mcpRefreshing = ref(false)

const mcpConfig = computed(() => state.toolsConfig?.mcp ?? null)
const mcpServers = computed(() => mcpConfig.value?.servers ?? [])
const allTools = computed(() => state.toolsConfig?.tools ?? [])
const effectiveTools = computed(() => state.effectiveTools?.tools ?? [])
const effectiveToolNames = computed(() => new Set(effectiveTools.value.map((tool) => tool.name)))
const roleScope = computed(() => state.activeRole?.runtimeScope ?? { tools: [], skills: [], mcpServers: [] })
const mcpToolCount = computed(() =>
  allTools.value.filter((tool) => tool.name.startsWith('mcp__')).length,
)
const effectiveMcpToolCount = computed(() =>
  effectiveTools.value.filter((tool) => tool.name.startsWith('mcp__')).length,
)
const mcpFailedCount = computed(() =>
  state.mcpAuditRecords.filter((record) => record.ok === false || record.failureCode).length,
)
const toolAuditFailedCount = computed(() =>
  state.toolAuditRecords.filter((record) => record.ok === false || record.failureCode || record.decision === 'failed').length,
)
const toolAuditDeniedCount = computed(() =>
  state.toolAuditRecords.filter((record) => record.decision === 'denied' || record.decision === 'blocked').length,
)
const averageToolDuration = computed(() => {
  const durations = state.toolAuditRecords
    .map((record) => record.durationMs)
    .filter((duration): duration is number => typeof duration === 'number')
  if (!durations.length) return 0
  return Math.round(durations.reduce((sum, duration) => sum + duration, 0) / durations.length)
})
const roleHiddenTools = computed(() =>
  allTools.value.filter((tool) => tool.enabled !== false && !effectiveToolNames.value.has(tool.name)),
)
const recentToolAuditRecords = computed(() => state.toolAuditRecords.slice(-30).reverse())
const recentMcpAuditRecords = computed(() => state.mcpAuditRecords.slice().reverse())

const decisionLabels: Record<string, string> = {
  started: '开始',
  finished: '完成',
  denied: '拒绝',
  blocked: '阻断',
  failed: '失败',
}

function normalizeMcpIdentifier(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function mcpToolPrefix(serverName: string): string {
  return `mcp__${normalizeMcpIdentifier(serverName)}__`
}

function toolsForMcpServer(serverName: string) {
  const prefix = mcpToolPrefix(serverName)
  return allTools.value.filter((tool) => tool.name.startsWith(prefix))
}

function effectiveToolsForMcpServer(serverName: string) {
  const prefix = mcpToolPrefix(serverName)
  return effectiveTools.value.filter((tool) => tool.name.startsWith(prefix))
}

function latestAuditForPrefix(prefix: string) {
  for (let index = state.toolAuditRecords.length - 1; index >= 0; index -= 1) {
    const record = state.toolAuditRecords[index]
    if (record.toolName.startsWith(prefix)) return record
  }
  return null
}

const mcpServerDiagnostics = computed(() =>
  mcpServers.value.map((server) => {
    const prefix = mcpToolPrefix(server.name)
    const discoveredTools = toolsForMcpServer(server.name)
    const visibleTools = effectiveToolsForMcpServer(server.name)
    const latestAudit = latestAuditForPrefix(prefix)
    const hiddenByRole = discoveredTools.length - visibleTools.length
    const hasScopeRestriction = roleScope.value.mcpServers.length > 0 || roleScope.value.tools.length > 0
    const tone: ToolTone = !server.enabled
      ? 'neutral'
      : discoveredTools.length === 0
        ? 'warning'
        : hiddenByRole === discoveredTools.length && hasScopeRestriction
          ? 'danger'
          : 'success'
    return {
      ...server,
      discoveredTools,
      visibleTools,
      hiddenByRole,
      latestAudit,
      hasScopeRestriction,
      tone,
    }
  }),
)

function auditTone(record: { decision: string; ok?: boolean; failureCode?: string }): ToolTone {
  if (record.ok === false || record.failureCode || record.decision === 'failed') return 'danger'
  if (record.decision === 'denied' || record.decision === 'blocked') return 'warning'
  if (record.ok === true || record.decision === 'finished') return 'success'
  return 'neutral'
}

function metadataPreview(metadata?: Record<string, unknown> | null): string {
  if (!metadata) return ''
  try {
    return JSON.stringify(metadata)
  } catch {
    return ''
  }
}

async function refreshMcp() {
  mcpRefreshing.value = true
  await refreshMcpDiagnostics()
  mcpRefreshing.value = false
}
</script>

<template>
  <section
    class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
           bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
  >
    <!-- header -->
    <div class="flex items-center gap-3 border-b border-line/70 px-6 py-4">
      <span class="grid size-10 place-items-center rounded-[var(--radius-xl2)] bg-brand-100/70 text-brand-500">
        <Icon icon="ph:faders-duotone" :width="20" />
      </span>
      <div class="flex-1">
        <p class="text-[15px] font-semibold text-ink">配置中心</p>
        <p class="text-xs text-ink-faint">模型密钥与参数、Live2D 形象与动作、语音合成引擎</p>
      </div>
      <AmTabs v-model="activeTab" :tabs="tabs" />
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto px-6 py-6">
      <div class="mx-auto w-full max-w-5xl space-y-7">
        <!-- ============ 模型 ============ -->
        <template v-if="activeTab === 'model'">
          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:cpu-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">模型服务商与连接</span>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">服务商</label>
                <AmSelect
                  :model-value="apiProvider"
                  :options="providerOptions"
                  placeholder="选择服务商"
                  @update:model-value="onApiProviderChange"
                />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">模型名称</label>
                <AmInput v-model="apiModel" icon="ph:cpu-duotone" :placeholder="activePreset?.defaultModel || '如 gpt-4o'" />
              </div>
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">Base URL</label>
              <AmInput v-model="apiBaseUrl" icon="ph:link-duotone" placeholder="如 https://api.openai.com/v1" />
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">Max Tokens（0 表示不限制）</label>
                <AmInput v-model="apiMaxTokens" type="number" icon="ph:gauge-duotone" placeholder="0" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">流式输出</label>
                <button
                  type="button"
                  class="flex h-10 w-full items-center justify-between rounded-[var(--radius-xl2)] border border-line bg-surface px-3
                         text-sm text-ink transition-colors hover:border-brand-200"
                  @click="apiStreaming = !apiStreaming"
                >
                  <span>{{ apiStreaming ? '已开启流式响应' : '关闭（等待整段返回）' }}</span>
                  <span
                    class="relative h-5 w-9 rounded-full transition-colors"
                    :class="apiStreaming ? 'bg-brand-500' : 'bg-line'"
                  >
                    <span
                      class="absolute top-0.5 size-4 rounded-full bg-white transition-all"
                      :class="apiStreaming ? 'left-4' : 'left-0.5'"
                    />
                  </span>
                </button>
              </div>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">思考模式</label>
                <button
                  type="button"
                  class="flex h-10 w-full items-center justify-between rounded-[var(--radius-xl2)] border border-line bg-surface px-3
                         text-sm text-ink transition-colors hover:border-brand-200"
                  @click="apiThinkingEnabled = !apiThinkingEnabled"
                >
                  <span>{{ apiThinkingEnabled ? '已开启思考模式' : '关闭' }}</span>
                  <span
                    class="relative h-5 w-9 rounded-full transition-colors"
                    :class="apiThinkingEnabled ? 'bg-brand-500' : 'bg-line'"
                  >
                    <span
                      class="absolute top-0.5 size-4 rounded-full bg-white transition-all"
                      :class="apiThinkingEnabled ? 'left-4' : 'left-0.5'"
                    />
                  </span>
                </button>
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">思考强度</label>
                <AmSelect
                  v-model="apiReasoningEffort"
                  :options="reasoningEffortOptions"
                  placeholder="选择强度"
                />
              </div>
            </div>
            <p class="-mt-2 text-[11px] leading-relaxed text-ink-faint">
              开启后会向 DeepSeek 发送 <code class="font-mono">thinking.enabled</code> 与
              <code class="font-mono">reasoning_effort</code>，Main UI 会折叠展示思考过程，Companion 不展示。
            </p>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">
                API Key
                <span v-if="activePreset" class="text-ink-faint">
                  · 环境变量 <code class="font-mono">{{ activePreset.envVar }}</code>
                </span>
              </label>
              <AmInput
                v-model="apiKey"
                type="password"
                icon="ph:key-duotone"
                :placeholder="
                  state.runtimeConfig?.api.apiKeyConfigured
                    ? `已配置（${state.runtimeConfig.api.apiKeyPreview}），留空则保持不变`
                    : '输入 API Key'
                "
              />
              <p v-if="activePreset && !activePreset.requiresApiKey" class="text-[11px] text-ink-faint">
                该服务商无需 API Key（本地部署）。
              </p>
            </div>
            <div class="flex items-center justify-end gap-3">
              <transition
                enter-active-class="transition duration-200"
                enter-from-class="opacity-0 translate-x-1"
                leave-active-class="transition duration-200"
                leave-to-class="opacity-0"
              >
                <span v-if="apiFlash" class="mr-auto inline-flex items-center gap-1.5 text-[13px] font-medium text-success">
                  <Icon icon="ph:check-circle-fill" :width="16" /> 已写入配置
                </span>
              </transition>
              <AmButton variant="primary" size="sm" icon="ph:check-bold" :loading="apiSaving" @click="saveModel">
                保存模型配置
              </AmButton>
            </div>
          </div>
          <p class="px-1 text-[11px] leading-relaxed text-ink-faint">
            密钥写入 <code class="font-mono">.env</code>，连接参数写入
            <code class="font-mono">configs/providers.yaml</code>，保存后立即热切换生效。
          </p>
        </template>

        <!-- ============ 记忆 Embedding ============ -->
        <template v-else-if="activeTab === 'memory'">
          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-2">
                <Icon icon="ph:brain-duotone" :width="18" class="text-brand-500" />
                <span class="text-sm font-semibold text-ink">BGE-M3 本地 Embedding</span>
              </div>
              <AmTag :tone="embeddingStatusTone" size="sm" dot>
                {{ embeddingStatusLabel }}
              </AmTag>
            </div>

            <p class="text-[11px] leading-relaxed text-ink-faint">
              本地 BGE-M3 用于后续 memory vector / hybrid retrieval。部署会写入
              <code class="font-mono">.env</code> 和 <code class="font-mono">configs/providers.yaml</code>，
              并在后台安装可选依赖、下载模型缓存；不会阻塞当前对话。
            </p>

            <div class="grid gap-3 sm:grid-cols-4">
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">配置</p>
                <div class="mt-2">
                  <AmTag :tone="embedding?.configured ? 'success' : 'neutral'" size="sm">
                    {{ embedding?.configured ? '已配置' : '未配置' }}
                  </AmTag>
                </div>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">依赖</p>
                <div class="mt-2">
                  <AmTag :tone="embedding?.dependenciesInstalled ? 'success' : 'warning'" size="sm">
                    {{ embedding?.dependenciesInstalled ? '已安装' : '待安装' }}
                  </AmTag>
                </div>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">模型缓存</p>
                <div class="mt-2">
                  <AmTag :tone="embedding?.modelInstalled ? 'success' : 'warning'" size="sm">
                    {{ embedding?.modelInstalled ? '已下载' : '待下载' }}
                  </AmTag>
                </div>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">维度</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ embedding?.dimensions ?? 1024 }}</p>
              </div>
            </div>

            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">本地模型目录</label>
              <AmInput
                v-model="embeddingLocalDir"
                icon="ph:folder-open-duotone"
                :disabled="embeddingDeploymentActive"
                placeholder="默认 models/embeddings/bge-m3"
              />
              <p class="text-[11px] text-ink-faint">
                当前模型：<code class="font-mono">{{ embedding?.modelId || 'BAAI/bge-m3' }}</code>
                <span v-if="state.embeddingConfig?.paths.defaultModelDir">
                  · 默认目录 <code class="font-mono">{{ state.embeddingConfig.paths.defaultModelDir }}</code>
                </span>
              </p>
            </div>

            <div class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/40 p-3">
              <div class="flex flex-wrap items-center gap-2">
                <AmTag :tone="embeddingDeploymentActive ? 'info' : embeddingStatusTone" size="sm">
                  {{ embeddingPhaseLabel }}
                </AmTag>
                <span class="text-[11px] text-ink-faint">
                  {{ embeddingDeployment?.message || '等待部署操作' }}
                </span>
              </div>
              <p v-if="embeddingDeployment?.startedAt" class="mt-2 text-[11px] text-ink-faint">
                开始：{{ shortDateTime(embeddingDeployment.startedAt) }}
                <span v-if="embeddingDeployment.finishedAt">
                  · 结束：{{ shortDateTime(embeddingDeployment.finishedAt) }}
                </span>
              </p>
              <p v-if="embeddingDeployment?.error" class="mt-2 rounded-[var(--radius-xl2)] bg-danger/10 p-2 text-[11px] text-danger">
                {{ embeddingDeployment.error }}
              </p>
            </div>

            <div v-if="embeddingDependencyRows.length" class="rounded-[var(--radius-xl2)] bg-surface-muted/50 p-3">
              <p class="text-[11px] font-semibold text-ink-soft">可选依赖</p>
              <div class="mt-2 flex flex-wrap gap-1.5">
                <AmTag
                  v-for="row in embeddingDependencyRows"
                  :key="row.name"
                  :tone="row.installed ? 'success' : 'warning'"
                  size="sm"
                >
                  {{ row.name }}
                </AmTag>
              </div>
              <p v-if="embedding?.dependencyInstallCommand" class="mt-2 break-all font-mono text-[10px] text-ink-faint">
                {{ embedding.dependencyInstallCommand }}
              </p>
            </div>

            <p v-if="embeddingError" class="rounded-[var(--radius-xl2)] bg-danger/10 p-3 text-xs text-danger">
              {{ embeddingError }}
            </p>

            <div class="flex flex-wrap items-center justify-end gap-3">
              <transition
                enter-active-class="transition duration-200"
                enter-from-class="opacity-0 translate-x-1"
                leave-active-class="transition duration-200"
                leave-to-class="opacity-0"
              >
                <span v-if="embeddingFlash" class="mr-auto inline-flex items-center gap-1.5 text-[13px] font-medium text-success">
                  <Icon icon="ph:check-circle-fill" :width="16" /> 部署任务已提交
                </span>
              </transition>
              <AmButton
                variant="ghost"
                size="sm"
                icon="ph:arrow-clockwise-bold"
                :loading="embeddingRefreshing"
                @click="refreshEmbedding"
              >
                刷新
              </AmButton>
              <AmButton
                v-if="embeddingDeploymentActive"
                variant="danger"
                size="sm"
                icon="ph:x-bold"
                :loading="embeddingCancelling"
                @click="cancelEmbeddingDeployAction"
              >
                取消部署
              </AmButton>
              <AmButton
                v-else
                variant="secondary"
                size="sm"
                icon="ph:download-simple-bold"
                :loading="embeddingDeploying"
                :disabled="!embeddingLocalDir.trim()"
                @click="runEmbeddingDeploy(false)"
              >
                {{ embedding?.deployed ? '重新检查部署' : '安装并部署' }}
              </AmButton>
              <AmButton
                v-if="!embeddingDeploymentActive"
                variant="primary"
                size="sm"
                icon="ph:arrow-counter-clockwise-bold"
                :loading="embeddingDeploying"
                :disabled="!embeddingLocalDir.trim()"
                @click="runEmbeddingDeploy(true)"
              >
                强制重装
              </AmButton>
            </div>
          </div>

          <p class="px-1 text-[11px] leading-relaxed text-ink-faint">
            当前只做本地 BGE-M3 的资源部署与配置观测，后续 hybrid retrieval 会复用这个 provider 边界接入
            memory vector index。
          </p>
        </template>

        <!-- ============ Live2D 形象 ============ -->
        <template v-else-if="activeTab === 'live2d'">
          <!-- model list -->
          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:user-focus-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">本地模型</span>
            </div>
            <div v-if="state.live2dModels.length" class="grid gap-3 sm:grid-cols-2">
              <button
                v-for="m in state.live2dModels"
                :key="m.id"
                type="button"
                class="flex items-center gap-3 rounded-[var(--radius-xl3)] border p-3.5 text-left transition-all duration-200
                       hover:-translate-y-0.5"
                :class="
                  m.active
                    ? 'border-brand-300 bg-gradient-to-br from-brand-50 to-surface shadow-[var(--shadow-glow)]'
                    : 'border-line bg-surface hover:border-brand-200 hover:shadow-[var(--shadow-soft)]'
                "
                @click="chooseModel(m.id)"
              >
                <span
                  class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)]"
                  :class="m.active ? 'bg-brand-500 text-white' : 'bg-surface-muted text-ink-faint'"
                >
                  <Icon icon="ph:person-simple-duotone" :width="20" />
                </span>
                <div class="min-w-0 flex-1">
                  <p class="truncate text-[13px] font-semibold text-ink">{{ m.id }}</p>
                  <p class="truncate text-[11px] text-ink-faint">{{ m.path }}</p>
                </div>
                <Icon v-if="m.active" icon="ph:check-circle-fill" :width="18" class="shrink-0 text-brand-500" />
              </button>
            </div>
            <p v-else class="rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
              未检测到本地 Live2D 模型，可通过下方导入。
            </p>
          </div>

          <!-- import -->
          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:download-simple-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">导入新模型</span>
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">模型文件夹路径</label>
              <AmInput
                v-model="importSourceDir"
                icon="ph:folder-open-duotone"
                placeholder="包含 *.model3.json 的本地目录绝对路径"
              />
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">模型 ID（可选）</label>
                <AmInput v-model="importModelId" icon="ph:tag-duotone" placeholder="留空则用文件夹名" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">导入后启用</label>
                <button
                  type="button"
                  class="flex h-10 w-full items-center justify-between rounded-[var(--radius-xl2)] border border-line bg-surface px-3
                         text-sm text-ink transition-colors hover:border-brand-200"
                  @click="importActivate = !importActivate"
                >
                  <span>{{ importActivate ? '导入后立即启用' : '仅导入，不启用' }}</span>
                  <span
                    class="relative h-5 w-9 rounded-full transition-colors"
                    :class="importActivate ? 'bg-brand-500' : 'bg-line'"
                  >
                    <span class="absolute top-0.5 size-4 rounded-full bg-white transition-all" :class="importActivate ? 'left-4' : 'left-0.5'" />
                  </span>
                </button>
              </div>
            </div>
            <p v-if="importError" class="rounded-[var(--radius-xl2)] bg-danger/10 p-3 text-xs text-danger">
              {{ importError }}
            </p>
            <div class="flex items-center justify-end gap-3">
              <transition
                enter-active-class="transition duration-200"
                enter-from-class="opacity-0 translate-x-1"
                leave-active-class="transition duration-200"
                leave-to-class="opacity-0"
              >
                <span v-if="importFlash" class="mr-auto inline-flex items-center gap-1.5 text-[13px] font-medium text-success">
                  <Icon icon="ph:check-circle-fill" :width="16" /> 已导入
                </span>
              </transition>
              <AmButton
                variant="primary"
                size="sm"
                icon="ph:download-simple-bold"
                :loading="importing"
                :disabled="!importSourceDir.trim()"
                @click="runImport"
              >
                扫描并导入
              </AmButton>
            </div>
          </div>

          <!-- behaviors -->
          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:hand-waving-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">语音播放动作</span>
            </div>
            <p class="text-[11px] leading-relaxed text-ink-faint">
              定义语音播放不同阶段时，Live2D 形象触发的表情与动作。
              <template v-if="expressionSuggestions.length || motionSuggestions.length">
                当前模型可用表情：{{ expressionSuggestions.join('、') || '—' }}；动作：{{ motionSuggestions.join('、') || '—' }}。
              </template>
            </p>
            <div
              v-for="s in behaviorStates"
              :key="s.id"
              class="space-y-3 rounded-[var(--radius-xl2)] bg-surface-muted/50 p-4"
            >
              <p class="text-xs font-semibold text-ink-soft">{{ s.label }}</p>
              <div v-if="behaviorForm[s.id]" class="grid gap-3 sm:grid-cols-2">
                <div class="space-y-1.5">
                  <label class="text-[11px] text-ink-faint">情绪 emotion</label>
                  <AmInput v-model="behaviorForm[s.id].emotion" placeholder="如 happy / neutral" />
                </div>
                <div class="space-y-1.5">
                  <label class="text-[11px] text-ink-faint">表情 expression</label>
                  <AmInput v-model="behaviorForm[s.id].expression" placeholder="如 smile" />
                </div>
                <div class="space-y-1.5">
                  <label class="text-[11px] text-ink-faint">动作 motion</label>
                  <AmInput v-model="behaviorForm[s.id].motion" placeholder="如 talk / idle" />
                </div>
                <div class="space-y-1.5">
                  <label class="text-[11px] text-ink-faint">强度 intensity（0–1）</label>
                  <AmInput v-model="behaviorForm[s.id].intensity" type="number" placeholder="0.5" />
                </div>
              </div>
            </div>
            <div class="flex items-center justify-end gap-3">
              <transition
                enter-active-class="transition duration-200"
                enter-from-class="opacity-0 translate-x-1"
                leave-active-class="transition duration-200"
                leave-to-class="opacity-0"
              >
                <span v-if="behaviorFlash" class="mr-auto inline-flex items-center gap-1.5 text-[13px] font-medium text-success">
                  <Icon icon="ph:check-circle-fill" :width="16" /> 已保存动作
                </span>
              </transition>
              <AmButton
                variant="primary"
                size="sm"
                icon="ph:check-bold"
                :loading="behaviorSaving"
                :disabled="!behaviorStates.length"
                @click="saveBehaviors"
              >
                保存动作配置
              </AmButton>
            </div>
          </div>
        </template>

        <!-- ============ 语音 TTS ============ -->
        <template v-else-if="activeTab === 'voice'">
          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:waveform-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">语音合成引擎</span>
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">
                TTS 引擎
                <span v-if="state.audioConfig" class="text-ink-faint">
                  （当前生效：{{ state.audioConfig.runtimeProvider }}）
                </span>
              </label>
              <AmSelect v-model="ttsProvider" :options="ttsProviderOptions" placeholder="选择 TTS 引擎" />
            </div>
          </div>

          <div v-if="showMacos" class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:speaker-high-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">macOS 系统语音</span>
              <span
                v-if="state.audioConfig && !state.audioConfig.macosAvailable"
                class="rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-medium text-warning"
              >
                当前系统不可用
              </span>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">音色</label>
                <AmSelect v-model="macosVoice" :options="voiceOptions" placeholder="系统默认音色" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">语速（词/分钟，可留空）</label>
                <AmInput v-model="macosRate" type="number" icon="ph:gauge-duotone" placeholder="如 180" />
              </div>
            </div>
          </div>

          <div v-if="showGptSovits" class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:microphone-stage-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">GPT-SoVITS</span>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">服务地址 Base URL</label>
                <AmInput v-model="gpt.baseUrl" icon="ph:link-duotone" placeholder="如 http://127.0.0.1:9880" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">合成接口路径</label>
                <AmInput v-model="gpt.endpoint" icon="ph:path-duotone" placeholder="/tts" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">文本语言</label>
                <AmInput v-model="gpt.textLang" placeholder="auto / zh / ja / en" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">参考音频语言</label>
                <AmInput v-model="gpt.promptLang" placeholder="auto / zh / ja / en" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">参考音频路径</label>
                <AmInput v-model="gpt.refAudioPath" icon="ph:file-audio-duotone" placeholder="参考音频文件路径" />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-medium text-ink-soft">超时（秒）</label>
                <AmInput v-model="gpt.timeoutSeconds" type="number" placeholder="60" />
              </div>
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">参考音频提示文本</label>
              <AmInput v-model="gpt.promptText" placeholder="参考音频对应的文字内容" />
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">流式合成</label>
              <button
                type="button"
                class="flex h-10 w-full items-center justify-between rounded-[var(--radius-xl2)] border border-line bg-surface px-3
                       text-sm text-ink transition-colors hover:border-brand-200"
                @click="gpt.streamingMode = !gpt.streamingMode"
              >
                <span>{{ gpt.streamingMode ? '已开启流式合成' : '关闭' }}</span>
                <span
                  class="relative h-5 w-9 rounded-full transition-colors"
                  :class="gpt.streamingMode ? 'bg-brand-500' : 'bg-line'"
                >
                  <span class="absolute top-0.5 size-4 rounded-full bg-white transition-all" :class="gpt.streamingMode ? 'left-4' : 'left-0.5'" />
                </span>
              </button>
            </div>
          </div>

          <div class="flex items-center justify-end gap-3">
            <transition
              enter-active-class="transition duration-200"
              enter-from-class="opacity-0 translate-x-1"
              leave-active-class="transition duration-200"
              leave-to-class="opacity-0"
            >
              <span v-if="ttsFlash" class="mr-auto inline-flex items-center gap-1.5 text-[13px] font-medium text-success">
                <Icon icon="ph:check-circle-fill" :width="16" /> 已写入并热切换
              </span>
            </transition>
            <AmButton variant="primary" size="sm" icon="ph:check-bold" :loading="ttsSaving" @click="saveVoice">
              保存语音配置
            </AmButton>
          </div>
          <p class="px-1 text-[11px] leading-relaxed text-ink-faint">
            引擎与参数写入 <code class="font-mono">.env</code> 与
            <code class="font-mono">configs/providers.yaml</code>，保存后立即重建 TTS Provider。
          </p>
        </template>

        <!-- ============ MCP 诊断 ============ -->
        <template v-else-if="activeTab === 'mcp'">
          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-start justify-between gap-3">
              <div class="flex items-center gap-2">
                <Icon icon="ph:plugs-connected-duotone" :width="18" class="text-brand-500" />
                <span class="text-sm font-semibold text-ink">MCP 运行状态</span>
              </div>
              <AmButton
                variant="secondary"
                size="sm"
                icon="ph:arrow-clockwise-bold"
                :loading="mcpRefreshing"
                @click="refreshMcp"
              >
                刷新诊断
              </AmButton>
            </div>

            <div class="grid gap-3 sm:grid-cols-4">
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">全局状态</p>
                <div class="mt-2">
                  <AmTag :tone="mcpConfig?.enabled ? 'success' : 'neutral'" dot>
                    {{ mcpConfig?.enabled ? '已启用' : '未启用' }}
                  </AmTag>
                </div>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">默认权限</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ mcpConfig?.permission ?? 'ask' }}</p>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">已发现工具</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ mcpToolCount }}</p>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">当前角色可见</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ effectiveMcpToolCount }}</p>
              </div>
            </div>

            <p class="text-[11px] leading-relaxed text-ink-faint">
              这里展示的是全局 MCP discovery 与当前会话角色过滤后的有效工具差异。当前不扩展 stdio/SSE；
              优先把 HTTP JSON-RPC MCP 的发现、权限、耗时、失败和 role scope 过滤结果看清楚。
            </p>
          </div>

          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:funnel-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">当前角色过滤结果</span>
            </div>
            <div class="grid gap-3 sm:grid-cols-3">
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">Tools allowlist</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ roleScope.tools.length || '全局' }}</p>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">MCP server allowlist</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ roleScope.mcpServers.length || '全局' }}</p>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">被过滤工具</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ roleHiddenTools.length }}</p>
              </div>
            </div>
            <div v-if="roleHiddenTools.length" class="rounded-[var(--radius-xl2)] bg-warning-soft/45 p-3">
              <p class="text-[11px] font-semibold text-[#b9791a]">当前角色不可见的工具</p>
              <div class="mt-2 flex flex-wrap gap-1.5">
                <AmTag
                  v-for="tool in roleHiddenTools.slice(0, 16)"
                  :key="tool.name"
                  tone="warning"
                  size="sm"
                >
                  {{ tool.name }}
                </AmTag>
                <AmTag v-if="roleHiddenTools.length > 16" tone="neutral" size="sm">
                  +{{ roleHiddenTools.length - 16 }}
                </AmTag>
              </div>
            </div>
            <p v-else class="rounded-[var(--radius-xl2)] bg-success-soft/45 p-3 text-xs text-success">
              当前角色没有额外过滤全局启用工具。
            </p>
          </div>

          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center gap-2">
              <Icon icon="ph:server-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">MCP Server Discovery</span>
            </div>
            <div v-if="mcpServerDiagnostics.length" class="space-y-2">
              <div
                v-for="server in mcpServerDiagnostics"
                :key="server.name"
                class="flex items-start gap-3 rounded-[var(--radius-xl2)] border border-line bg-surface-muted/40 p-3"
              >
                <span
                  class="mt-0.5 grid size-8 shrink-0 place-items-center rounded-[var(--radius-xl2)]"
                  :class="server.enabled ? 'bg-success-soft text-success' : 'bg-surface-muted text-ink-faint'"
                >
                  <Icon icon="ph:server-duotone" :width="16" />
                </span>
                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <span class="text-[13px] font-semibold text-ink">{{ server.name }}</span>
                    <AmTag :tone="server.enabled ? 'success' : 'neutral'" size="sm">
                      {{ server.enabled ? '启用' : '停用' }}
                    </AmTag>
                    <AmTag tone="info" size="sm">{{ server.permission }}</AmTag>
                    <AmTag :tone="server.tone" size="sm" dot>
                      {{ server.discoveredTools.length }} discovered / {{ server.visibleTools.length }} visible
                    </AmTag>
                  </div>
                  <p class="mt-1 truncate font-mono text-[11px] text-ink-faint">{{ server.url }}</p>
                  <p class="mt-1 text-[11px] text-ink-faint">
                    超时 {{ server.timeoutSeconds }}s
                    <span v-if="server.hiddenByRole > 0"> · role scope 过滤 {{ server.hiddenByRole }} 个工具</span>
                    <span v-if="server.latestAudit">
                      · 最近调用 {{ decisionLabels[server.latestAudit.decision] ?? server.latestAudit.decision }}
                      <template v-if="server.latestAudit.durationMs !== undefined">
                        / {{ server.latestAudit.durationMs }}ms
                      </template>
                      <template v-if="server.latestAudit.failureCode">
                        / {{ server.latestAudit.failureCode }}
                      </template>
                    </span>
                  </p>
                  <div v-if="server.discoveredTools.length" class="mt-2 flex flex-wrap gap-1.5">
                    <AmTag
                      v-for="tool in server.discoveredTools.slice(0, 8)"
                      :key="tool.name"
                      :tone="effectiveToolNames.has(tool.name) ? 'success' : 'neutral'"
                      size="sm"
                    >
                      {{ tool.name.replace(mcpToolPrefix(server.name), '') }}
                    </AmTag>
                    <AmTag v-if="server.discoveredTools.length > 8" tone="neutral" size="sm">
                      +{{ server.discoveredTools.length - 8 }}
                    </AmTag>
                  </div>
                </div>
              </div>
            </div>
            <p v-else class="rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
              当前没有配置 MCP server。
            </p>
          </div>

          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <Icon icon="ph:list-checks-duotone" :width="18" class="text-brand-500" />
                <span class="text-sm font-semibold text-ink">ToolRuntime 审计</span>
              </div>
              <AmTag :tone="toolAuditFailedCount || toolAuditDeniedCount ? 'warning' : 'success'" size="sm" dot>
                {{ toolAuditFailedCount }} 失败 · {{ toolAuditDeniedCount }} 权限/阻断
              </AmTag>
            </div>

            <div class="grid gap-3 sm:grid-cols-4">
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">审计记录</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ state.toolAuditRecords.length }}</p>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">平均耗时</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ averageToolDuration }}ms</p>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">MCP 异常</p>
                <p class="mt-2 text-lg font-semibold text-ink">{{ mcpFailedCount }}</p>
              </div>
              <div class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3">
                <p class="text-[11px] font-medium text-ink-faint">当前 Session</p>
                <p class="mt-2 truncate text-sm font-semibold text-ink">{{ state.activeSessionId }}</p>
              </div>
            </div>

            <div v-if="recentToolAuditRecords.length" class="space-y-2">
              <div
                v-for="record in recentToolAuditRecords"
                :key="record.recordId"
                class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/40 p-3"
              >
                <div class="flex items-center justify-between gap-3">
                  <p class="min-w-0 truncate font-mono text-[12px] font-semibold text-ink">
                    {{ record.toolName }}
                  </p>
                  <AmTag
                    :tone="auditTone(record)"
                    size="sm"
                  >
                    {{ record.failureCode || decisionLabels[record.decision] || record.decision }}
                  </AmTag>
                </div>
                <p class="mt-1 text-[11px] text-ink-faint">
                  {{ shortDateTime(record.timestamp) }}
                  <span v-if="record.durationMs !== undefined"> · {{ record.durationMs }}ms</span>
                  <span> · {{ record.sessionId }}</span>
                  <span> · {{ record.toolName.startsWith('mcp__') ? 'MCP' : 'local' }}</span>
                </p>
                <p v-if="record.detail" class="mt-1 line-clamp-2 text-[11px] text-ink-faint">
                  {{ record.detail }}
                </p>
                <p v-if="metadataPreview(record.metadata)" class="mt-1 line-clamp-2 font-mono text-[10px] text-ink-faint">
                  {{ metadataPreview(record.metadata) }}
                </p>
              </div>
            </div>
            <p v-else class="rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
              当前会话还没有 ToolRuntime 审计记录。执行一次工具后，这里会显示权限判定、耗时、失败原因和 metadata。
            </p>
          </div>

          <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
            <div class="flex items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <Icon icon="ph:plugs-connected-duotone" :width="18" class="text-brand-500" />
                <span class="text-sm font-semibold text-ink">最近 MCP 调用</span>
              </div>
              <AmTag :tone="mcpFailedCount ? 'warning' : 'success'" size="sm" dot>
                {{ mcpFailedCount ? `${mcpFailedCount} 条异常` : '无异常' }}
              </AmTag>
            </div>
            <div v-if="recentMcpAuditRecords.length" class="space-y-2">
              <div
                v-for="record in recentMcpAuditRecords"
                :key="`mcp-${record.recordId}`"
                class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/40 p-3"
              >
                <div class="flex items-center justify-between gap-3">
                  <p class="min-w-0 truncate font-mono text-[12px] font-semibold text-ink">
                    {{ record.toolName }}
                  </p>
                  <AmTag :tone="auditTone(record)" size="sm">
                    {{ record.failureCode || decisionLabels[record.decision] || record.decision }}
                  </AmTag>
                </div>
                <p class="mt-1 text-[11px] text-ink-faint">
                  {{ shortDateTime(record.timestamp) }}
                  <span v-if="record.durationMs !== undefined"> · {{ record.durationMs }}ms</span>
                  <span> · {{ record.sessionId }}</span>
                </p>
                <p v-if="record.detail" class="mt-1 line-clamp-2 text-[11px] text-ink-faint">
                  {{ record.detail }}
                </p>
              </div>
            </div>
            <p v-else class="rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
              当前会话还没有 MCP 工具审计记录。
            </p>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>
