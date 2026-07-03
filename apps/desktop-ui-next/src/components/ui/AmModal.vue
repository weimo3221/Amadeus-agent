<script setup lang="ts">
import { Icon } from '@iconify/vue'

defineProps<{
  modelValue: boolean
  title?: string
  subtitle?: string
  icon?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

function close() {
  emit('update:modelValue', false)
}
</script>

<template>
  <transition
    enter-active-class="transition duration-200 ease-[var(--ease-soft)]"
    enter-from-class="opacity-0"
    leave-active-class="transition duration-150 ease-[var(--ease-soft)]"
    leave-to-class="opacity-0"
  >
    <div
      v-if="modelValue"
      class="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div
        class="absolute inset-0 bg-brand-900/25 backdrop-blur-sm"
        @click="close"
      />
      <transition
        enter-active-class="transition duration-200 ease-[var(--ease-soft)]"
        enter-from-class="opacity-0 translate-y-3 scale-[0.97]"
        leave-active-class="transition duration-150 ease-[var(--ease-soft)]"
        leave-to-class="opacity-0 translate-y-3 scale-[0.97]"
        appear
      >
        <div
          v-if="modelValue"
          class="glass-card relative z-10 w-full max-w-[440px] overflow-hidden rounded-[var(--radius-xl4)]"
        >
          <header class="flex items-start justify-between gap-3 border-b border-line/70 px-5 py-4">
            <div class="flex items-center gap-3">
              <div
                v-if="icon"
                class="grid size-10 place-items-center rounded-[var(--radius-xl2)] bg-brand-50 text-brand-600"
              >
                <Icon :icon="icon" :width="20" />
              </div>
              <div>
                <h3 class="text-[15px] font-semibold text-ink">{{ title }}</h3>
                <p v-if="subtitle" class="text-xs text-ink-faint">{{ subtitle }}</p>
              </div>
            </div>
            <button
              type="button"
              class="grid size-8 place-items-center rounded-full text-ink-faint transition-all
                     duration-200 hover:bg-surface-muted hover:text-ink hover:rotate-90"
              @click="close"
            >
              <Icon icon="ph:x-bold" :width="16" />
            </button>
          </header>

          <div class="px-5 py-4">
            <slot />
          </div>

          <footer
            v-if="$slots.footer"
            class="flex items-center justify-end gap-2 border-t border-line/70 bg-surface-muted/40 px-5 py-3"
          >
            <slot name="footer" />
          </footer>
        </div>
      </transition>
    </div>
  </transition>
</template>
