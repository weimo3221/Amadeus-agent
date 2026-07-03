<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { Icon } from '@iconify/vue'
import type { SessionItem } from '@/types'

const props = defineProps<{
  sessions: SessionItem[]
  activeId: string
}>()

const emit = defineEmits<{
  select: [id: string]
  create: []
  delete: [id: string]
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const confirmId = ref<string | null>(null)

function current() {
  return props.sessions.find((s) => s.id === props.activeId)
}

function pick(id: string) {
  emit('select', id)
  open.value = false
}

function askDelete(id: string) {
  confirmId.value = id
}

function confirmDelete(id: string) {
  emit('delete', id)
  confirmId.value = null
  open.value = false
}

function cancelDelete() {
  confirmId.value = null
}

function onDocClick(event: MouseEvent) {
  if (root.value && !root.value.contains(event.target as Node)) {
    open.value = false
    confirmId.value = null
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div ref="root" class="relative">
    <button
      type="button"
      class="group flex items-center gap-2.5 rounded-[var(--radius-pill)] border border-line bg-surface/80 py-1.5 pl-2.5 pr-3
             transition-all duration-200 ease-[var(--ease-soft)]
             hover:border-brand-200 hover:shadow-[var(--shadow-soft)]"
      :class="open ? 'border-brand-300 shadow-[var(--shadow-glow)]' : ''"
      @click="open = !open"
    >
      <span class="grid size-7 place-items-center rounded-full bg-brand-50 text-brand-500">
        <Icon icon="ph:chat-circle-dots-duotone" :width="16" />
      </span>
      <span class="flex flex-col items-start leading-tight">
        <span class="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">当前会话</span>
        <span class="max-w-[160px] truncate text-[13px] font-medium text-ink">
          {{ current()?.title ?? '未选择' }}
        </span>
      </span>
      <Icon
        icon="ph:caret-down-bold"
        :width="14"
        class="text-ink-faint transition-transform duration-200"
        :class="open ? 'rotate-180' : ''"
      />
    </button>

    <transition
      enter-active-class="transition duration-200 ease-[var(--ease-soft)]"
      enter-from-class="opacity-0 -translate-y-1 scale-[0.98]"
      leave-active-class="transition duration-150 ease-[var(--ease-soft)]"
      leave-to-class="opacity-0 -translate-y-1 scale-[0.98]"
    >
      <div
        v-if="open"
        class="glass-card absolute left-0 z-40 mt-2 w-80 overflow-hidden rounded-[var(--radius-xl3)] p-2"
      >
        <div class="flex items-center justify-between px-2 py-1.5">
          <span class="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">会话列表</span>
          <button
            type="button"
            class="flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-[12px] font-medium text-brand-700
                   transition-all duration-200 hover:bg-brand-100 hover:-translate-y-px"
            @click="emit('create'); open = false"
          >
            <Icon icon="ph:plus-bold" :width="13" />
            新建
          </button>
        </div>

        <ul class="mt-1 flex max-h-72 flex-col gap-1 overflow-auto">
          <li v-for="s in sessions" :key="s.id">
            <div
              class="group flex w-full items-center gap-3 rounded-[var(--radius-xl2)] px-2.5 py-2 text-left
                     transition-colors duration-150 hover:bg-brand-50/70"
              :class="s.id === activeId ? 'bg-brand-50' : ''"
            >
              <button
                type="button"
                class="flex min-w-0 flex-1 items-center gap-3 text-left"
                @click="pick(s.id)"
              >
                <span
                  class="grid size-8 shrink-0 place-items-center rounded-[var(--radius-xl2)]"
                  :class="s.id === activeId ? 'bg-brand-500 text-white' : 'bg-surface-muted text-ink-faint'"
                >
                  <Icon icon="ph:chat-teardrop-dots-duotone" :width="16" />
                </span>
                <span class="min-w-0 flex-1">
                  <span class="block truncate text-[13px] font-medium text-ink">{{ s.title }}</span>
                  <span class="block truncate text-[11px] text-ink-faint">
                    {{ s.roleName }} · {{ s.messageCount }} 条 · {{ s.updatedAt }}
                  </span>
                </span>
              </button>

              <template v-if="confirmId === s.id">
                <button
                  type="button"
                  title="确认删除"
                  class="grid size-7 shrink-0 place-items-center rounded-full bg-danger-soft text-danger
                         transition-colors duration-150 hover:bg-danger hover:text-white"
                  @click.stop="confirmDelete(s.id)"
                >
                  <Icon icon="ph:check-bold" :width="14" />
                </button>
                <button
                  type="button"
                  title="取消"
                  class="grid size-7 shrink-0 place-items-center rounded-full bg-surface-muted text-ink-faint
                         transition-colors duration-150 hover:bg-line hover:text-ink"
                  @click.stop="cancelDelete"
                >
                  <Icon icon="ph:x-bold" :width="14" />
                </button>
              </template>
              <template v-else>
                <Icon
                  v-if="s.id === activeId"
                  icon="ph:check-circle-fill"
                  :width="16"
                  class="shrink-0 text-brand-500 group-hover:hidden"
                />
                <button
                  type="button"
                  title="删除会话"
                  class="hidden size-7 shrink-0 place-items-center rounded-full text-ink-faint
                         transition-colors duration-150 hover:bg-danger-soft hover:text-danger group-hover:grid"
                  @click.stop="askDelete(s.id)"
                >
                  <Icon icon="ph:trash-duotone" :width="15" />
                </button>
              </template>
            </div>
          </li>
        </ul>
        <p class="px-2 pt-1.5 text-[11px] text-ink-faint">点击切换会话，悬停可删除会话。</p>
      </div>
    </transition>
  </div>
</template>
