<script setup lang="ts">
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import type { ToolTone } from '@/types'
import AmButton from '@/components/ui/AmButton.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'
import AmTag from '@/components/ui/AmTag.vue'

const { state, refreshRuntimeObservability } = useRuntime()

const refreshing = ref(false)
const snapshot = computed(() => state.runtimeObservability)
const summary = computed(() => snapshot.value?.summary)

const summaryCards = computed(() => [
  {
    key: 'health',
    label: 'Runtime',
    value: healthLabel(summary.value?.healthStatus),
    detail: `健康检查 ${Object.keys(snapshot.value?.health.checks ?? {}).length} 项`,
    icon: 'ph:pulse-duotone',
    tone: healthTone(summary.value?.healthStatus),
  },
  {
    key: 'tasks',
    label: '任务图',
    value: String(summary.value?.activeTaskCount ?? 0),
    detail: `${summary.value?.blockedTaskCount ?? 0} 个 blocked`,
    icon: 'ph:graph-duotone',
    tone: (summary.value?.blockedTaskCount ?? 0) > 0 ? 'warning' : 'success',
  },
  {
    key: 'tools',
    label: '工具/MCP',
    value: String(summary.value?.toolFailureCount ?? 0),
    detail: `${summary.value?.effectiveToolCount ?? 0} tools · ${summary.value?.mcpToolCount ?? 0} MCP tools`,
    icon: 'ph:wrench-duotone',
    tone: (summary.value?.toolFailureCount ?? 0) > 0 ? 'warning' : 'success',
  },
  {
    key: 'memory',
    label: 'Memory',
    value: String(summary.value?.memoryDiagnosticCount ?? 0),
    detail: memorySourceLabel(summary.value?.memorySourceCounts ?? {}),
    icon: 'ph:brain-duotone',
    tone: (summary.value?.memoryDiagnosticCount ?? 0) > 0 ? 'info' : 'neutral',
  },
] satisfies Array<{
  key: string
  label: string
  value: string
  detail: string
  icon: string
  tone: ToolTone
}>)

const latestTask = computed(() => snapshot.value?.tasks.latest ?? null)
const latestGraph = computed(() => snapshot.value?.tasks.latestGraph ?? null)
const latestMemoryDiagnostic = computed(() => snapshot.value?.memory.latestDiagnostic ?? null)
const memorySourceEntries = computed(() => Object.entries(snapshot.value?.memory.sourceCounts ?? {}))
const toolDecisionEntries = computed(() => Object.entries(snapshot.value?.tools.audit.decisionCounts ?? {}))
const recentFailures = computed(() => {
  const records = [
    ...(snapshot.value?.tools.audit.recentFailures ?? []),
    ...(snapshot.value?.mcp.recentFailures ?? []),
  ]
  const byId = new Map(records.map((record) => [record.recordId, record]))
  return [...byId.values()]
    .sort((left, right) => String(right.timestamp).localeCompare(String(left.timestamp)))
    .slice(0, 8)
})

