<script setup lang="ts">
import { computed } from 'vue'
import { Icon } from '@iconify/vue'
import type { ConnectionState } from '@/types'

interface NavItem {
  key: string
  label: string
  icon: string
  badge?: number
}

const props = defineProps<{
  active: string
  connection: ConnectionState
  roleName?: string
  taskCount?: number
  skillCount?: number
  scheduledCount?: number
}>()

const emit = defineEmits<{
  navigate: [key: string]
}>()

const primary = computed<NavItem[]>(() => [
  { key: 'chat', label: '对话工作台', icon: 'ph:chats-circle-duotone' },
  { key: 'tasks', label: '任务', icon: 'ph:list-checks-duotone', badge: props.taskCount || undefined },
  { key: 'skills', label: '技能', icon: 'ph:sparkle-duotone', badge: props.skillCount || undefined },
  { key: 'schedule', label: '定时任务', icon: 'ph:alarm-duotone', badge: props.scheduledCount || undefined },
  { key: 'memory', label: '记忆库', icon: 'ph:brain-duotone' },
  { key: 'config', label: '配置中心', icon: 'ph:faders-duotone' },
])

const secondary: NavItem[] = [
  { key: 'settings', label: '设置', icon: 'ph:sliders-horizontal-duotone' },
]

const connectionMeta: Record<ConnectionState, { label: string; tone: string; dot: string }> = {
  online: { label: '已连接', tone: 'text-success', dot: 'bg-success' },
  connecting: { label: '连接中', tone: 'text-warning', dot: 'bg-warning' },
  offline: { label: '离线', tone: 'text-ink-faint', dot: 'bg-ink-faint' },
}
</script>

<template>
  <aside
    class="flex w-[248px] shrink-0 flex-col gap-6 rounded-[var(--radius-xl4)] border border-white/70
           bg-surface/80 p-4 shadow-[var(--shadow-card)] backdrop-blur-xl"
  >
    <!-- brand -->
    <div class="flex items-center gap-3 px-1 pt-1">
      <div
        class="grid size-11 place-items-center rounded-[var(--radius-xl2)]
               bg-gradient-to-br from-brand-400 to-brand-600 text-white shadow-[var(--shadow-soft)]"
      >
        <Icon icon="ph:sparkle-fill" :width="22" />
      </div>
      <div class="leading-tight">
        <p class="text-[15px] font-semibold text-ink">Amadeus</p>
        <p class="text-[11px] text-ink-faint">桌面智能体控制台</p>
      </div>
    </div>

    <!-- primary nav -->
    <nav class="flex flex-col gap-1">
      <p class="px-2 pb-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
        工作区
      </p>
      <button
        v-for="item in primary"
        :key="item.key"
        type="button"
        data-testid="main-nav-item"
        :data-nav-key="item.key"
        class="group relative flex items-center gap-3 rounded-[var(--radius-xl2)] px-3 py-2.5 text-sm font-medium
               transition-all duration-200 ease-[var(--ease-soft)]"
        :class="
          active === item.key
            ? 'bg-gradient-to-r from-brand-50 to-brand-100/60 text-brand-700 shadow-[var(--shadow-soft)]'
            : 'text-ink-soft hover:bg-surface-muted hover:text-ink'
        "
        @click="emit('navigate', item.key)"
      >
        <Icon
          :icon="item.icon"
          :width="20"
          class="shrink-0 transition-transform duration-200 group-hover:-translate-y-px"
          :class="active === item.key ? 'text-brand-500' : 'text-ink-faint'"
        />
        <span class="flex-1 text-left">{{ item.label }}</span>
        <span
          v-if="item.badge"
          class="grid min-w-5 place-items-center rounded-full px-1.5 text-[11px] font-semibold"
          :class="active === item.key ? 'bg-brand-500 text-white' : 'bg-line text-ink-faint'"
        >
          {{ item.badge }}
        </span>
        <span
          v-if="active === item.key"
          class="absolute left-0 h-6 w-1 rounded-r-full bg-brand-500"
        />
      </button>
    </nav>

    <div class="mt-auto flex flex-col gap-1">
      <button
        v-for="item in secondary"
        :key="item.key"
        type="button"
        data-testid="main-nav-item"
        :data-nav-key="item.key"
        class="group flex items-center gap-3 rounded-[var(--radius-xl2)] px-3 py-2.5 text-sm font-medium
               transition-all duration-200 ease-[var(--ease-soft)]"
        :class="
          active === item.key
            ? 'bg-brand-50 text-brand-700'
            : 'text-ink-soft hover:bg-surface-muted hover:text-ink'
        "
        @click="emit('navigate', item.key)"
      >
        <Icon :icon="item.icon" :width="20" class="text-ink-faint" />
        <span>{{ item.label }}</span>
      </button>

      <!-- connection + profile -->
      <div class="mt-2 flex items-center gap-3 rounded-[var(--radius-xl2)] bg-surface-muted/70 p-2.5">
        <div class="relative">
          <div class="grid size-9 place-items-center rounded-full bg-gradient-to-br from-blush-soft to-brand-300 text-white">
            <Icon icon="ph:cat-duotone" :width="18" />
          </div>
          <span
            class="absolute -bottom-0.5 -right-0.5 size-3 rounded-full ring-2 ring-surface"
            :class="connectionMeta[connection].dot"
          />
        </div>
        <div class="min-w-0 leading-tight">
          <p class="truncate text-[13px] font-medium text-ink">{{ roleName || 'Amadeus' }}</p>
          <p class="text-[11px]" :class="connectionMeta[connection].tone">
            {{ connectionMeta[connection].label }}
          </p>
        </div>
      </div>
    </div>
  </aside>
</template>
