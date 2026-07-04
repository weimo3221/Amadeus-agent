<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmInput from '@/components/ui/AmInput.vue'
import AmSelect from '@/components/ui/AmSelect.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmTabs from '@/components/ui/AmTabs.vue'
import type { Live2dBehavior } from '@/runtime/http'

interface BehaviorFormEntry {
  emotion: string
  expression: string
  motion: string
  intensity: string
}

const { state, saveApiConfig, saveAudioConfig, saveLive2dBehaviors, importLive2d, selectLive2d } =
  useRuntime()

const activeTab = ref('model')

const tabs = [
  { value: 'model', label: '模型', icon: 'ph:cpu-duotone' },
  { value: 'live2d', label: '形象', icon: 'ph:sparkle-duotone' },
  { value: 'voice', label: '语音', icon: 'ph:waveform-duotone' },
]

function flash(target: { value: boolean }) {
  target.value = true
  setTimeout(() => (target.value = false), 2200)
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
      <div class="mx-auto max-w-2xl space-y-7">
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
        <template v-else>
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
      </div>
    </div>
  </section>
</template>
