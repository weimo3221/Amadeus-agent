<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import ChatMessageBubble from '@/components/workspace/ChatMessage.vue'
import ChatComposer from '@/components/workspace/ChatComposer.vue'
import AmTag from '@/components/ui/AmTag.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'

const { state, sendMessage, respondPermission, selectCompanionSession } = useRuntime()

const emit = defineEmits<{
  navigate: [key: string]
}>()

const chatLog = ref<HTMLElement | null>(null)

const runningCount = computed(() => state.tasks.filter((t) => t.status === 'running').length)
const blockedCount = computed(() => state.tasks.filter((t) => t.status === 'blocked').length)
const pendingPermissionLabel = computed(() =>
  state.toolPermission ? `等待授权：${state.toolPermission.displayName}` : '无待授权工具',
)
const latestMemoryDiagnostic = computed(() =>
  state.memoryContextDiagnostics[state.memoryContextDiagnostics.length - 1] ?? null,
)
const memorySourceCount = computed(() => {
  const diagnostic = latestMemoryDiagnostic.value
  return diagnostic?.sources?.length ?? 0
})
const mcpConfig = computed(() => state.toolsConfig?.mcp ?? null)
const mcpServerCount = computed(() => mcpConfig.value?.servers?.filter((server) => server.enabled).length ?? 0)
const mcpToolCount = computed(() =>
  (state.toolsConfig?.tools ?? []).filter((tool) => tool.name.startsWith('mcp__')).length,
)
const roleScopeSummary = computed(() => {
  const scope = state.activeRole?.runtimeScope
  const tools = scope?.tools?.length ?? 0
  const skills = scope?.skills?.length ?? 0
  const mcpServers = scope?.mcpServers?.length ?? 0
  if (!tools && !skills && !mcpServers) return '跟随全局可用集合'
  return `${tools} 工具 · ${skills} 技能 · ${mcpServers} MCP`
})
const connectionLabel = computed(() => {
  if (state.connection === 'online') return '在线 · 实时已连接'
  if (state.connection === 'connecting') return '连接中…'
  return '离线 · 正在重连'
})

async function scrollToBottom() {
  await nextTick()
  if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight
}

watch(() => state.chat, scrollToBottom, { deep: true })

function handleSend(text: string) {
  sendMessage(text)
}

scrollToBottom()
</script>

