<script setup lang="ts">
import { Icon } from '@iconify/vue'

interface TabItem {
  value: string
  label: string
  icon?: string
  badge?: string | number
}

defineProps<{
  modelValue: string
  tabs: TabItem[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>

<template>
  <div
    class="inline-flex items-center gap-1 rounded-[var(--radius-pill)] border border-line bg-surface-muted/70 p-1"
  >
    <button
      v-for="tab in tabs"
      :key="tab.value"
      type="button"
      class="group relative inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] px-3.5 py-1.5
             text-[13px] font-medium transition-all duration-200 ease-[var(--ease-soft)]"
      :class="
        modelValue === tab.value
          ? 'bg-surface text-brand-700 shadow-[var(--shadow-soft)]'
          : 'text-ink-faint hover:text-ink-soft'
      "
      @click="emit('update:modelValue', tab.value)"
    >
      <Icon
        v-if="tab.icon"
        :icon="tab.icon"
        :width="16"
        class="transition-transform duration-200 group-hover:-translate-y-px"
      />
      <span>{{ tab.label }}</span>
      <span
        v-if="tab.badge !== undefined"
        class="ml-0.5 grid min-w-4 place-items-center rounded-full px-1 text-[10px] font-semibold"
        :class="
          modelValue === tab.value
            ? 'bg-brand-100 text-brand-700'
            : 'bg-line text-ink-faint'
        "
      >
        {{ tab.badge }}
      </span>
    </button>
  </div>
</template>
