<script setup lang="ts">
import type { ToolTone } from '@/types'

withDefaults(
  defineProps<{
    tone?: ToolTone
    dot?: boolean
    size?: 'sm' | 'md'
  }>(),
  {
    tone: 'neutral',
    size: 'md',
  },
)

const tones: Record<ToolTone, string> = {
  brand: 'bg-brand-50 text-brand-700 ring-brand-100',
  success: 'bg-success-soft text-success ring-success/15',
  warning: 'bg-warning-soft text-[#b9791a] ring-warning/20',
  danger: 'bg-danger-soft text-danger ring-danger/15',
  info: 'bg-info-soft text-info ring-info/15',
  neutral: 'bg-surface-muted text-ink-soft ring-line',
}

const dotTones: Record<ToolTone, string> = {
  brand: 'bg-brand-500',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
  neutral: 'bg-ink-faint',
}
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] font-medium ring-1 ring-inset"
    :class="[tones[tone], size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs']"
  >
    <span
      v-if="dot"
      class="size-1.5 rounded-full"
      :class="dotTones[tone]"
    />
    <slot />
  </span>
</template>
