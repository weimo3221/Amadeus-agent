<script setup lang="ts">
import { Icon } from '@iconify/vue'
import type { ConnectionState, SessionItem } from '@/types'
import SessionSwitcher from '@/components/workspace/SessionSwitcher.vue'
import AmButton from '@/components/ui/AmButton.vue'

defineProps<{
  sessions: SessionItem[]
  activeId: string
  connection: ConnectionState
}>()

const emit = defineEmits<{
  select: [id: string]
  create: []
  delete: [id: string]
  openSettings: []
}>()

const connectionMeta: Record<ConnectionState, { label: string; tone: string; dot: string }> = {
  online: { label: '实时已连接', tone: 'text-success bg-success-soft ring-success/15', dot: 'bg-success' },
  connecting: { label: '连接中', tone: 'text-warning bg-warning-soft ring-warning/20', dot: 'bg-warning' },
  offline: { label: '离线模式', tone: 'text-ink-faint bg-surface-muted ring-line', dot: 'bg-ink-faint' },
}
</script>

<template>
  <header
    class="relative z-30 flex items-center gap-4 rounded-[var(--radius-xl4)] border border-white/70 bg-surface/80 px-5 py-3
           shadow-[var(--shadow-card)] backdrop-blur-xl"
  >
    <div class="shrink-0">
      <div class="flex items-center gap-2">
        <h1 class="whitespace-nowrap text-lg font-semibold text-ink">对话工作台</h1>
        <span class="rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-medium text-brand-600">Beta</span>
      </div>
      <p class="mt-0.5 hidden whitespace-nowrap text-xs text-ink-faint xl:block">与你的桌面智能体协作，管理记忆、任务与技能。</p>
    </div>

    <div class="mx-1 hidden h-9 w-px shrink-0 bg-line lg:block" />

    <SessionSwitcher
      :sessions="sessions"
      :active-id="activeId"
      @select="emit('select', $event)"
      @create="emit('create')"
      @delete="emit('delete', $event)"
    />

    <div class="ml-auto flex shrink-0 items-center gap-2">
      <span
        class="hidden items-center gap-1.5 whitespace-nowrap rounded-[var(--radius-pill)] px-3 py-1.5 text-xs font-medium ring-1 ring-inset lg:inline-flex"
        :class="connectionMeta[connection].tone"
      >
        <span class="relative flex size-2">
          <span
            class="absolute inline-flex size-full animate-ping rounded-full opacity-60"
            :class="connectionMeta[connection].dot"
          />
          <span class="relative inline-flex size-2 rounded-full" :class="connectionMeta[connection].dot" />
        </span>
        {{ connectionMeta[connection].label }}
      </span>

      <AmButton variant="secondary" size="sm" icon="ph:plus-bold" class="whitespace-nowrap" @click="emit('create')">
        新会话
      </AmButton>

      <button
        type="button"
        title="设置"
        class="grid size-9 place-items-center rounded-full text-ink-faint transition-all duration-200
               hover:bg-surface-muted hover:text-ink hover:rotate-45"
        @click="emit('openSettings')"
      >
        <Icon icon="ph:gear-six-duotone" :width="19" />
      </button>
    </div>
  </header>
</template>
