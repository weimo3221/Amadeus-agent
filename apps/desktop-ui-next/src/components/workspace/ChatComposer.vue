<script setup lang="ts">
import { ref } from 'vue'
import { Icon } from '@iconify/vue'

defineProps<{
  suggestedSkillCount?: number
}>()

const emit = defineEmits<{
  send: [text: string]
}>()

const draft = ref('')

const chips = [
  { icon: 'ph:paperclip-duotone', label: '附件' },
  { icon: 'ph:magic-wand-duotone', label: '技能' },
  { icon: 'ph:brain-duotone', label: '记忆' },
]

function submit() {
  const text = draft.value.trim()
  if (!text) return
  emit('send', text)
  draft.value = ''
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submit()
  }
}
</script>

<template>
  <div
    class="rounded-[var(--radius-xl4)] border border-line bg-surface/90 p-2.5 shadow-[var(--shadow-soft)]
           transition-all duration-200 ease-[var(--ease-soft)]
           focus-within:border-brand-300 focus-within:shadow-[var(--shadow-glow)]"
  >
    <textarea
      v-model="draft"
      rows="2"
      placeholder="和 Amadeus 说点什么…（Enter 发送，Shift+Enter 换行）"
      class="max-h-40 w-full resize-none bg-transparent px-2 py-1.5 text-sm text-ink outline-none placeholder:text-ink-faint"
      @keydown="onKeydown"
    />
    <div class="flex items-center justify-between gap-2 px-1 pt-1">
      <div class="flex items-center gap-1">
        <button
          v-for="chip in chips"
          :key="chip.label"
          type="button"
          :title="chip.label"
          class="group flex items-center gap-1.5 rounded-[var(--radius-pill)] px-2.5 py-1.5 text-xs font-medium text-ink-faint
                 transition-all duration-200 hover:bg-brand-50 hover:text-brand-600"
        >
          <Icon :icon="chip.icon" :width="16" class="transition-transform duration-200 group-hover:-translate-y-px" />
          <span class="hidden sm:inline">{{ chip.label }}</span>
          <span
            v-if="chip.label === '技能' && suggestedSkillCount"
            class="ml-0.5 rounded-full bg-brand-100 px-1.5 py-0.5 text-[10px] text-brand-700"
          >
            {{ suggestedSkillCount }}
          </span>
        </button>
      </div>

      <button
        type="button"
        :disabled="!draft.trim()"
        class="group inline-flex items-center gap-2 rounded-[var(--radius-pill)] bg-gradient-to-b from-brand-400 to-brand-600
               px-4 py-2 text-sm font-medium text-white shadow-[var(--shadow-soft)]
               transition-all duration-200 ease-[var(--ease-soft)]
               hover:-translate-y-0.5 hover:shadow-[var(--shadow-card)]
               active:translate-y-px active:scale-[0.98]
               disabled:pointer-events-none disabled:opacity-40"
        @click="submit"
      >
        发送
        <Icon icon="ph:paper-plane-tilt-fill" :width="16" class="transition-transform duration-200 group-hover:translate-x-0.5" />
      </button>
    </div>
  </div>
</template>
