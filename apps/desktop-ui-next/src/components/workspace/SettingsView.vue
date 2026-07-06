<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmInput from '@/components/ui/AmInput.vue'
import AmSelect from '@/components/ui/AmSelect.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'

const { state, updateRole } = useRuntime()

const editRoleId = ref('')
const roleName = ref('')
const provider = ref('')
const model = ref('')
const persona = ref('')
const style = ref('')
const live2dModel = ref('')
const ttsVoice = ref('')
const runtimeTools = ref('')
const runtimeSkills = ref('')
const runtimeMcpServers = ref('')
const saving = ref(false)
const savedFlash = ref(false)

const selectedRole = computed(() => state.roles.find((r) => r.id === editRoleId.value) ?? null)

const providerOptions = computed(() =>
  state.providerPresets.map((p) => ({ label: p.label, value: p.id })),
)

const activePreset = computed(() =>
  state.providerPresets.find((p) => p.id === provider.value) ?? null,
)

const live2dOptions = computed(() => [
  { label: '不使用 Live2D', value: '' },
  ...state.live2dModels.map((m) => ({ label: m.id, value: m.id })),
])

const ttsOptions = computed(() => [
  { label: '跟随服务商默认', value: '' },
  ...state.ttsVoices.map((v) => ({
    label: v.locale ? `${v.label} · ${v.locale}` : v.label,
    value: v.id,
  })),
])

function loadForm(id: string) {
  const role = state.roles.find((r) => r.id === id)
  if (!role) return
  roleName.value = role.name
  provider.value = role.provider || ''
  model.value = role.model
  persona.value = role.persona
  style.value = role.style
  live2dModel.value = role.live2dModel
  ttsVoice.value = role.ttsVoice
  runtimeTools.value = formatScopeList(role.runtimeScope?.tools)
  runtimeSkills.value = formatScopeList(role.runtimeScope?.skills)
  runtimeMcpServers.value = formatScopeList(role.runtimeScope?.mcpServers)
}

function formatScopeList(items?: string[]) {
  return (items ?? []).join('\n')
}

function parseScopeList(value: string) {
  const seen = new Set<string>()
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item || seen.has(item)) return false
      seen.add(item)
      return true
    })
}

function sameList(left: string[], right?: string[]) {
  const normalizedRight = right ?? []
  return left.length === normalizedRight.length && left.every((item, index) => item === normalizedRight[index])
}

watch(
  () => [state.roles, state.activeRole] as const,
  () => {
    if (!editRoleId.value) {
      editRoleId.value = state.activeRole?.id ?? state.roles[0]?.id ?? ''
      if (editRoleId.value) loadForm(editRoleId.value)
    }
  },
  { immediate: true, deep: true },
)

function selectRole(id: string) {
  if (id === editRoleId.value) return
  editRoleId.value = id
  loadForm(id)
  savedFlash.value = false
}

function onProviderChange(value: string) {
  provider.value = value
  const preset = state.providerPresets.find((p) => p.id === value)
  if (preset && !model.value.trim()) {
    model.value = preset.defaultModel
  }
}

const dirty = computed(() => {
  const role = selectedRole.value
  if (!role) return false
  return (
    roleName.value.trim() !== role.name ||
    provider.value !== role.provider ||
    model.value.trim() !== role.model ||
    persona.value !== role.persona ||
    style.value !== role.style ||
    live2dModel.value !== role.live2dModel ||
    ttsVoice.value.trim() !== role.ttsVoice ||
    !sameList(parseScopeList(runtimeTools.value), role.runtimeScope?.tools) ||
    !sameList(parseScopeList(runtimeSkills.value), role.runtimeScope?.skills) ||
    !sameList(parseScopeList(runtimeMcpServers.value), role.runtimeScope?.mcpServers)
  )
})

async function save() {
  const role = selectedRole.value
  if (!role || !dirty.value) return
  saving.value = true
  const ok = await updateRole(role.id, {
    name: roleName.value.trim() || role.name,
    provider: provider.value,
    model: model.value.trim(),
    persona: persona.value,
    style: style.value,
    live2dModel: live2dModel.value,
    ttsVoice: ttsVoice.value.trim(),
    runtimeScope: {
      tools: parseScopeList(runtimeTools.value),
      skills: parseScopeList(runtimeSkills.value),
      mcpServers: parseScopeList(runtimeMcpServers.value),
    },
  })
  saving.value = false
  if (ok) {
    savedFlash.value = true
    setTimeout(() => (savedFlash.value = false), 2200)
  }
}

