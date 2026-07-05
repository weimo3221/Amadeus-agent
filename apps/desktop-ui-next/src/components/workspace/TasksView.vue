<script setup lang="ts">
import { Icon } from '@iconify/vue'
import type { TaskStatus, ToolTone } from '@/types'
import { useRuntime } from '@/composables/useRuntime'
import AmTable from '@/components/ui/AmTable.vue'
import AmTag from '@/components/ui/AmTag.vue'

const { state } = useRuntime()

const taskColumns = [
  { key: 'title', title: '任务', width: '46%' },
  { key: 'status', title: '状态', width: '20%' },
  { key: 'attempts', title: '尝试', width: '12%', align: 'center' as const },
  { key: 'updatedAt', title: '更新', width: '22%', align: 'right' as const },
]

const statusMeta: Record<TaskStatus, { label: string; tone: ToolTone }> = {
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'info' },
  blocked: { label: '阻塞', tone: 'warning' },
  succeeded: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

function taskDetail(row: { detail?: string; result?: string; error?: string; status?: TaskStatus }) {
  if (row.error) return `失败原因：${row.error}`
  if (row.result) return `结果：${row.result}`
  return row.detail || '暂无任务描述'
}
</script>

<template>
  <section
    class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
           bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
  >
    <div class="flex items-center gap-3 border-b border-line/70 px-6 py-4">
      <span class="grid size-10 place-items-center rounded-[var(--radius-xl2)] bg-info/10 text-info">
        <Icon icon="ph:list-checks-duotone" :width="20" />
      </span>
      <div>
        <p class="text-[15px] font-semibold text-ink">任务</p>
        <p class="text-xs text-ink-faint">当前会话共 {{ state.tasks.length }} 个任务，包含已完成和失败记录</p>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-6">
      <AmTable
        :columns="taskColumns"
        :rows="state.tasks"
        empty-title="暂无任务"
        empty-description="新建任务后会显示在这里"
        empty-icon="ph:list-plus-duotone"
      >
        <template #cell-title="{ row }">
          <div class="flex flex-col">
            <span class="font-medium text-ink">{{ row.title }}</span>
            <span class="line-clamp-2 text-xs text-ink-faint">{{ taskDetail(row) }}</span>
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
  </section>
</template>
