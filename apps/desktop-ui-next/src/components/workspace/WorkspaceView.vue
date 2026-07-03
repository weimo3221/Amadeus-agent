<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import type { ChatMessage, SkillItem, TaskItem, TaskStatus, ToolTone } from '@/types'
import {
  messages as mockMessages,
  plan,
  skills,
  statusTiles,
  tasks,
} from '@/mock/data'
import ChatMessageBubble from '@/components/workspace/ChatMessage.vue'
import ChatComposer from '@/components/workspace/ChatComposer.vue'
import PlanPanel from '@/components/workspace/PlanPanel.vue'
import StatusTiles from '@/components/workspace/StatusTiles.vue'
import AmTabs from '@/components/ui/AmTabs.vue'
import AmTable from '@/components/ui/AmTable.vue'
import AmTag from '@/components/ui/AmTag.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'

const chat = ref<ChatMessage[]>([...mockMessages])
const chatLog = ref<HTMLElement | null>(null)

const rightTab = ref('overview')
const tabs = [
  { value: 'overview', label: '概览', icon: 'ph:squares-four-duotone' },
  { value: 'tasks', label: '任务', icon: 'ph:list-checks-duotone', badge: tasks.length },
  { value: 'skills', label: '技能', icon: 'ph:sparkle-duotone', badge: skills.length },
]

const taskColumns = [
  { key: 'title', title: '任务', width: '42%' },
  { key: 'status', title: '状态', width: '22%' },
  { key: 'attempts', title: '尝试', width: '14%', align: 'center' as const },
  { key: 'updatedAt', title: '更新', width: '22%', align: 'right' as const },
]

const taskRows = ref<TaskItem[]>([...tasks])
const skillRows = ref<SkillItem[]>([...skills])

const statusMeta: Record<TaskStatus, { label: string; tone: ToolTone }> = {
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'info' },
  blocked: { label: '阻塞', tone: 'warning' },
  done: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
}

const runningCount = computed(() => taskRows.value.filter((t) => t.status === 'running').length)

async function scrollToBottom() {
  await nextTick()
  if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight
}

watch(chat, scrollToBottom, { deep: true })

function handleSend(text: string) {
  chat.value.push({
    id: `m-${Date.now()}`,
    role: 'user',
    content: text,
    createdAt: nowLabel(),
  })
  const pendingId = `m-${Date.now()}-a`
  chat.value.push({
    id: pendingId,
    role: 'assistant',
    content: '收到，让我想想…',
    createdAt: nowLabel(),
    pending: true,
  })
  setTimeout(() => {
    const target = chat.value.find((m) => m.id === pendingId)
    if (target) {
      target.pending = false
      target.content = '好的，我已经记下这条需求，稍后给你一份处理方案与变更摘要～'
    }
  }, 1400)
}

function nowLabel() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

scrollToBottom()
</script>