const roleScopeSummary = computed(() => {
  const scope = snapshot.value?.skills.roleScope
  if (!scope) return '跟随全局可用集合'
  const parts = [
    scope.tools.length ? `${scope.tools.length} tools` : '',
    scope.skills.length ? `${scope.skills.length} skills` : '',
    scope.mcpServers.length ? `${scope.mcpServers.length} MCP servers` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : '跟随全局可用集合'
})

function healthTone(status?: string): ToolTone {
  if (status === 'ok') return 'success'
  if (status === 'degraded') return 'warning'
  if (status === 'error') return 'danger'
  if (status === 'disabled') return 'neutral'
  return 'info'
}

function healthLabel(status?: string): string {
  if (status === 'ok') return '正常'
  if (status === 'degraded') return '降级'
  if (status === 'error') return '异常'
  if (status === 'disabled') return '停用'
  return status || '未知'
}

function taskTone(status?: string): ToolTone {
  if (status === 'succeeded') return 'success'
  if (status === 'running' || status === 'queued') return 'info'
  if (status === 'blocked') return 'warning'
  if (status === 'failed' || status === 'cancelled') return 'danger'
  return 'neutral'
}

function memorySourceLabel(counts: Record<string, number>): string {
  const entries = Object.entries(counts).filter(([, count]) => count > 0)
  if (!entries.length) return '暂无注入记录'
  return entries.slice(0, 2).map(([name, count]) => `${name}: ${count}`).join(' · ')
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

async function refresh() {
  refreshing.value = true
  await refreshRuntimeObservability()
  refreshing.value = false
}
</script>

<template>
  <section
    class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
           bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
  >
    <div class="flex items-center gap-3 border-b border-line/70 px-6 py-4">
      <span class="grid size-10 place-items-center rounded-[var(--radius-xl2)] bg-brand-100/70 text-brand-500">
        <Icon icon="ph:radar-duotone" :width="20" />
      </span>
      <div class="min-w-0 flex-1">
        <p class="text-[15px] font-semibold text-ink">运行观测</p>
        <p class="truncate text-xs text-ink-faint">
          会话 {{ state.activeSessionId }} · {{ shortDateTime(snapshot?.timestamp) }}
        </p>
      </div>
      <AmButton
        variant="secondary"
        size="sm"
        icon="ph:arrow-clockwise-bold"
        :loading="refreshing"
        @click="refresh"
      >
        刷新快照
      </AmButton>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-6">
      <AmEmptyState
        v-if="!snapshot"
        icon="ph:radar-duotone"
        title="暂无观测快照"
        description="连接运行时后，系统会聚合任务、Memory、Skill 和 MCP 诊断"
      />

      <div v-else class="space-y-4">
        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div
            v-for="card in summaryCards"
            :key="card.key"
            class="rounded-[var(--radius-xl3)] border border-line bg-surface p-4 shadow-[var(--shadow-soft)]"
          >
            <div class="flex items-center justify-between gap-3">
              <span class="grid size-9 place-items-center rounded-[var(--radius-xl2)] bg-brand-50 text-brand-500">
                <Icon :icon="card.icon" :width="18" />
              </span>
              <AmTag :tone="card.tone" size="sm" dot>{{ card.label }}</AmTag>
            </div>
            <p class="mt-3 text-2xl font-semibold text-ink">{{ card.value }}</p>
            <p class="mt-1 line-clamp-2 text-xs text-ink-faint">{{ card.detail }}</p>
          </div>
        </div>

        <div class="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <div class="rounded-[var(--radius-xl3)] border border-line bg-surface p-4">
            <div class="flex items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <Icon icon="ph:list-checks-duotone" :width="18" class="text-brand-500" />
                <span class="text-sm font-semibold text-ink">最近任务图</span>
              </div>
              <AmTag :tone="taskTone(latestTask?.status)" size="sm" dot>
                {{ latestTask?.status ?? 'empty' }}
              </AmTag>
            </div>
            <div v-if="latestTask" class="mt-3 rounded-[var(--radius-xl2)] bg-surface-muted/70 p-3">
              <p class="text-sm font-semibold text-ink">{{ latestTask.title }}</p>
              <p class="mt-1 line-clamp-2 text-xs text-ink-faint">{{ latestTask.body || latestTask.result || latestTask.error || '暂无任务详情' }}</p>
              <div class="mt-3 grid gap-2 sm:grid-cols-3">
                <div class="rounded-[var(--radius-xl)] bg-white/55 p-2">
                  <p class="text-[11px] text-ink-faint">Graph tasks</p>
                  <p class="mt-1 text-sm font-semibold text-ink">{{ latestGraph?.taskCount ?? 0 }}</p>
                </div>
                <div class="rounded-[var(--radius-xl)] bg-white/55 p-2">
                  <p class="text-[11px] text-ink-faint">Edges</p>
                  <p class="mt-1 text-sm font-semibold text-ink">{{ latestGraph?.edgeCount ?? 0 }}</p>
                </div>
                <div class="rounded-[var(--radius-xl)] bg-white/55 p-2">
                  <p class="text-[11px] text-ink-faint">Attempts</p>
                  <p class="mt-1 text-sm font-semibold text-ink">{{ latestTask.attemptCount ?? 0 }}/{{ latestTask.maxAttempts ?? 0 }}</p>
                </div>
              </div>
              <div v-if="latestGraph?.statusCounts" class="mt-3 flex flex-wrap gap-1.5">
                <AmTag
                  v-for="[status, count] in Object.entries(latestGraph.statusCounts)"
                  :key="status"
                  :tone="taskTone(status)"
                  size="sm"
                >
                  {{ status }} · {{ count }}
                </AmTag>
              </div>
              <p v-if="latestGraph?.error" class="mt-2 text-xs text-danger">{{ latestGraph.error }}</p>
            </div>
            <p v-else class="mt-3 rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
              当前会话还没有任务记录。
            </p>
          </div>

          <div class="rounded-[var(--radius-xl3)] border border-line bg-surface p-4">
            <div class="flex items-center gap-2">
              <Icon icon="ph:brain-duotone" :width="18" class="text-brand-500" />
              <span class="text-sm font-semibold text-ink">Memory 注入</span>
            </div>
            <div v-if="latestMemoryDiagnostic" class="mt-3 rounded-[var(--radius-xl2)] bg-surface-muted/70 p-3">
              <div class="flex items-center justify-between gap-2">
                <AmTag tone="info" size="sm">{{ latestMemoryDiagnostic.phase }}</AmTag>
                <span class="text-[11px] text-ink-faint">{{ shortDateTime(latestMemoryDiagnostic.timestamp) }}</span>
              </div>
              <p class="mt-2 truncate font-mono text-[11px] text-ink-faint">turn {{ latestMemoryDiagnostic.turnId }}</p>
              <div class="mt-3 flex flex-wrap gap-1.5">
                <AmTag v-for="[source, count] in memorySourceEntries" :key="source" tone="neutral" size="sm">
                  {{ source }} · {{ count }}
                </AmTag>
              </div>
              <div v-if="latestMemoryDiagnostic.sources.length" class="mt-3 space-y-2">
                <div
                  v-for="source in latestMemoryDiagnostic.sources.slice(0, 4)"
                  :key="`${source.kind}-${source.sourceId}`"
                  class="rounded-[var(--radius-xl)] bg-white/55 px-2.5 py-2"
                >
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-[11px] font-semibold text-ink-soft">{{ source.kind }}</span>
                    <span class="text-[10px] text-ink-faint">{{ source.contentChars }} chars</span>
                  </div>
                  <p class="mt-1 line-clamp-2 text-[11px] text-ink-faint">{{ source.reason }}</p>
                </div>
              </div>
            </div>
            <p v-else class="mt-3 rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
              暂无 memory context 诊断。发送消息后会出现本轮注入来源。
            </p>
          </div>
        </div>

        <div class="grid gap-4 xl:grid-cols-2">
          <div class="rounded-[var(--radius-xl3)] border border-line bg-surface p-4">
            <div class="flex items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <Icon icon="ph:plugs-connected-duotone" :width="18" class="text-brand-500" />
                <span class="text-sm font-semibold text-ink">Scope / MCP</span>
              </div>
              <AmTag :tone="snapshot.mcp.config.enabled ? 'success' : 'neutral'" size="sm" dot>
                {{ snapshot.mcp.config.enabled ? 'MCP enabled' : 'MCP disabled' }}
              </AmTag>
            </div>
            <div class="mt-3 grid gap-2 sm:grid-cols-3">
              <div class="rounded-[var(--radius-xl)] bg-surface-muted/70 p-3">
                <p class="text-[11px] text-ink-faint">Role scope</p>
                <p class="mt-1 line-clamp-2 text-xs font-semibold text-ink">{{ roleScopeSummary }}</p>
              </div>
              <div class="rounded-[var(--radius-xl)] bg-surface-muted/70 p-3">
                <p class="text-[11px] text-ink-faint">Skills</p>
                <p class="mt-1 text-xs font-semibold text-ink">{{ snapshot.skills.available.length }} available</p>
              </div>
              <div class="rounded-[var(--radius-xl)] bg-surface-muted/70 p-3">
                <p class="text-[11px] text-ink-faint">MCP servers</p>
                <p class="mt-1 text-xs font-semibold text-ink">
                  {{ summary?.enabledMcpServerCount ?? 0 }}/{{ summary?.mcpServerCount ?? 0 }} enabled
                </p>
              </div>
            </div>
            <div v-if="toolDecisionEntries.length" class="mt-3 flex flex-wrap gap-1.5">
              <AmTag v-for="[decision, count] in toolDecisionEntries" :key="decision" tone="neutral" size="sm">
                {{ decision }} · {{ count }}
              </AmTag>
            </div>
          </div>

          <div class="rounded-[var(--radius-xl3)] border border-line bg-surface p-4">
            <div class="flex items-center justify-between gap-3">
              <div class="flex items-center gap-2">
                <Icon icon="ph:warning-diamond-duotone" :width="18" class="text-warning" />
                <span class="text-sm font-semibold text-ink">最近异常</span>
              </div>
              <AmTag :tone="recentFailures.length ? 'warning' : 'success'" size="sm" dot>
                {{ recentFailures.length ? `${recentFailures.length} 条` : '无异常' }}
              </AmTag>
            </div>
            <div v-if="recentFailures.length" class="mt-3 space-y-2">
              <div
                v-for="record in recentFailures"
                :key="record.recordId"
                class="rounded-[var(--radius-xl2)] bg-surface-muted/70 p-3"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="truncate text-xs font-semibold text-ink">{{ record.toolName }}</span>
                  <AmTag :tone="record.toolName.startsWith('mcp__') ? 'info' : 'warning'" size="sm">
                    {{ record.decision }}
                  </AmTag>
                </div>
                <p class="mt-1 text-[11px] text-ink-faint">
                  {{ record.failureCode || record.detail || 'tool returned ok=false' }} · {{ shortDateTime(record.timestamp) }}
                </p>
              </div>
            </div>
            <p v-else class="mt-3 rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
              当前会话最近没有失败的工具或 MCP 审计记录。
            </p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
