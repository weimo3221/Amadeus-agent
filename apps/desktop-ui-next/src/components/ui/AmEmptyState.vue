<script setup lang="ts">
import { Icon } from '@iconify/vue'

defineProps<{
  icon?: string
  title: string
  description?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-3 px-6 py-10 text-center animate-[var(--animate-fade-in)]">
    <div
      class="grid size-16 place-items-center rounded-[var(--radius-xl4)]
             bg-gradient-to-br from-brand-50 to-surface-muted text-brand-300"
    >
      <Icon :icon="icon ?? 'ph:planet-duotone'" :width="32" />
    </div>
    <div class="space-y-1">
      <p class="text-sm font-semibold text-ink-soft">{{ title }}</p>
      <p v-if="description" class="mx-auto max-w-[220px] text-xs leading-relaxed text-ink-faint">
        {{ description }}
      </p>
    </div>
    <slot />
  </div>
</template>