function reset() {
  if (editRoleId.value) loadForm(editRoleId.value)
  savedFlash.value = false
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
        <Icon icon="ph:sliders-horizontal-duotone" :width="20" />
      </span>
      <div>
        <p class="text-[15px] font-semibold text-ink">设置</p>
        <p class="text-xs text-ink-faint">配置角色人设、模型服务商、形象与语音</p>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto px-6 py-6">
      <AmEmptyState
        v-if="!state.roles.length"
        icon="ph:user-circle-duotone"
        title="暂无可配置的角色"
        description="连接运行时后，角色配置会在这里显示"
      />

      <div v-else class="mx-auto max-w-2xl space-y-7">
        <!-- role picker -->
        <div>
          <p class="mb-2.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">选择角色</p>
          <div class="grid gap-3 sm:grid-cols-2">
            <button
              v-for="role in state.roles"
              :key="role.id"
              type="button"
              class="group flex items-center gap-3 rounded-[var(--radius-xl3)] border p-3.5 text-left
                     transition-all duration-200 ease-[var(--ease-soft)] hover:-translate-y-0.5"
              :class="
                role.id === editRoleId
                  ? 'border-brand-300 bg-gradient-to-br from-brand-50 to-surface shadow-[var(--shadow-glow)]'
                  : 'border-line bg-surface hover:border-brand-200 hover:shadow-[var(--shadow-soft)]'
              "
              @click="selectRole(role.id)"
            >
              <span
                class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)]"
                :class="role.id === editRoleId ? 'bg-brand-500 text-white' : 'bg-surface-muted text-ink-faint'"
              >
                <Icon icon="ph:cat-duotone" :width="20" />
              </span>
              <div class="min-w-0 flex-1">
                <p class="truncate text-[13px] font-semibold text-ink">{{ role.name }}</p>
                <p class="truncate text-[11px] text-ink-faint">
                  {{ role.model || '未配置模型' }}
                </p>
              </div>
              <Icon
                v-if="role.id === editRoleId"
                icon="ph:check-circle-fill"
                :width="18"
                class="shrink-0 text-brand-500"
              />
            </button>
          </div>
        </div>

        <!-- identity -->
        <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
          <div class="flex items-center gap-2">
            <Icon icon="ph:identification-badge-duotone" :width="18" class="text-brand-500" />
            <span class="text-sm font-semibold text-ink">身份</span>
          </div>
          <div class="space-y-1.5">
            <label class="text-xs font-medium text-ink-soft">角色名称</label>
            <AmInput v-model="roleName" icon="ph:user-circle-duotone" placeholder="给你的智能体起个名字" />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs font-medium text-ink-soft">人设</label>
            <textarea
              v-model="persona"
              rows="4"
              placeholder="描述角色的性格、背景与行为设定…"
              class="w-full resize-y rounded-[var(--radius-xl2)] border border-line bg-surface px-3 py-2.5 text-sm leading-relaxed text-ink outline-none
                     transition-all duration-200 ease-[var(--ease-soft)] placeholder:text-ink-faint
                     hover:border-brand-200 hover:shadow-[var(--shadow-soft)]
                     focus:border-brand-300 focus:shadow-[var(--shadow-glow)]"
            />
          </div>
          <div class="space-y-1.5">
            <label class="text-xs font-medium text-ink-soft">语气风格</label>
            <AmInput v-model="style" icon="ph:chat-teardrop-text-duotone" placeholder="如：温柔、简洁、俏皮" />
          </div>
        </div>

        <!-- model -->
        <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
          <div class="flex items-center gap-2">
            <Icon icon="ph:cpu-duotone" :width="18" class="text-brand-500" />
            <span class="text-sm font-semibold text-ink">模型</span>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">模型服务商</label>
              <AmSelect
                :model-value="provider"
                :options="providerOptions"
                placeholder="选择服务商"
                @update:model-value="onProviderChange"
              />
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">模型名称</label>
              <AmInput
                v-model="model"
                icon="ph:cpu-duotone"
                :placeholder="activePreset?.defaultModel || '如 gpt-4o'"
              />
            </div>
          </div>
          <p
            v-if="activePreset"
            class="rounded-[var(--radius-xl2)] bg-brand-50/60 p-3 text-xs leading-relaxed text-brand-700"
          >
            {{ activePreset.label }} · 默认模型 {{ activePreset.defaultModel }} ·
            密钥环境变量 <code class="font-mono">{{ activePreset.envVar }}</code>
          </p>
          <p v-else class="rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs leading-relaxed text-ink-faint">
            选择服务商后会带出其默认模型与所需密钥说明。
          </p>
        </div>

        <!-- runtime scope -->
        <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
          <div class="flex items-center gap-2">
            <Icon icon="ph:funnel-duotone" :width="18" class="text-brand-500" />
            <span class="text-sm font-semibold text-ink">上下文范围</span>
          </div>
          <p class="rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs leading-relaxed text-ink-soft">
            这些列表只对当前角色生效，用来收窄每轮注入的工具、Skills 和 MCP。留空表示跟随全局可用集合。
          </p>
          <div class="grid gap-4 lg:grid-cols-3">
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">Tools</label>
              <textarea
                v-model="runtimeTools"
                rows="5"
                placeholder="get_current_time&#10;read_file"
                class="w-full resize-y rounded-[var(--radius-xl2)] border border-line bg-surface px-3 py-2.5 font-mono text-xs leading-relaxed text-ink outline-none
                       transition-all duration-200 ease-[var(--ease-soft)] placeholder:text-ink-faint
                       hover:border-brand-200 hover:shadow-[var(--shadow-soft)]
                       focus:border-brand-300 focus:shadow-[var(--shadow-glow)]"
              />
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">Skills</label>
              <textarea
                v-model="runtimeSkills"
                rows="5"
                placeholder="development/runtime-debug"
                class="w-full resize-y rounded-[var(--radius-xl2)] border border-line bg-surface px-3 py-2.5 font-mono text-xs leading-relaxed text-ink outline-none
                       transition-all duration-200 ease-[var(--ease-soft)] placeholder:text-ink-faint
                       hover:border-brand-200 hover:shadow-[var(--shadow-soft)]
                       focus:border-brand-300 focus:shadow-[var(--shadow-glow)]"
              />
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">MCP Servers</label>
              <textarea
                v-model="runtimeMcpServers"
                rows="5"
                placeholder="hermes-fixture"
                class="w-full resize-y rounded-[var(--radius-xl2)] border border-line bg-surface px-3 py-2.5 font-mono text-xs leading-relaxed text-ink outline-none
                       transition-all duration-200 ease-[var(--ease-soft)] placeholder:text-ink-faint
                       hover:border-brand-200 hover:shadow-[var(--shadow-soft)]
                       focus:border-brand-300 focus:shadow-[var(--shadow-glow)]"
              />
            </div>
          </div>
        </div>

        <!-- appearance & voice -->
        <div class="space-y-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-5">
          <div class="flex items-center gap-2">
            <Icon icon="ph:sparkle-duotone" :width="18" class="text-brand-500" />
            <span class="text-sm font-semibold text-ink">形象与语音</span>
          </div>
          <div class="grid gap-4 sm:grid-cols-2">
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">Live2D 形象</label>
              <AmSelect v-model="live2dModel" :options="live2dOptions" placeholder="不使用 Live2D" />
              <p v-if="!state.live2dModels.length" class="text-[11px] text-ink-faint">
                未检测到本地 Live2D 模型，可将模型放入 models/live2d/ 目录。
              </p>
            </div>
            <div class="space-y-1.5">
              <label class="text-xs font-medium text-ink-soft">
                语音音色
                <span v-if="state.ttsProvider && state.ttsProvider !== 'none'" class="text-ink-faint">
                  （{{ state.ttsProvider }}）
                </span>
              </label>
              <AmSelect
                v-if="state.ttsSupportsEnumeration && state.ttsVoices.length"
                v-model="ttsVoice"
                :options="ttsOptions"
                placeholder="跟随服务商默认"
              />
              <AmInput
                v-else
                v-model="ttsVoice"
                icon="ph:waveform-duotone"
                placeholder="留空为默认音色"
              />
            </div>
          </div>
          <p class="rounded-[var(--radius-xl2)] bg-brand-50/60 p-3 text-xs leading-relaxed text-brand-700">
            保存后立即写入该角色配置，桌面伴侣将使用对应形象与音色。
          </p>
        </div>

        <!-- actions -->
        <div class="flex items-center justify-end gap-3 pb-2">
          <transition
            enter-active-class="transition duration-200"
            enter-from-class="opacity-0 translate-x-1"
            leave-active-class="transition duration-200"
            leave-to-class="opacity-0"
          >
            <span
              v-if="savedFlash"
              class="mr-auto inline-flex items-center gap-1.5 text-[13px] font-medium text-success"
            >
              <Icon icon="ph:check-circle-fill" :width="16" />
              已保存
            </span>
          </transition>
          <AmButton variant="ghost" size="sm" :disabled="!dirty || saving" @click="reset">重置</AmButton>
          <AmButton
            variant="primary"
            size="sm"
            icon="ph:check-bold"
            :loading="saving"
            :disabled="!dirty"
            @click="save"
          >
            保存修改
          </AmButton>
        </div>
      </div>
    </div>
  </section>
</template>
