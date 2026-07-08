<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmInput from '@/components/ui/AmInput.vue'
import AmSelect from '@/components/ui/AmSelect.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'
import AmTag from '@/components/ui/AmTag.vue'

const { state, updateRole } = useRuntime()

interface ScopeOption {
  id: string
  label: string
  detail: string
  tone: 'brand' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  available: boolean
}

const editRoleId = ref('')
const roleName = ref('')
const provider = ref('')
const model = ref('')
const persona = ref('')
const style = ref('')
const live2dModel = ref('')
const ttsVoice = ref('')
const runtimeTools = ref<string[]>([])
const runtimeSkills = ref<string[]>([])
const runtimeMcpServers = ref<string[]>([])
const toolSearch = ref('')
const skillSearch = ref('')
const mcpSearch = ref('')
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
  runtimeTools.value = normalizeScopeList(role.runtimeScope?.tools)
  runtimeSkills.value = normalizeScopeList(role.runtimeScope?.skills)
  runtimeMcpServers.value = normalizeScopeList(role.runtimeScope?.mcpServers)
}

function normalizeScopeList(items?: string[]) {
  const seen = new Set<string>()
  return (items ?? [])
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

function buildSelectedAwareOptions(inventory: ScopeOption[], selected: string[]) {
  const byId = new Map(inventory.map((option) => [option.id, option]))
  const unknownSelected = selected
    .filter((id) => !byId.has(id))
    .map((id) => ({
      id,
      label: id,
      detail: '已保存，但当前 inventory 未发现',
      tone: 'warning' as const,
      available: false,
    }))
  return [...inventory, ...unknownSelected]
}

function filterScopeOptions(options: ScopeOption[], query: string) {
  const normalized = query.trim().toLowerCase()
  if (!normalized) return options
  return options.filter((option) =>
    `${option.id} ${option.label} ${option.detail}`.toLowerCase().includes(normalized),
  )
}

function toggleScopeList(items: string[], id: string) {
  return items.includes(id)
    ? items.filter((item) => item !== id)
    : [...items, id]
}

function toggleTool(id: string) {
  runtimeTools.value = toggleScopeList(runtimeTools.value, id)
}

function toggleSkill(id: string) {
  runtimeSkills.value = toggleScopeList(runtimeSkills.value, id)
}

function toggleMcpServer(id: string) {
  runtimeMcpServers.value = toggleScopeList(runtimeMcpServers.value, id)
}

function clearTools() {
  runtimeTools.value = []
}

function clearSkills() {
  runtimeSkills.value = []
}

function clearMcpServers() {
  runtimeMcpServers.value = []
}

function selectedOptions(options: ScopeOption[], selected: string[]) {
  const byId = new Map(options.map((option) => [option.id, option]))
  return selected.map((id) => byId.get(id) ?? {
    id,
    label: id,
    detail: '已保存，但当前 inventory 未发现',
    tone: 'warning' as const,
    available: false,
  })
}

const toolOptions = computed(() =>
  buildSelectedAwareOptions(
    (state.toolsConfig?.tools ?? []).map((tool) => ({
      id: tool.name,
      label: tool.displayName || tool.name,
      detail: `${tool.name}${tool.permission ? ` · ${tool.permission}` : ''}${tool.enabled === false ? ' · 全局停用' : ''}`,
      tone: tool.enabled === false ? 'neutral' : tool.permission === 'ask' ? 'warning' : 'success',
      available: tool.enabled !== false,
    })),
    runtimeTools.value,
  ),
)

const skillOptions = computed(() =>
  buildSelectedAwareOptions(
    state.skills.map((skill) => ({
      id: skill.id,
      label: skill.name,
      detail: `${skill.category}${skill.summary ? ` · ${skill.summary}` : ''}`,
      tone: 'brand',
      available: true,
    })),
    runtimeSkills.value,
  ),
)

const mcpServerOptions = computed(() =>
  buildSelectedAwareOptions(
    (state.toolsConfig?.mcp?.servers ?? []).map((server) => ({
      id: server.name,
      label: server.name,
      detail: `${server.url}${server.permission ? ` · ${server.permission}` : ''}${server.enabled ? '' : ' · 停用'}`,
      tone: server.enabled ? 'info' : 'neutral',
      available: server.enabled,
    })),
    runtimeMcpServers.value,
  ),
)

const visibleToolOptions = computed(() => filterScopeOptions(toolOptions.value, toolSearch.value))
const visibleSkillOptions = computed(() => filterScopeOptions(skillOptions.value, skillSearch.value))
const visibleMcpServerOptions = computed(() => filterScopeOptions(mcpServerOptions.value, mcpSearch.value))
const selectedToolOptions = computed(() => selectedOptions(toolOptions.value, runtimeTools.value))
const selectedSkillOptions = computed(() => selectedOptions(skillOptions.value, runtimeSkills.value))
const selectedMcpServerOptions = computed(() => selectedOptions(mcpServerOptions.value, runtimeMcpServers.value))

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
    !sameList(runtimeTools.value, role.runtimeScope?.tools) ||
    !sameList(runtimeSkills.value, role.runtimeScope?.skills) ||
    !sameList(runtimeMcpServers.value, role.runtimeScope?.mcpServers)
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
      tools: runtimeTools.value,
      skills: runtimeSkills.value,
      mcpServers: runtimeMcpServers.value,
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
            这些选择只对当前角色生效，用来收窄每轮注入的工具、Skills 和 MCP server。每组留空表示跟随全局可用集合。
          </p>
          <div class="grid gap-4">
            <div class="space-y-3 rounded-[var(--radius-xl2)] border border-line bg-surface-muted/35 p-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p class="text-xs font-semibold text-ink">Tools</p>
                  <p class="text-[11px] text-ink-faint">
                    {{ runtimeTools.length ? `仅注入 ${runtimeTools.length} 个工具` : '不限制工具，使用全局启用集合' }}
                  </p>
                </div>
                <AmButton
                  variant="ghost"
                  size="sm"
                  :disabled="!runtimeTools.length"
                  @click="clearTools"
                >
                  清空
                </AmButton>
              </div>
              <AmInput v-model="toolSearch" icon="ph:magnifying-glass-duotone" clearable placeholder="搜索工具名称、权限或显示名" />
              <div v-if="selectedToolOptions.length" class="flex flex-wrap gap-2">
                <button
                  v-for="option in selectedToolOptions"
                  :key="`selected-tool-${option.id}`"
                  type="button"
                  class="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-700 ring-1 ring-brand-100 transition-colors hover:bg-danger-soft hover:text-danger hover:ring-danger/15"
                  @click="toggleTool(option.id)"
                >
                  {{ option.label }}
                  <Icon icon="ph:x-bold" :width="11" />
                </button>
              </div>
              <p v-else class="rounded-[var(--radius-xl2)] bg-white/55 p-2 text-[11px] text-ink-faint">
                当前未选择工具，角色会看到全局启用的工具集合。
              </p>
              <div class="max-h-56 space-y-2 overflow-y-auto pr-1">
                <button
                  v-for="option in visibleToolOptions"
                  :key="option.id"
                  type="button"
                  class="flex w-full items-start gap-3 rounded-[var(--radius-xl2)] border p-3 text-left transition-all duration-150"
                  :class="[
                    runtimeTools.includes(option.id)
                      ? 'border-brand-200 bg-brand-50'
                      : 'border-white/70 bg-white/55 hover:border-brand-200 hover:bg-brand-50/70',
                    !option.available && !runtimeTools.includes(option.id) ? 'cursor-not-allowed opacity-55' : '',
                  ]"
                  :disabled="!option.available && !runtimeTools.includes(option.id)"
                  @click="toggleTool(option.id)"
                >
                  <span
                    class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border"
                    :class="runtimeTools.includes(option.id) ? 'border-brand-500 bg-brand-500 text-white' : 'border-line text-transparent'"
                  >
                    <Icon icon="ph:check-bold" :width="12" />
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="flex flex-wrap items-center gap-2">
                      <span class="truncate text-[13px] font-semibold text-ink">{{ option.label }}</span>
                      <AmTag :tone="option.tone" size="sm">{{ option.available ? '可用' : '不可用' }}</AmTag>
                    </span>
                    <span class="mt-1 block truncate font-mono text-[11px] text-ink-faint">{{ option.detail }}</span>
                  </span>
                </button>
                <p v-if="!visibleToolOptions.length" class="rounded-[var(--radius-xl2)] bg-white/55 p-3 text-xs text-ink-faint">
                  没有匹配的工具。
                </p>
              </div>
            </div>

            <div class="space-y-3 rounded-[var(--radius-xl2)] border border-line bg-surface-muted/35 p-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p class="text-xs font-semibold text-ink">Skills</p>
                  <p class="text-[11px] text-ink-faint">
                    {{ runtimeSkills.length ? `仅注入 ${runtimeSkills.length} 个技能` : '不限制技能，使用全局技能目录' }}
                  </p>
                </div>
                <AmButton
                  variant="ghost"
                  size="sm"
                  :disabled="!runtimeSkills.length"
                  @click="clearSkills"
                >
                  清空
                </AmButton>
              </div>
              <AmInput v-model="skillSearch" icon="ph:magnifying-glass-duotone" clearable placeholder="搜索技能名称、类别或描述" />
              <div v-if="selectedSkillOptions.length" class="flex flex-wrap gap-2">
                <button
                  v-for="option in selectedSkillOptions"
                  :key="`selected-skill-${option.id}`"
                  type="button"
                  class="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-700 ring-1 ring-brand-100 transition-colors hover:bg-danger-soft hover:text-danger hover:ring-danger/15"
                  @click="toggleSkill(option.id)"
                >
                  {{ option.label }}
                  <Icon icon="ph:x-bold" :width="11" />
                </button>
              </div>
              <p v-else class="rounded-[var(--radius-xl2)] bg-white/55 p-2 text-[11px] text-ink-faint">
                当前未选择技能，角色会看到全局技能目录。
              </p>
              <div class="max-h-56 space-y-2 overflow-y-auto pr-1">
                <button
                  v-for="option in visibleSkillOptions"
                  :key="option.id"
                  type="button"
                  class="flex w-full items-start gap-3 rounded-[var(--radius-xl2)] border p-3 text-left transition-all duration-150"
                  :class="[
                    runtimeSkills.includes(option.id)
                      ? 'border-brand-200 bg-brand-50'
                      : 'border-white/70 bg-white/55 hover:border-brand-200 hover:bg-brand-50/70',
                    !option.available && !runtimeSkills.includes(option.id) ? 'cursor-not-allowed opacity-55' : '',
                  ]"
                  :disabled="!option.available && !runtimeSkills.includes(option.id)"
                  @click="toggleSkill(option.id)"
                >
                  <span
                    class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border"
                    :class="runtimeSkills.includes(option.id) ? 'border-brand-500 bg-brand-500 text-white' : 'border-line text-transparent'"
                  >
                    <Icon icon="ph:check-bold" :width="12" />
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="flex flex-wrap items-center gap-2">
                      <span class="truncate text-[13px] font-semibold text-ink">{{ option.label }}</span>
                      <AmTag :tone="option.tone" size="sm">{{ option.available ? '可用' : '未发现' }}</AmTag>
                    </span>
                    <span class="mt-1 block truncate text-[11px] text-ink-faint">{{ option.detail }}</span>
                  </span>
                </button>
                <p v-if="!visibleSkillOptions.length" class="rounded-[var(--radius-xl2)] bg-white/55 p-3 text-xs text-ink-faint">
                  没有匹配的技能。
                </p>
              </div>
            </div>

            <div class="space-y-3 rounded-[var(--radius-xl2)] border border-line bg-surface-muted/35 p-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p class="text-xs font-semibold text-ink">MCP Servers</p>
                  <p class="text-[11px] text-ink-faint">
                    {{ runtimeMcpServers.length ? `仅允许 ${runtimeMcpServers.length} 个 MCP server` : '不限制 MCP server，使用全局 MCP 配置' }}
                  </p>
                </div>
                <AmButton
                  variant="ghost"
                  size="sm"
                  :disabled="!runtimeMcpServers.length"
                  @click="clearMcpServers"
                >
                  清空
                </AmButton>
              </div>
              <AmInput v-model="mcpSearch" icon="ph:magnifying-glass-duotone" clearable placeholder="搜索 MCP server 名称、URL 或权限" />
              <div v-if="selectedMcpServerOptions.length" class="flex flex-wrap gap-2">
                <button
                  v-for="option in selectedMcpServerOptions"
                  :key="`selected-mcp-${option.id}`"
                  type="button"
                  class="inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-700 ring-1 ring-brand-100 transition-colors hover:bg-danger-soft hover:text-danger hover:ring-danger/15"
                  @click="toggleMcpServer(option.id)"
                >
                  {{ option.label }}
                  <Icon icon="ph:x-bold" :width="11" />
                </button>
              </div>
              <p v-else class="rounded-[var(--radius-xl2)] bg-white/55 p-2 text-[11px] text-ink-faint">
                当前未选择 MCP server，角色会看到全局启用的 MCP 工具。
              </p>
              <div class="max-h-56 space-y-2 overflow-y-auto pr-1">
                <button
                  v-for="option in visibleMcpServerOptions"
                  :key="option.id"
                  type="button"
                  class="flex w-full items-start gap-3 rounded-[var(--radius-xl2)] border p-3 text-left transition-all duration-150"
                  :class="[
                    runtimeMcpServers.includes(option.id)
                      ? 'border-brand-200 bg-brand-50'
                      : 'border-white/70 bg-white/55 hover:border-brand-200 hover:bg-brand-50/70',
                    !option.available && !runtimeMcpServers.includes(option.id) ? 'cursor-not-allowed opacity-55' : '',
                  ]"
                  :disabled="!option.available && !runtimeMcpServers.includes(option.id)"
                  @click="toggleMcpServer(option.id)"
                >
                  <span
                    class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border"
                    :class="runtimeMcpServers.includes(option.id) ? 'border-brand-500 bg-brand-500 text-white' : 'border-line text-transparent'"
                  >
                    <Icon icon="ph:check-bold" :width="12" />
                  </span>
                  <span class="min-w-0 flex-1">
                    <span class="flex flex-wrap items-center gap-2">
                      <span class="truncate text-[13px] font-semibold text-ink">{{ option.label }}</span>
                      <AmTag :tone="option.tone" size="sm">{{ option.available ? '启用' : '停用/未发现' }}</AmTag>
                    </span>
                    <span class="mt-1 block truncate font-mono text-[11px] text-ink-faint">{{ option.detail }}</span>
                  </span>
                </button>
                <p v-if="!visibleMcpServerOptions.length" class="rounded-[var(--radius-xl2)] bg-white/55 p-3 text-xs text-ink-faint">
                  没有匹配的 MCP server。
                </p>
              </div>
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