<template>
  <div class="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_380px]">
    <!-- ===== Chat panel ===== -->
    <section
      class="relative flex min-h-0 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
             bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
    >
      <div
        class="pointer-events-none absolute -right-16 -top-16 size-56 rounded-full blur-3xl"
        style="background: radial-gradient(circle, rgba(183,166,255,0.35), transparent 70%)"
      />

      <!-- chat header + plan -->
      <div class="border-b border-line/70 px-5 py-4">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <div class="grid size-11 place-items-center rounded-[var(--radius-xl2)] bg-gradient-to-br from-blush-soft to-brand-300 text-white shadow-[var(--shadow-soft)]">
              <Icon icon="ph:cat-duotone" :width="22" />
            </div>
            <div>
              <p class="text-[15px] font-semibold text-ink">未来星 · Coding Assistant</p>
              <p class="flex items-center gap-1.5 text-xs text-ink-faint">
                <span class="inline-flex size-1.5 rounded-full bg-success" />
                在线 · GPT-SoVITS 语音就绪
              </p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <AmTag tone="brand" size="sm">
              <Icon icon="ph:sparkle-fill" :width="12" />
              8 项技能
            </AmTag>
            <button
              type="button"
              title="更多"
              class="grid size-8 place-items-center rounded-full text-ink-faint transition-all duration-200 hover:bg-surface-muted hover:text-ink"
            >
              <Icon icon="ph:dots-three-bold" :width="18" />
            </button>
          </div>
        </div>
        <div class="mt-3">
          <PlanPanel :items="plan" />
        </div>
      </div>

      <!-- chat log -->
      <div ref="chatLog" class="flex-1 space-y-5 overflow-y-auto px-5 py-5">
        <ChatMessageBubble v-for="m in chat" :key="m.id" :message="m" />
      </div>

      <!-- composer -->
      <div class="border-t border-line/70 px-5 py-4">
        <ChatComposer @send="handleSend" />
      </div>
    </section>

    <!-- ===== Right rail ===== -->
    <aside class="flex min-h-0 flex-col gap-4">
      <div
        class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
               bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
      >
        <div class="flex items-center justify-between gap-2 border-b border-line/70 px-4 py-3">
          <AmTabs v-model="rightTab" :tabs="tabs" />
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto p-4">
          <!-- overview -->
          <transition
            enter-active-class="transition duration-200 ease-[var(--ease-soft)]"
            enter-from-class="opacity-0 translate-y-2"
            mode="out-in"
          >
            <div v-if="rightTab === 'overview'" key="overview" class="space-y-4">
              <div>
                <p class="mb-2 px-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">运行状态</p>
                <StatusTiles :tiles="statusTiles" />
              </div>
              <div class="rounded-[var(--radius-xl3)] border border-line bg-surface-muted/50 p-4">
                <div class="flex items-center gap-2">
                  <Icon icon="ph:activity-duotone" :width="18" class="text-brand-500" />
                  <span class="text-sm font-semibold text-ink">实时活动</span>
                </div>
                <ul class="mt-3 space-y-2.5 text-[13px] text-ink-soft">
                  <li class="flex items-start gap-2">
                    <span class="mt-1.5 size-1.5 shrink-0 rounded-full bg-brand-400" />
                    正在运行 {{ runningCount }} 个任务，检索基准报告进行中。
                  </li>
                  <li class="flex items-start gap-2">
                    <span class="mt-1.5 size-1.5 shrink-0 rounded-full bg-success" />
                    jieba 中文检索已生效，召回率提升明显。
                  </li>
                  <li class="flex items-start gap-2">
                    <span class="mt-1.5 size-1.5 shrink-0 rounded-full bg-warning" />
                    外部 provider 等待 API key 配置。
                  </li>
                </ul>
              </div>
            </div>

            <!-- tasks -->
            <div v-else-if="rightTab === 'tasks'" key="tasks" class="space-y-3">
              <div class="flex items-center justify-between">
                <p class="px-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">任务队列</p>
                <AmButton variant="ghost" size="sm" icon="ph:arrow-clockwise-bold">刷新</AmButton>
              </div>
              <AmTable
                :columns="taskColumns"
                :rows="taskRows"
                empty-title="暂无任务"
                empty-description="新建任务后会显示在这里"
                empty-icon="ph:list-plus-duotone"
              >
                <template #cell-title="{ row }">
                  <div class="flex flex-col">
                    <span class="font-medium text-ink">{{ row.title }}</span>
                    <span class="text-xs text-ink-faint">{{ row.detail }}</span>
                  </div>
                </template>
                <template #cell-status="{ row }">
                  <AmTag :tone="statusMeta[row.status as TaskStatus].tone" size="sm" dot>
                    {{ statusMeta[row.status as TaskStatus].label }}
                  </AmTag>
                </template>
                <template #cell-attempts="{ row }">
                  <span class="text-ink-soft">{{ row.attempts }}</span>
                </template>
                <template #cell-updatedAt="{ row }">
                  <span class="text-xs text-ink-faint">{{ row.updatedAt }}</span>
                </template>
              </AmTable>
            </div>

            <!-- skills -->
            <div v-else key="skills" class="space-y-2.5">
              <p class="px-1 text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">推荐技能</p>
              <AmEmptyState
                v-if="!skillRows.length"
                icon="ph:sparkle-duotone"
                title="暂无推荐技能"
              />
              <div
                v-for="sk in skillRows"
                :key="sk.id"
                class="group rounded-[var(--radius-xl3)] border border-line bg-surface p-3.5 transition-all duration-200 ease-[var(--ease-soft)]
                       hover:-translate-y-0.5 hover:border-brand-200 hover:shadow-[var(--shadow-soft)]"
              >
                <div class="flex items-start justify-between gap-2">
                  <div class="flex items-center gap-2.5">
                    <span class="grid size-9 place-items-center rounded-[var(--radius-xl2)] bg-brand-50 text-brand-500">
                      <Icon icon="ph:puzzle-piece-duotone" :width="18" />
                    </span>
                    <div>
                      <p class="text-[13px] font-semibold text-ink">{{ sk.name }}</p>
                      <AmTag tone="neutral" size="sm">{{ sk.category }}</AmTag>
                    </div>
                  </div>
                  <span class="rounded-full bg-success-soft px-2 py-0.5 text-[11px] font-semibold text-success">
                    {{ Math.round(sk.score * 100) }}%
                  </span>
                </div>
                <p class="mt-2 text-xs leading-relaxed text-ink-soft">{{ sk.summary }}</p>
              </div>
            </div>
          </transition>
        </div>
      </div>
    </aside>
  </div>
</template>
