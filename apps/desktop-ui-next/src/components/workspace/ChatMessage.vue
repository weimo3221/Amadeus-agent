<script setup lang="ts">
import { computed } from 'vue'
import { Icon } from '@iconify/vue'
import { renderMarkdown } from '@/runtime/markdown'
import type { ChatMessage } from '@/types'
import PlanPanel from '@/components/workspace/PlanPanel.vue'

const props = defineProps<{
  message: ChatMessage
  showAvatar?: boolean
}>()

const renderedContent = computed(() => renderMarkdown(props.message.content ?? ''))
const hasContent = computed(() => Boolean((props.message.content ?? '').trim() || (props.message.reasoning ?? '').trim()))
const toolCalls = computed(() => props.message.toolCalls ?? [])
const hasToolCalls = computed(() => toolCalls.value.length > 0)
const showBubble = computed(() =>
  hasContent.value || hasToolCalls.value || Boolean(props.message.pending && !props.message.plan?.length),
)
const toolSummary = computed(() => {
  if (!toolCalls.value.length) return ''
  const names = toolCalls.value.map((call) => call.name).slice(0, 3).join('、')
  const suffix = toolCalls.value.length > 3 ? ` 等 ${toolCalls.value.length} 个` : ''
  return `${names}${suffix}`
})
</script>

<template>
  <div
    data-testid="chat-message"
    :data-role="message.role"
    class="flex items-end gap-2.5 animate-[var(--animate-rise-in)]"
    :class="message.role === 'user' ? 'flex-row-reverse' : ''"
  >
    <!-- avatar -->
    <div
      class="grid size-9 shrink-0 place-items-center rounded-[var(--radius-xl2)] shadow-[var(--shadow-soft)]"
      :class="[
        showAvatar === false ? 'invisible' : '',
        message.role === 'user'
          ? 'bg-gradient-to-br from-sky-soft to-brand-400 text-white'
          : 'bg-gradient-to-br from-blush-soft to-brand-300 text-white',
      ]"
    >
      <Icon :icon="message.role === 'user' ? 'ph:user-duotone' : 'ph:cat-duotone'" :width="18" />
    </div>

    <div class="flex max-w-[76%] flex-col gap-1" :class="message.role === 'user' ? 'items-end' : 'items-start'">
      <!-- tool tag -->
      <span
        v-if="message.toolName && showBubble"
        class="inline-flex items-center gap-1 rounded-[var(--radius-pill)] bg-info-soft px-2 py-0.5 text-[11px] font-medium text-info"
      >
        <Icon icon="ph:wrench-duotone" :width="12" />
        {{ message.toolName }}
      </span>

      <!-- bubble -->
      <div
        v-if="showBubble"
        class="rounded-[var(--radius-xl3)] px-4 py-2.5 text-sm leading-relaxed shadow-[var(--shadow-soft)]"
        :class="
          message.role === 'user'
            ? 'rounded-br-md bg-gradient-to-br from-brand-500 to-brand-600 text-white'
            : 'rounded-bl-md border border-white/70 bg-surface text-ink'
        "
      >
        <details
          v-if="message.role === 'assistant' && message.reasoning"
          class="reasoning-panel mb-2 rounded-[var(--radius-xl2)] border border-brand-100 bg-brand-50/60 px-3 py-2 text-xs text-ink-soft"
        >
          <summary class="flex cursor-pointer items-center gap-1.5 font-medium text-brand-700">
            <Icon icon="ph:brain-duotone" :width="14" />
            思考过程
          </summary>
          <div class="mt-2 whitespace-pre-wrap leading-relaxed">{{ message.reasoning }}</div>
        </details>
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div v-if="hasContent" class="am-md" v-html="renderedContent" />
        <details
          v-if="hasToolCalls"
          class="tool-call-panel rounded-[var(--radius-xl2)] border border-info/20 bg-info-soft/50 px-3 py-2 text-xs text-ink-soft"
          :class="hasContent ? 'mt-2' : ''"
        >
          <summary class="flex cursor-pointer items-center gap-1.5 font-medium text-info">
            <Icon icon="ph:wrench-duotone" :width="14" />
            工具调用 {{ toolCalls.length }} 个
            <span v-if="toolSummary" class="truncate text-ink-faint">· {{ toolSummary }}</span>
          </summary>
          <div class="mt-2 flex flex-col gap-2">
            <div
              v-for="(call, index) in toolCalls"
              :key="call.id ?? `${call.name}-${index}`"
              class="rounded-[var(--radius-xl)] border border-white/70 bg-surface/80 p-2"
            >
              <div class="flex items-center gap-2">
                <span class="rounded-full bg-info/10 px-2 py-0.5 font-mono text-[11px] text-info">
                  {{ call.name }}
                </span>
                <span v-if="call.id" class="truncate font-mono text-[10px] text-ink-faint">{{ call.id }}</span>
              </div>
              <pre
                v-if="call.argumentsText"
                class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-[var(--radius-lg)] bg-white/70 p-2 font-mono text-[11px] leading-relaxed text-ink-soft"
              >{{ call.argumentsText }}</pre>
              <p v-else class="mt-1 text-[11px] text-ink-faint">无参数</p>
            </div>
          </div>
        </details>
        <span v-if="message.pending" class="mt-1 inline-flex items-center gap-1 text-xs opacity-80">
          <span class="flex gap-0.5">
            <span class="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.2s]" />
            <span class="size-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.1s]" />
            <span class="size-1.5 animate-bounce rounded-full bg-current" />
          </span>
          正在思考
        </span>
      </div>

      <PlanPanel
        v-if="message.plan?.length"
        class="mt-2 w-full min-w-[280px]"
        :items="message.plan"
        :archived="message.planArchived"
        :readonly="message.planArchived"
        :incomplete="message.planIncomplete"
        :default-collapsed="message.planCollapsed"
      />

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

.reasoning-panel summary::-webkit-details-marker {
  display: none;
}

.tool-call-panel summary::-webkit-details-marker {
  display: none;
}
</style>
