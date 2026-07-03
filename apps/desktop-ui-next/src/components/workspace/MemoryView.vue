<script setup lang="ts">
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmTag from '@/components/ui/AmTag.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'

const { state } = useRuntime()

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
      <div>
        <p class="text-[15px] font-semibold text-ink">记忆库</p>
        <p class="text-xs text-ink-faint">共 {{ state.memoryItems.length }} 条长期记忆</p>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-6">
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
  </section>
</template>
