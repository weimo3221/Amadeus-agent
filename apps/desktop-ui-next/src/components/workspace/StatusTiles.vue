<script setup lang="ts">
import { Icon } from '@iconify/vue'
import type { StatusTile, ToolTone } from '@/types'

defineProps<{
  tiles: StatusTile[]
}>()

const toneMeta: Record<ToolTone, { bg: string; icon: string }> = {
  brand: { bg: 'from-brand-50 to-surface', icon: 'text-brand-500 bg-brand-100/70' },
  success: { bg: 'from-success-soft to-surface', icon: 'text-success bg-success/10' },
  warning: { bg: 'from-warning-soft to-surface', icon: 'text-[#b9791a] bg-warning/10' },
  danger: { bg: 'from-danger-soft to-surface', icon: 'text-danger bg-danger/10' },
  info: { bg: 'from-info-soft to-surface', icon: 'text-info bg-info/10' },
  neutral: { bg: 'from-surface-muted to-surface', icon: 'text-ink-soft bg-line' },
}
</script>

<template>
  <div class="grid grid-cols-2 gap-3">
    <div
      v-for="tile in tiles"
      :key="tile.key"
      class="group flex items-center gap-3 rounded-[var(--radius-xl3)] border border-white/70 bg-gradient-to-br p-3
             shadow-[var(--shadow-soft)] transition-all duration-200 ease-[var(--ease-soft)]
             hover:-translate-y-0.5 hover:shadow-[var(--shadow-card)]"
      :class="toneMeta[tile.tone].bg"
    >
      <span
        class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)] transition-transform duration-200 group-hover:scale-105"
        :class="toneMeta[tile.tone].icon"
      >
        <Icon :icon="tile.icon" :width="20" />
      </span>
      <div class="min-w-0">
        <p class="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{{ tile.label }}</p>
        <p class="truncate text-[13px] font-semibold text-ink">{{ tile.value }}</p>
      </div>
    </div>
  </div>
</template>
