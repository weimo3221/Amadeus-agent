<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Icon } from '@iconify/vue'
import type { SessionContext, SessionItem } from '@/types'

const props = defineProps<{
  sessions: SessionItem[]
  activeId: string
  sessionContext: SessionContext
}>()

const emit = defineEmits<{
  select: [id: string]
  selectCompanion: []
  create: []
  delete: [id: string]
  rename: [id: string, title: string]
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const confirmId = ref<string | null>(null)
const editingId = ref<string | null>(null)
const editingTitle = ref('')
const regularSessions = computed(() =>
  props.sessions.filter((s) => s.id !== props.sessionContext.companionId),
)

function current() {
  return props.sessions.find((s) => s.id === props.activeId)
}

function pick(id: string) {
  emit('select', id)
  open.value = false
}

function askDelete(id: string) {
  if (id === props.sessionContext.companionId) return
  editingId.value = null
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

function beginRename(session: SessionItem) {
  if (session.id === props.sessionContext.companionId) return
  confirmId.value = null
  editingId.value = session.id
  editingTitle.value = session.title
}

function submitRename(session: SessionItem) {
  const title = editingTitle.value.trim()
  if (!title) return
  if (title !== session.title.trim()) {
    emit('rename', session.id, title)
  }
  editingId.value = null
  editingTitle.value = ''
}

function cancelRename() {
  editingId.value = null
  editingTitle.value = ''
}

function onDocClick(event: MouseEvent) {
  if (root.value && !root.value.contains(event.target as Node)) {
    open.value = false
    confirmId.value = null
    cancelRename()
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div ref="root" data-testid="session-switcher" class="relative">
    <button
      type="button"
      data-testid="session-switcher-trigger"
      :data-session-id="activeId"
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
            data-testid="session-create"
            class="flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-[12px] font-medium text-brand-700
                   transition-all duration-200 hover:bg-brand-100 hover:-translate-y-px"
            @click="emit('create'); open = false"
          >
            <Icon icon="ph:plus-bold" :width="13" />
            新建
          </button>
        </div>

        <button
          type="button"
          data-testid="session-companion"
          :data-session-id="sessionContext.companionId"
          class="mb-1 flex w-full items-center gap-3 rounded-[var(--radius-xl2)] border px-2.5 py-2 text-left
                 transition-all duration-150"
          :class="sessionContext.viewingCompanion
            ? 'border-brand-200 bg-brand-50'
            : 'border-line bg-white/50 hover:border-brand-200 hover:bg-brand-50/70'"
          @click="emit('selectCompanion'); open = false"
        >
          <span
            class="grid size-8 shrink-0 place-items-center rounded-[var(--radius-xl2)]"
            :class="sessionContext.viewingCompanion ? 'bg-brand-500 text-white' : 'bg-brand-50 text-brand-500'"
          >
            <Icon icon="ph:sparkle-duotone" :width="16" />
          </span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-[13px] font-semibold text-ink">
              {{ sessionContext.companionTitle }}
            </span>
            <span class="block truncate text-[11px] text-ink-faint">
              Companion 默认会话 · {{ sessionContext.companionMessageCount }} 条 ·
              {{ sessionContext.companionUpdatedAt || '未同步' }}
            </span>
          </span>
          <Icon
            v-if="sessionContext.viewingCompanion"
            icon="ph:check-circle-fill"
            :width="16"
            class="shrink-0 text-brand-500"
          />
        </button>

        <ul class="mt-1 flex max-h-72 flex-col gap-1 overflow-auto">
          <li v-for="s in regularSessions" :key="s.id">
            <div
              class="group flex w-full items-center gap-3 rounded-[var(--radius-xl2)] px-2.5 py-2 text-left
                     transition-colors duration-150 hover:bg-brand-50/70"
              :class="s.id === activeId ? 'bg-brand-50' : ''"
            >
              <form
                v-if="editingId === s.id"
                class="flex min-w-0 flex-1 items-center gap-2"
                @submit.prevent.stop="submitRename(s)"
              >
                <span
                  class="grid size-8 shrink-0 place-items-center rounded-[var(--radius-xl2)]"
                  :class="s.id === activeId ? 'bg-brand-500 text-white' : 'bg-brand-50 text-brand-500'"
                >
                  <Icon icon="ph:pencil-simple-duotone" :width="16" />
                </span>
                <input
                  v-model="editingTitle"
                  data-testid="session-rename-input"
                  :data-session-id="s.id"
                  autofocus
                  maxlength="160"
                  class="min-w-0 flex-1 rounded-[var(--radius-lg)] border border-brand-200 bg-white/80 px-2 py-1.5
                         text-[13px] font-medium text-ink outline-none transition-all duration-150
                         focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
                  @click.stop
                  @keydown.esc.prevent.stop="cancelRename"
                >
                <button
                  type="submit"
                  data-testid="session-rename-save"
                  :data-session-id="s.id"
                  title="保存名称"
                  class="grid size-7 shrink-0 place-items-center rounded-full bg-brand-500 text-white
                         transition-colors duration-150 hover:bg-brand-600"
                  :disabled="!editingTitle.trim()"
                >
                  <Icon icon="ph:check-bold" :width="14" />
                </button>
                <button
                  type="button"
                  data-testid="session-rename-cancel"
                  :data-session-id="s.id"
                  title="取消重命名"
                  class="grid size-7 shrink-0 place-items-center rounded-full bg-surface-muted text-ink-faint
                         transition-colors duration-150 hover:bg-line hover:text-ink"
                  @click.stop="cancelRename"
                >
                  <Icon icon="ph:x-bold" :width="14" />
                </button>
              </form>

              <template v-else>
                <button
                  type="button"
                  data-testid="session-select"
                  :data-session-id="s.id"
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
                    v-if="s.id !== sessionContext.companionId"
                    type="button"
                    data-testid="session-rename"
                    :data-session-id="s.id"
                    title="重命名会话"
                    class="hidden size-7 shrink-0 place-items-center rounded-full text-ink-faint
                           transition-colors duration-150 hover:bg-brand-50 hover:text-brand-600 group-hover:grid"
                    @click.stop="beginRename(s)"
                  >
                    <Icon icon="ph:pencil-simple-duotone" :width="15" />
                  </button>
                  <button
                    v-if="s.id !== sessionContext.companionId"
                    type="button"
                    title="删除会话"
                    class="hidden size-7 shrink-0 place-items-center rounded-full text-ink-faint
                           transition-colors duration-150 hover:bg-danger-soft hover:text-danger group-hover:grid"
                    @click.stop="askDelete(s.id)"
                  >
                    <Icon icon="ph:trash-duotone" :width="15" />
                  </button>
                </template>
              </template>
            </div>
          </li>
        </ul>
        <p class="px-2 pt-1.5 text-[11px] text-ink-faint">
          Main UI 查看的是当前会话；切到 Companion 后，聊天、任务和记忆会与桌面伴随窗口共享同一条会话线。
        </p>
      </div>
    </transition>
  </div>
</template>
