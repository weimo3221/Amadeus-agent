<script setup lang="ts">
import { ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmTag from '@/components/ui/AmTag.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'

const { state, refreshMemoryDiagnostics } = useRuntime()

const refreshingDiagnostics = ref(false)

const scopeMeta: Record<string, { label: string; tone: 'brand' | 'info' | 'success' | 'neutral' }> = {
  user: { label: '用户', tone: 'brand' },
  agent: { label: '智能体', tone: 'info' },
  project: { label: '项目', tone: 'success' },
}

function scopeLabel(scope: string) {
  return scopeMeta[scope]?.label ?? scope
}

function scopeTone(scope: string) {
  return scopeMeta[scope]?.tone ?? 'neutral'
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

function sourceCountLabel(counts: Record<string, number>): string {
  const entries = Object.entries(counts).filter(([, count]) => count > 0)
  if (!entries.length) return '未注入额外上下文'
  return entries.map(([key, count]) => `${key}: ${count}`).join(' · ')
}

async function refreshDiagnostics() {
  refreshingDiagnostics.value = true
  await refreshMemoryDiagnostics()
  refreshingDiagnostics.value = false
}
</script>

<template>
  <section
    class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
           bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
  >
    <div class="flex items-center gap-3 border-b border-line/70 px-6 py-4">
      <span class="grid size-10 place-items-center rounded-[var(--radius-xl2)] bg-brand-100/70 text-brand-500">
        <Icon icon="ph:brain-duotone" :width="20" />
      </span>
      <div class="flex-1">
        <p class="text-[15px] font-semibold text-ink">记忆库</p>
        <p class="text-xs text-ink-faint">
          {{ state.memoryItems.length }} 条长期记忆 · {{ state.memoryContextDiagnostics.length }} 条上下文诊断
        </p>
      </div>
      <AmButton
        variant="secondary"
        size="sm"
        icon="ph:arrow-clockwise-bold"
        :loading="refreshingDiagnostics"
        @click="refreshDiagnostics"
      >
        刷新诊断
      </AmButton>
    </div>

    <div class="grid min-h-0 flex-1 gap-4 overflow-hidden p-6 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div class="min-h-0 overflow-y-auto">
        <AmEmptyState
          v-if="!state.memoryItems.length"
          icon="ph:brain-duotone"
          title="暂无记忆条目"
          description="随着对话进行，智能体会在这里沉淀长期记忆"
        />
        <div v-else class="space-y-3">
          <div
            v-for="item in state.memoryItems"
            :key="item.id"
            class="rounded-[var(--radius-xl3)] border border-line bg-surface p-4 transition-all duration-200 ease-[var(--ease-soft)]
                   hover:border-brand-200 hover:shadow-[var(--shadow-soft)]"
          >
            <div class="flex items-center justify-between gap-2">
              <AmTag :tone="scopeTone(item.scope)" size="sm">{{ scopeLabel(item.scope) }}</AmTag>
              <span class="text-[11px] text-ink-faint">{{ item.updatedAt }}</span>
            </div>
            <p class="mt-2 text-[13px] leading-relaxed text-ink-soft">{{ item.content }}</p>
          </div>
        </div>
      </div>

      <aside class="min-h-0 overflow-y-auto rounded-[var(--radius-xl3)] border border-line bg-surface p-4">
        <div class="flex items-center gap-2">
          <Icon icon="ph:activity-duotone" :width="18" class="text-brand-500" />
          <span class="text-sm font-semibold text-ink">上下文注入诊断</span>
        </div>
        <p class="mt-2 text-xs leading-relaxed text-ink-faint">
          这里展示最近几次 turn 的 Memory v2 context assembly。它是诊断视图，不代表新的长期记忆。
        </p>

        <div v-if="state.memoryContextDiagnostics.length" class="mt-4 space-y-3">
          <div
            v-for="diag in [...state.memoryContextDiagnostics].reverse()"
            :key="`${diag.turnId}-${diag.phase}-${diag.timestamp}`"
            class="rounded-[var(--radius-xl2)] bg-surface-muted/60 p-3"
          >
            <div class="flex items-center justify-between gap-2">
              <AmTag tone="info" size="sm">{{ diag.phase }}</AmTag>
              <span class="text-[11px] text-ink-faint">{{ shortDateTime(diag.timestamp) }}</span>
            </div>
            <p class="mt-2 text-[12px] font-medium text-ink">
              {{ sourceCountLabel(diag.sourceCounts) }}
            </p>
            <p class="mt-1 truncate font-mono text-[11px] text-ink-faint">turn {{ diag.turnId }}</p>
            <div v-if="diag.sources.length" class="mt-2 space-y-1.5">
              <div
                v-for="source in diag.sources.slice(0, 4)"
                :key="`${source.kind}-${source.sourceId}`"
                class="rounded-[var(--radius-xl)] bg-white/50 px-2 py-1.5"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="text-[11px] font-semibold text-ink-soft">{{ source.kind }}</span>
                  <span class="text-[10px] text-ink-faint">{{ source.contentChars }} chars</span>
                </div>
                <p class="mt-0.5 line-clamp-2 text-[11px] text-ink-faint">{{ source.reason }}</p>
              </div>
            </div>
          </div>
        </div>
        <p v-else class="mt-4 rounded-[var(--radius-xl2)] bg-surface-muted p-3 text-xs text-ink-faint">
          暂无上下文诊断。发送一条消息后，runtime 会通过 <code class="font-mono">memory.context.used</code> 返回本轮注入来源。
        </p>
      </aside>
    </div>
  </section>
</template>