<template>
  <div class="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_380px]">
    <!-- ===== Chat panel ===== -->
    <section
      class="relative flex min-h-0 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
             bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
    >
      <div
        class="pointer-events-none absolute -right-16 -top-16 size-56 rounded-full blur-3xl"
        style="background: radial-gradient(circle, rgba(183,166,255,0.35), transparent 70%)"
      />

      <!-- chat header -->
      <div class="border-b border-line/70 px-5 py-4">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <div class="grid size-11 place-items-center rounded-[var(--radius-xl2)] bg-gradient-to-br from-blush-soft to-brand-300 text-white shadow-[var(--shadow-soft)]">
              <Icon icon="ph:cat-duotone" :width="22" />
            </div>
            <div>
              <p class="text-[15px] font-semibold text-ink">{{ state.roleName }}</p>
              <p class="flex items-center gap-1.5 text-xs text-ink-faint">
                <span
                  class="inline-flex size-1.5 rounded-full"
                  :class="state.connection === 'online' ? 'bg-success' : state.connection === 'connecting' ? 'bg-warning' : 'bg-ink-faint'"
                />
                {{ connectionLabel }}
              </p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <AmTag tone="brand" size="sm">
              <Icon icon="ph:sparkle-fill" :width="12" />
              {{ state.skills.length }} 项技能
            </AmTag>
            <button
              type="button"
              title="更多"
              class="grid size-8 place-items-center rounded-full text-ink-faint transition-all duration-200 hover:bg-surface-muted hover:text-ink"
            >
              <Icon icon="ph:dots-three-bold" :width="18" />
            </button>
          </div>
        </div>
      </div>

      <!-- chat log -->
      <div ref="chatLog" class="flex-1 space-y-5 overflow-y-auto px-5 py-5">
        <AmEmptyState
          v-if="!state.chat.length"
          icon="ph:chat-circle-dots-duotone"
          title="开始新的对话"
          description="发送一条消息，与你的桌面智能体开始协作。"
        />
        <ChatMessageBubble v-for="m in state.chat" :key="m.id" :message="m" />
      </div>

      <!-- tool permission prompt -->
      <div v-if="state.toolPermission" class="border-t border-line/70 px-5 py-3">
        <div class="flex items-start gap-3 rounded-[var(--radius-xl3)] border border-warning/30 bg-warning-soft/60 p-3">
          <Icon icon="ph:shield-warning-duotone" :width="20" class="mt-0.5 shrink-0 text-[#b9791a]" />
          <div class="min-w-0 flex-1">
            <p class="text-[13px] font-semibold text-ink">
              请求使用工具：{{ state.toolPermission.displayName }}
            </p>
            <p class="mt-0.5 text-xs text-ink-soft">{{ state.toolPermission.reason }}</p>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <AmButton variant="ghost" size="sm" @click="respondPermission(false)">拒绝</AmButton>
            <AmButton variant="primary" size="sm" icon="ph:check-bold" @click="respondPermission(true)">允许</AmButton>
          </div>
        </div>
      </div>

      <!-- composer -->
      <div class="border-t border-line/70 px-5 py-4">
        <ChatComposer :suggested-skill-count="state.suggestedSkillIds.length" @send="handleSend" />
      </div>
    </section>

    <!-- ===== Right rail (overview only) ===== -->
    <aside class="flex min-h-0 flex-col gap-4">
      <div
        class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
               bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
      >
        <div class="flex items-center gap-2 border-b border-line/70 px-4 py-3.5">
          <Icon icon="ph:squares-four-duotone" :width="18" class="text-brand-500" />
          <span class="text-sm font-semibold text-ink">概览</span>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto p-4">
          <div class="space-y-4">
            <div>
              <p class="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">运行状态</p>
              <div class="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  class="flex items-center gap-3 rounded-[var(--radius-xl3)] border border-white/70 bg-gradient-to-br from-brand-50 to-surface p-3 text-left shadow-[var(--shadow-soft)] transition-transform duration-200 hover:-translate-y-0.5"
                  @click="emit('navigate', 'chat')"
                >
                  <span class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)] bg-brand-100/70 text-brand-500">
                    <Icon icon="ph:chats-circle-duotone" :width="20" />
                  </span>
                  <div class="min-w-0">
                    <p class="text-[11px] font-medium uppercase tracking-wide text-ink-faint">Messages</p>
                    <p class="truncate text-[13px] font-semibold text-ink">{{ state.chat.length }} 条对话</p>
                  </div>
                </button>
                <button
                  type="button"
                  class="flex items-center gap-3 rounded-[var(--radius-xl3)] border border-white/70 bg-gradient-to-br from-info-soft to-surface p-3 text-left shadow-[var(--shadow-soft)] transition-transform duration-200 hover:-translate-y-0.5"
                  @click="emit('navigate', 'tasks')"
                >
                  <span class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)] bg-info/10 text-info">
                    <Icon icon="ph:list-checks-duotone" :width="20" />
                  </span>
                  <div class="min-w-0">
                    <p class="text-[11px] font-medium uppercase tracking-wide text-ink-faint">Tasks</p>
                    <p class="truncate text-[13px] font-semibold text-ink">{{ state.tasks.length }} 个活跃</p>
                  </div>
                </button>
                <button
                  type="button"
                  class="flex items-center gap-3 rounded-[var(--radius-xl3)] border border-white/70 bg-gradient-to-br from-success-soft to-surface p-3 text-left shadow-[var(--shadow-soft)] transition-transform duration-200 hover:-translate-y-0.5"
                  @click="emit('navigate', 'skills')"
                >
                  <span class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)] bg-success/10 text-success">
                    <Icon icon="ph:sparkle-duotone" :width="20" />
                  </span>
                  <div class="min-w-0">
                    <p class="text-[11px] font-medium uppercase tracking-wide text-ink-faint">Skills</p>
                    <p class="truncate text-[13px] font-semibold text-ink">{{ state.skills.length }} 个可用</p>
                  </div>
                </button>
                <button
                  type="button"
                  class="flex items-center gap-3 rounded-[var(--radius-xl3)] border border-white/70 bg-gradient-to-br from-warning-soft to-surface p-3 text-left shadow-[var(--shadow-soft)] transition-transform duration-200 hover:-translate-y-0.5"
                  @click="emit('navigate', 'schedule')"
                >
                  <span class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)] bg-warning/10 text-[#b9791a]">
                    <Icon icon="ph:alarm-duotone" :width="20" />
                  </span>
                  <div class="min-w-0">
                    <p class="text-[11px] font-medium uppercase tracking-wide text-ink-faint">Scheduled</p>
                    <p class="truncate text-[13px] font-semibold text-ink">{{ state.scheduledCount }} 个定时</p>
                  </div>
                </button>
              </div>
            </div>
            <div class="rounded-[var(--radius-xl3)] border border-line bg-surface-muted/50 p-4">
              <div class="flex items-center gap-2">
                <Icon icon="ph:activity-duotone" :width="18" class="text-brand-500" />
                <span class="text-sm font-semibold text-ink">实时活动</span>
              </div>
              <ul class="mt-3 space-y-2.5 text-[13px] text-ink-soft">
                <li class="flex items-start gap-2">
                  <span
                    class="mt-1.5 size-1.5 shrink-0 rounded-full"
                    :class="state.connection === 'online' ? 'bg-success' : 'bg-warning'"
                  />
                  {{ connectionLabel }}
                </li>
                <li class="flex items-start gap-2">
                  <span class="mt-1.5 size-1.5 shrink-0 rounded-full bg-brand-400" />
                  正在运行 {{ runningCount }} 个任务。
                </li>
                <li class="flex items-start gap-2">
                  <span class="mt-1.5 size-1.5 shrink-0 rounded-full bg-info" />
                  当前会话共 {{ state.chat.length }} 条消息。
                </li>
              </ul>
            </div>
            <div class="rounded-[var(--radius-xl3)] border border-line bg-surface-muted/50 p-4">
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <Icon icon="ph:radar-duotone" :width="18" class="text-brand-500" />
                  <span class="text-sm font-semibold text-ink">工作流状态</span>
                </div>
                <AmTag :tone="state.sessionContext.viewingCompanion ? 'brand' : 'neutral'" size="sm" dot>
                  {{ state.sessionContext.viewingCompanion ? '已附着 Companion' : '独立会话' }}
                </AmTag>
              </div>

              <div class="mt-3 space-y-2">
                <div
                  class="w-full rounded-[var(--radius-xl2)] border border-white/70 bg-white/55 p-3 text-left"
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-ink">
                      <Icon icon="ph:sparkle-duotone" :width="16" class="shrink-0 text-brand-500" />
                      <span class="truncate">Companion 会话</span>
                    </span>
                    <button
                      v-if="!state.sessionContext.viewingCompanion"
                      type="button"
                      class="shrink-0 rounded-full bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-700 transition-colors hover:bg-brand-100"
                      @click.stop="selectCompanionSession"
                    >
                      附着
                    </button>
                  </div>
                  <p class="mt-1 text-[11px] leading-relaxed text-ink-faint">
                    {{ state.sessionContext.viewingCompanion
                      ? `Main UI 正在查看桌面 Companion 的默认会话，${state.sessionContext.companionMessageCount} 条消息。`
                      : `当前查看 ${state.sessionContext.activeTitle}；需要共享桌面伴随窗口上下文时，可直接附着到 ${state.sessionContext.companionTitle}。` }}
                  </p>
                </div>

                <button
                  type="button"
                  class="w-full rounded-[var(--radius-xl2)] border border-white/70 bg-white/55 p-3 text-left transition-colors hover:border-brand-200 hover:bg-brand-50"
                  @click="emit('navigate', 'tasks')"
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-ink">
                      <Icon icon="ph:list-checks-duotone" :width="16" class="shrink-0 text-info" />
                      <span class="truncate">任务执行</span>
                    </span>
                    <AmTag :tone="blockedCount ? 'warning' : runningCount ? 'info' : 'neutral'" size="sm" dot>
                      {{ runningCount }} 运行 · {{ blockedCount }} 阻塞
                    </AmTag>
                  </div>
                  <p class="mt-1 text-[11px] leading-relaxed text-ink-faint">
                    任务详情、时间线、取消、重跑、审核通过与恢复都集中在任务页。
                  </p>
                </button>

                <button
                  type="button"
                  class="w-full rounded-[var(--radius-xl2)] border border-white/70 bg-white/55 p-3 text-left transition-colors hover:border-brand-200 hover:bg-brand-50"
                  @click="state.toolPermission ? emit('navigate', 'chat') : emit('navigate', 'config')"
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-ink">
                      <Icon icon="ph:shield-check-duotone" :width="16" class="shrink-0 text-warning" />
                      <span class="truncate">权限与 MCP</span>
                    </span>
                    <AmTag :tone="state.toolPermission ? 'warning' : mcpConfig?.enabled ? 'success' : 'neutral'" size="sm" dot>
                      {{ state.toolPermission ? '等待确认' : mcpConfig?.enabled ? `${mcpToolCount} 工具` : '未启用' }}
                    </AmTag>
                  </div>
                  <p class="mt-1 text-[11px] leading-relaxed text-ink-faint">
                    {{ pendingPermissionLabel }}；{{ mcpServerCount }} 个 MCP server 已启用，调用审计在配置中心查看。
                  </p>
                </button>

                <button
                  type="button"
                  class="w-full rounded-[var(--radius-xl2)] border border-white/70 bg-white/55 p-3 text-left transition-colors hover:border-brand-200 hover:bg-brand-50"
                  @click="emit('navigate', 'memory')"
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-ink">
                      <Icon icon="ph:brain-duotone" :width="16" class="shrink-0 text-brand-500" />
                      <span class="truncate">记忆上下文</span>
                    </span>
                    <AmTag tone="brand" size="sm">{{ state.memoryItems.length }} 条事实</AmTag>
                  </div>
                  <p class="mt-1 text-[11px] leading-relaxed text-ink-faint">
                    最近一次上下文装配使用 {{ memorySourceCount }} 个来源；记忆事实与诊断集中在记忆页。
                  </p>
                </button>

                <button
                  type="button"
                  class="w-full rounded-[var(--radius-xl2)] border border-white/70 bg-white/55 p-3 text-left transition-colors hover:border-brand-200 hover:bg-brand-50"
                  @click="emit('navigate', 'settings')"
                >
                  <div class="flex items-center justify-between gap-3">
                    <span class="flex min-w-0 items-center gap-2 text-[13px] font-semibold text-ink">
                      <Icon icon="ph:funnel-duotone" :width="16" class="shrink-0 text-success" />
                      <span class="truncate">角色上下文范围</span>
                    </span>
                    <AmTag tone="success" size="sm">runtimeScope</AmTag>
                  </div>
                  <p class="mt-1 text-[11px] leading-relaxed text-ink-faint">
                    当前角色：{{ roleScopeSummary }}。在设置页收窄每轮可见工具、Skills 和 MCP server。
                  </p>
                </button>
              </div>
            </div>
            <div
              v-if="state.taskNotifications.length"
              class="rounded-[var(--radius-xl3)] border border-line bg-surface-muted/50 p-4"
            >
              <div class="flex items-center justify-between gap-2">
                <div class="flex items-center gap-2">
                  <Icon icon="ph:bell-ringing-duotone" :width="18" class="text-warning" />
                  <span class="text-sm font-semibold text-ink">任务通知</span>
                </div>
                <button
                  type="button"
                  class="text-[11px] font-medium text-brand-600 hover:underline"
                  @click="emit('navigate', 'tasks')"
                >
                  查看全部
                </button>
              </div>
              <div class="mt-3 space-y-2">
                <button
                  v-for="notice in state.taskNotifications"
                  :key="notice.id"
                  type="button"
                  class="w-full rounded-[var(--radius-xl2)] border border-white/70 bg-white/55 p-2 text-left transition-colors hover:border-brand-200 hover:bg-brand-50"
                  @click="emit('navigate', 'tasks')"
                >
                  <div class="flex items-center justify-between gap-2">
                    <AmTag :tone="notice.tone" size="sm" dot>{{ notice.status }}</AmTag>
                    <span class="text-[10px] text-ink-faint">{{ notice.createdAt }}</span>
                  </div>
                  <p class="mt-1 line-clamp-1 text-xs font-medium text-ink">{{ notice.title }}</p>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>
  </div>
</template>
