<script setup lang="ts">
import { Icon } from '@iconify/vue'
import type { ConnectionState, SessionContext, SessionItem } from '@/types'
import SessionSwitcher from '@/components/workspace/SessionSwitcher.vue'
import AmButton from '@/components/ui/AmButton.vue'

defineProps<{
  sessions: SessionItem[]
  activeId: string
  sessionContext: SessionContext
  connection: ConnectionState
}>()

const emit = defineEmits<{
  select: [id: string]
  selectCompanion: []
  create: []
  delete: [id: string]
  rename: [id: string, title: string]
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
      :session-context="sessionContext"
      @select="emit('select', $event)"
      @select-companion="emit('selectCompanion')"
      @create="emit('create')"
      @delete="emit('delete', $event)"
      @rename="(id, title) => emit('rename', id, title)"
    />

    <div class="ml-auto flex shrink-0 items-center gap-2">
      <button
        type="button"
        class="hidden items-center gap-2 rounded-[var(--radius-pill)] border px-3 py-1.5 text-left text-xs transition-all duration-200 xl:inline-flex"
        :class="sessionContext.viewingCompanion
          ? 'border-brand-200 bg-brand-50 text-brand-700'
          : 'border-line bg-surface-muted text-ink-faint hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700'"
        :title="sessionContext.viewingCompanion ? '当前正在查看 Companion 会话' : '切换到 Companion 默认会话'"
        @click="emit('selectCompanion')"
      >
        <Icon icon="ph:sparkle-duotone" :width="16" class="shrink-0" />
        <span class="min-w-0">
          <span class="block font-medium">
            {{ sessionContext.viewingCompanion ? '正在查看 Companion' : '查看 Companion' }}
          </span>
          <span class="block max-w-[180px] truncate text-[11px] opacity-75">
            {{ sessionContext.companionMessageCount }} 条 · {{ sessionContext.companionUpdatedAt || '未同步' }}
          </span>
        </span>
      </button>

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
