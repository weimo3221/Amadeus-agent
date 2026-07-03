<script setup lang="ts">
import { Icon } from '@iconify/vue'

withDefaults(
  defineProps<{
    title?: string
    subtitle?: string
    eyebrow?: string
    icon?: string
    hoverable?: boolean
    padded?: boolean
  }>(),
  {
    padded: true,
  },
)
</script>

<template>
  <section
    class="relative overflow-hidden rounded-[var(--radius-xl4)] border border-white/70 bg-surface
           shadow-[var(--shadow-card)] transition-all duration-200 ease-[var(--ease-soft)]"
    :class="hoverable ? 'hover:-translate-y-0.5 hover:shadow-[var(--shadow-float)]' : ''"
  >
    <header
      v-if="title || $slots.header || $slots.actions"
      class="flex items-center justify-between gap-3 px-5 pt-5"
    >
      <div class="flex items-center gap-3">
        <div
          v-if="icon"
          class="grid size-10 place-items-center rounded-[var(--radius-xl2)]
                 bg-gradient-to-br from-brand-100 to-brand-50 text-brand-600"
        >
          <Icon :icon="icon" :width="20" />
        </div>
        <div class="min-w-0">
          <p v-if="eyebrow" class="text-[11px] font-semibold uppercase tracking-[0.16em] text-brand-400">
            {{ eyebrow }}
          </p>
          <h3 v-if="title" class="truncate text-[15px] font-semibold text-ink">{{ title }}</h3>
          <p v-if="subtitle" class="truncate text-xs text-ink-faint">{{ subtitle }}</p>
          <slot name="header" />
        </div>
      </div>
      <div class="flex shrink-0 items-center gap-2">
        <slot name="actions" />
      </div>
    </header>

    <div :class="padded ? 'p-5' : ''">
      <slot />
    </div>
  </section>
</template>
