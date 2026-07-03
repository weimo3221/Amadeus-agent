<script setup lang="ts">
import { Icon } from '@iconify/vue'

withDefaults(
  defineProps<{
    modelValue?: string
    placeholder?: string
    icon?: string
    type?: string
    disabled?: boolean
    clearable?: boolean
  }>(),
  {
    type: 'text',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <label
    class="group flex h-10 items-center gap-2 rounded-[var(--radius-xl2)] border border-line bg-surface px-3
           transition-all duration-200 ease-[var(--ease-soft)]
           hover:border-brand-200 hover:shadow-[var(--shadow-soft)]
           focus-within:border-brand-300 focus-within:shadow-[var(--shadow-glow)]"
    :class="disabled ? 'opacity-50 pointer-events-none' : ''"
  >
    <Icon
      v-if="icon"
      :icon="icon"
      :width="18"
      class="shrink-0 text-ink-faint transition-colors duration-200 group-focus-within:text-brand-500"
    />
    <input
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      class="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-faint"
      @input="onInput"
    />
    <button
      v-if="clearable && modelValue"
      type="button"
      class="shrink-0 text-ink-faint transition-colors hover:text-danger"
      @click="emit('update:modelValue', '')"
    >
      <Icon icon="ph:x-circle-fill" :width="16" />
    </button>
  </label>
</template>
