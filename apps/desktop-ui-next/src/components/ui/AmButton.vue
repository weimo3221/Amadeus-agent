<script setup lang="ts">
import { computed } from 'vue'
import { Icon } from '@iconify/vue'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    variant?: Variant
    size?: Size
    icon?: string
    iconRight?: string
    block?: boolean
    disabled?: boolean
    loading?: boolean
    type?: 'button' | 'submit'
  }>(),
  {
    variant: 'primary',
    size: 'md',
    type: 'button',
  },
)

const base =
  'group relative inline-flex items-center justify-center gap-2 font-medium select-none ' +
  'rounded-[var(--radius-xl2)] transition-all duration-200 ease-[var(--ease-soft)] ' +
  'active:translate-y-px active:scale-[0.98] disabled:pointer-events-none disabled:opacity-45 ' +
  'focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand-500/20'

const sizes: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px]',
  md: 'h-10 px-4 text-sm',
  lg: 'h-12 px-6 text-[15px]',
}

const variants: Record<Variant, string> = {
  primary:
    'text-white bg-gradient-to-b from-brand-400 to-brand-600 shadow-[var(--shadow-soft)] ' +
    'hover:from-brand-300 hover:to-brand-500 hover:shadow-[var(--shadow-card)] hover:-translate-y-0.5',
  secondary:
    'text-brand-700 bg-brand-50 border border-brand-100 ' +
    'hover:bg-brand-100 hover:border-brand-200 hover:-translate-y-0.5 hover:shadow-[var(--shadow-soft)]',
  ghost:
    'text-ink-soft bg-transparent border border-transparent ' +
    'hover:bg-surface-muted hover:text-ink hover:border-line',
  danger:
    'text-white bg-gradient-to-b from-[#ff7d97] to-danger shadow-[var(--shadow-soft)] ' +
    'hover:-translate-y-0.5 hover:shadow-[var(--shadow-card)]',
}

const iconSize = computed(() => (props.size === 'sm' ? 16 : props.size === 'lg' ? 20 : 18))
</script>

<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    :class="[base, sizes[size], variants[variant], block ? 'w-full' : '']"
  >
    <Icon
      v-if="loading"
      icon="ph:circle-notch-bold"
      :width="iconSize"
      class="animate-spin"
    />
    <Icon
      v-else-if="icon"
      :icon="icon"
      :width="iconSize"
      class="transition-transform duration-200 group-hover:-translate-y-px"
    />
    <span><slot /></span>
    <Icon
      v-if="iconRight && !loading"
      :icon="iconRight"
      :width="iconSize"
      class="transition-transform duration-200 group-hover:translate-x-0.5"
    />
  </button>
</template>
