<script setup lang="ts">
import { computed } from 'vue'
import { Icon } from '@iconify/vue'
import { renderMarkdown } from '@/runtime/markdown'
import type { ChatMessage } from '@/types'

const props = defineProps<{
  message: ChatMessage
}>()

const renderedContent = computed(() => renderMarkdown(props.message.content ?? ''))
</script>

<template>
  <div
    class="flex items-end gap-2.5 animate-[var(--animate-rise-in)]"
    :class="message.role === 'user' ? 'flex-row-reverse' : ''"
  >
    <!-- avatar -->
    <div
      class="grid size-9 shrink-0 place-items-center rounded-[var(--radius-xl2)] shadow-[var(--shadow-soft)]"
      :class="
        message.role === 'user'
          ? 'bg-gradient-to-br from-sky-soft to-brand-400 text-white'
          : 'bg-gradient-to-br from-blush-soft to-brand-300 text-white'
      "
    >
      <Icon :icon="message.role === 'user' ? 'ph:user-duotone' : 'ph:cat-duotone'" :width="18" />
    </div>

    <div class="flex max-w-[76%] flex-col gap-1" :class="message.role === 'user' ? 'items-end' : 'items-start'">
      <!-- tool tag -->
      <span
        v-if="message.toolName"
        class="inline-flex items-center gap-1 rounded-[var(--radius-pill)] bg-info-soft px-2 py-0.5 text-[11px] font-medium text-info"
      >
        <Icon icon="ph:wrench-duotone" :width="12" />
        {{ message.toolName }}
      </span>

      <!-- bubble -->
      <div
        class="rounded-[var(--radius-xl3)] px-4 py-2.5 text-sm leading-relaxed shadow-[var(--shadow-soft)]"
        :class="
          message.role === 'user'
            ? 'rounded-br-md bg-gradient-to-br from-brand-500 to-brand-600 text-white'
            : 'rounded-bl-md border border-white/70 bg-surface text-ink'
        "
      >
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="am-md" v-html="renderedContent" />
        <span v-if="message.pending" class="mt-1 inline-flex items-center gap-1 text-xs opacity-80">
          <span class="flex gap-0.5">
            <span class="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.2s]" />
            <span class="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.1s]" />
            <span class="size-1.5 animate-bounce rounded-full bg-current" />
          </span>
          正在思考
        </span>
      </div>

      <span class="px-1 text-[11px] text-ink-faint">{{ message.createdAt }}</span>
    </div>
  </div>
</template>

<style scoped>
.am-md {
  white-space: normal;
  word-break: break-word;
}

.am-md :deep(> :first-child) {
  margin-top: 0;
}

.am-md :deep(> :last-child) {
  margin-bottom: 0;
}

.am-md :deep(p) {
  margin: 0.35em 0;
  white-space: pre-wrap;
}

.am-md :deep(h1),
.am-md :deep(h2),
.am-md :deep(h3) {
  margin: 0.6em 0 0.3em;
  font-weight: 700;
  line-height: 1.3;
}

.am-md :deep(h1) {
  font-size: 1.15em;
}

.am-md :deep(h2) {
  font-size: 1.08em;
}

.am-md :deep(h3) {
  font-size: 1em;
}

.am-md :deep(ul) {
  margin: 0.35em 0;
  padding-left: 1.2em;
  list-style: disc;
}

.am-md :deep(li) {
  margin: 0.15em 0;
}

.am-md :deep(blockquote) {
  margin: 0.4em 0;
  padding-left: 0.7em;
  border-left: 2px solid currentColor;
  opacity: 0.75;
}

.am-md :deep(a) {
  text-decoration: underline;
  text-underline-offset: 2px;
}

.am-md :deep(code) {
  padding: 0.1em 0.35em;
  border-radius: 6px;
  background: rgba(120, 110, 140, 0.16);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.88em;
}

.am-md :deep(pre) {
  margin: 0.45em 0;
  padding: 0.7em 0.85em;
  border-radius: 12px;
  background: rgba(30, 24, 48, 0.9);
  color: #f4f1fb;
  overflow-x: auto;
}

.am-md :deep(pre code) {
  padding: 0;
  border-radius: 0;
  background: transparent;
  color: inherit;
  font-size: 0.85em;
  line-height: 1.5;
}
</style>
