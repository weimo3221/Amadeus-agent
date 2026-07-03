<script setup lang="ts">
import { Icon } from '@iconify/vue'
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
}>()
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
        <p class="whitespace-pre-wrap">{{ message.content }}</p>
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
