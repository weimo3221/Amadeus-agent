<script setup lang="ts">
import { computed, ref } from 'vue'
import { Icon } from '@iconify/vue'
import type { TaskArtifact, TaskEventItem, TaskItem, TaskStatus, ToolTone } from '@/types'
import { useRuntime } from '@/composables/useRuntime'
import AmTable from '@/components/ui/AmTable.vue'
import AmTag from '@/components/ui/AmTag.vue'
import AmModal from '@/components/ui/AmModal.vue'
import AmButton from '@/components/ui/AmButton.vue'

const { state, loadTaskEvents, cancelTask, resumeTask, approveTask, rerunTask } = useRuntime()

const taskColumns = [
  { key: 'title', title: '任务', width: '42%' },
  { key: 'source', title: '来源', width: '18%' },
  { key: 'status', title: '状态', width: '16%' },
  { key: 'attempts', title: '尝试', width: '10%', align: 'center' as const },
  { key: 'updatedAt', title: '更新', width: '14%', align: 'right' as const },
]

const statusMeta: Record<TaskStatus, { label: string; tone: ToolTone }> = {
  queued: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'info' },
  blocked: { label: '阻塞', tone: 'warning' },
  succeeded: { label: '已完成', tone: 'success' },
  failed: { label: '失败', tone: 'danger' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

const sourceMeta: Record<string, { label: string; tone: ToolTone; icon: string }> = {
  plan: { label: '来自计划', tone: 'brand', icon: 'ph:steps-duotone' },
  scheduled_job: { label: '定时触发', tone: 'warning', icon: 'ph:alarm-duotone' },
  model: { label: '模型创建', tone: 'info', icon: 'ph:sparkle-duotone' },
  api: { label: '界面/API', tone: 'neutral', icon: 'ph:cursor-click-duotone' },
  manual: { label: '手动创建', tone: 'neutral', icon: 'ph:hand-duotone' },
  system: { label: '系统', tone: 'neutral', icon: 'ph:gear-six-duotone' },
}

const eventTone: Record<string, ToolTone> = {
  created: 'neutral',
  running: 'info',
  recovered: 'warning',
  retry_scheduled: 'warning',
  succeeded: 'success',
  failed: 'danger',
  cancelled: 'neutral',
  blocked: 'warning',
  resumed: 'info',
  review_approved: 'success',
}

const eventLabel: Record<string, string> = {
  created: '已创建',
  running: '开始执行',
  recovered: '恢复排队',
  retry_scheduled: '已安排重试',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  blocked: '等待审核',
  resumed: '恢复执行',
  review_approved: '审核通过',
}

const artifactMeta: Record<string, { label: string; icon: string; tone: ToolTone }> = {
  file: { label: '文件', icon: 'ph:file-text-duotone', tone: 'info' },
  diff: { label: 'Diff', icon: 'ph:git-diff-duotone', tone: 'brand' },
  command_output: { label: '命令输出', icon: 'ph:terminal-window-duotone', tone: 'neutral' },
  summary: { label: '摘要', icon: 'ph:article-duotone', tone: 'success' },
  link: { label: '链接', icon: 'ph:link-duotone', tone: 'info' },
}

const detailOpen = ref(false)
const selectedTaskId = ref<string | null>(null)
const taskEvents = ref<TaskEventItem[]>([])
const eventsLoading = ref(false)
const actionLoading = ref<string | null>(null)

const selectedTask = computed(() =>
  state.tasks.find((task) => task.id === selectedTaskId.value) ?? null,
)

const selectedPlanItem = computed(() => {
  const task = selectedTask.value
  if (!task?.planItemId) return null
  return state.plan.find((item) => item.id === task.planItemId) ?? null
})

function taskDetail(row: { detail?: string; result?: string; error?: string; status?: TaskStatus }) {
  if (row.error) return `失败原因：${row.error}`
  if (row.result) return `结果：${row.result}`
  return row.detail || '暂无任务描述'
}

function metaForSource(source: string) {
  return sourceMeta[source] ?? { label: source || '未知来源', tone: 'neutral' as ToolTone, icon: 'ph:question-duotone' }
}

function compactId(id?: string | null) {
  return id ? id.slice(0, 8) : '无'
}

function formatDateTime(value?: string | null) {
  if (!value) return '无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function relativeFutureLabel(value?: string | null) {
  if (!value) return '无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const diffMs = date.getTime() - Date.now()
  const absMin = Math.max(0, Math.ceil(Math.abs(diffMs) / 60000))
  if (diffMs <= 0) return '已到期'
  if (absMin < 1) return '1 分钟内'
  if (absMin < 60) return `${absMin} 分钟后`
  const hours = Math.ceil(absMin / 60)
  if (hours < 24) return `${hours} 小时后`
  return `${Math.ceil(hours / 24)} 天后`
}

function formatMetadata(value: unknown) {
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function checkpointText(task: TaskItem | null, key: string) {
  if (!task?.checkpoint || typeof task.checkpoint !== 'object') return ''
  const value = task.checkpoint[key]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

function checkpointResumeFrom(task: TaskItem | null): Record<string, unknown> | null {
  const value = task?.checkpoint?.resumeFrom
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function resumeText(task: TaskItem | null, key: string) {
  const resumeFrom = checkpointResumeFrom(task)
  if (!resumeFrom) return ''
  const value = resumeFrom[key]
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

function hasCheckpoint(task: TaskItem | null) {
  return Boolean(task && Object.keys(task.checkpoint ?? {}).length)
}

function checkpointPreview(task: TaskItem | null) {
  const resumeFrom = checkpointResumeFrom(task)
  const value = resumeFrom?.resultPreview ?? resumeFrom?.errorPreview
  return typeof value === 'string' ? value : ''
}

function checkpointRecord(task: TaskItem | null) {
  return asRecord(task?.checkpoint)
}

function checkpointOrResumeText(task: TaskItem | null, key: string) {
  const checkpoint = checkpointRecord(task)
  const checkpointValue = checkpoint?.[key]
  if (typeof checkpointValue === 'string' || typeof checkpointValue === 'number') return String(checkpointValue)
  const resumeFrom = checkpointResumeFrom(task)
  const resumeValue = resumeFrom?.[key]
  return typeof resumeValue === 'string' || typeof resumeValue === 'number' ? String(resumeValue) : ''
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter(Boolean)
}

function checkpointStringList(task: TaskItem | null, key: string) {
  const checkpoint = checkpointRecord(task)
  const direct = stringList(checkpoint?.[key])
  if (direct.length) return direct
  const resumeFrom = checkpointResumeFrom(task)
  return stringList(resumeFrom?.[key])
}

function approvalToolName(task: TaskItem | null) {
  return checkpointText(task, 'toolName') || checkpointText(task, 'approvedToolName') || resumeText(task, 'toolName')
}

function approvalActionKey(task: TaskItem | null) {
  return checkpointText(task, 'approvalActionKey') || checkpointText(task, 'approvedToolAction') || resumeText(task, 'approvalActionKey')
}

function approvalActionLabel(task: TaskItem | null) {
  return checkpointOrResumeText(task, 'approvalActionLabel') || approvalActionKey(task)
}

function approvalRiskLevel(task: TaskItem | null) {
  return checkpointOrResumeText(task, 'approvalRiskLevel')
}

function approvalRiskLabels(task: TaskItem | null) {
  return checkpointStringList(task, 'approvalRiskLabels')
}

function approvalExpiry(task: TaskItem | null) {
  return checkpointText(task, 'approvedToolActionExpiresAt')
}

function approvalExpiryTone(task: TaskItem | null): ToolTone {
  const expiry = approvalExpiry(task)
  if (!expiry) return 'neutral'
  const date = new Date(expiry)
  if (Number.isNaN(date.getTime())) return 'neutral'
  return date.getTime() <= Date.now() ? 'danger' : 'warning'
}

function approvalStateLabel(task: TaskItem | null) {
  const phase = checkpointText(task, 'phase')
  const expiry = approvalExpiry(task)
  if (expiry) {
    const date = new Date(expiry)
    if (!Number.isNaN(date.getTime()) && date.getTime() <= Date.now()) return '授权已过期'
  }
  if (phase === 'approval_required') return '等待批准'
  if (phase === 'approval_resume_requested') return '已批准本次动作'
  return '审批动作'
}

function hasApprovalCheckpoint(task: TaskItem | null) {
  return Boolean(
    approvalToolName(task)
      || approvalActionKey(task)
      || approvalActionLabel(task)
      || approvalRiskLevel(task)
      || approvalRiskLabels(task).length
      || approvalExpiry(task),
  )
}

function artifactLabel(artifact: TaskArtifact) {
  return artifactMeta[artifact.type]?.label ?? artifact.type
}

function artifactIcon(artifact: TaskArtifact) {
  return artifactMeta[artifact.type]?.icon ?? 'ph:package-duotone'
}

function artifactTone(artifact: TaskArtifact): ToolTone {
  return artifactMeta[artifact.type]?.tone ?? 'neutral'
}

function artifactBody(artifact: TaskArtifact) {
  return String(artifact.content ?? artifact.summary ?? artifact.path ?? artifact.url ?? '')
}

function artifactMetadata(artifact: TaskArtifact) {
  return asRecord(artifact.metadata)
}

function artifactResumePolicy(artifact: TaskArtifact) {
  return asRecord(artifactMetadata(artifact)?.fileResumePolicy)
}

function artifactManifestVerification(artifact: TaskArtifact) {
  return asRecord(artifactMetadata(artifact)?.fileManifestVerification)
}

const resumePolicyMeta: Record<string, { label: string; tone: ToolTone; icon: string }> = {
  skip_redundant_mutation: { label: '跳过重复修改', tone: 'success', icon: 'ph:skip-forward-circle-duotone' },
  reinspect_before_mutation: { label: '先重新检查', tone: 'warning', icon: 'ph:magnifying-glass-duotone' },
  reuse_observation: { label: '复用观察结果', tone: 'info', icon: 'ph:book-open-duotone' },
  refresh_context: { label: '刷新上下文', tone: 'warning', icon: 'ph:arrows-clockwise-duotone' },
}

const manifestStatusMeta: Record<string, { label: string; tone: ToolTone }> = {
  unchanged: { label: '文件未变化', tone: 'success' },
  changed: { label: '文件已变化', tone: 'warning' },
  unverifiable: { label: '无法校验', tone: 'neutral' },
}

function resumePolicyAction(policy: Record<string, unknown> | null) {
  return String(policy?.action ?? '')
}

function resumePolicyTone(policy: Record<string, unknown> | null): ToolTone {
  return resumePolicyMeta[resumePolicyAction(policy)]?.tone ?? 'neutral'
}

function resumePolicyIcon(policy: Record<string, unknown> | null) {
  return resumePolicyMeta[resumePolicyAction(policy)]?.icon ?? 'ph:traffic-sign-duotone'
}

function resumePolicyLabel(policy: Record<string, unknown> | null) {
  const action = resumePolicyAction(policy)
  return resumePolicyMeta[action]?.label ?? action
}

function manifestStatus(verification: Record<string, unknown> | null) {
  return String(verification?.status ?? '')
}

function manifestStatusTone(verification: Record<string, unknown> | null): ToolTone {
  return manifestStatusMeta[manifestStatus(verification)]?.tone ?? 'neutral'
}

function manifestStatusLabel(verification: Record<string, unknown> | null) {
  const status = manifestStatus(verification)
  return manifestStatusMeta[status]?.label ?? status
}

function resumePolicyInstructions(policy: Record<string, unknown> | null) {
  return stringList(policy?.instructions)
}

function resumePolicyPaths(policy: Record<string, unknown> | null) {
  return stringList(policy?.paths)
}

function resumePolicyReason(policy: Record<string, unknown> | null) {
  return typeof policy?.reason === 'string' ? policy.reason : ''
}

async function openTaskDetail(task: TaskItem) {
  selectedTaskId.value = task.id
  detailOpen.value = true
  taskEvents.value = []
  eventsLoading.value = true
  try {
    taskEvents.value = await loadTaskEvents(task.id)
  } finally {
    eventsLoading.value = false
  }
}

async function runCancel(task: TaskItem) {
  actionLoading.value = 'cancel'
  try {
    await cancelTask(task.id)
    taskEvents.value = await loadTaskEvents(task.id)
  } finally {
    actionLoading.value = null
  }
}

async function runResume(task: TaskItem) {
  actionLoading.value = 'resume'
  try {
    await resumeTask(task.id)
    taskEvents.value = await loadTaskEvents(task.id)
  } finally {
    actionLoading.value = null
  }
}

async function runApprove(task: TaskItem) {
  actionLoading.value = 'approve'
  try {
    await approveTask(task.id)
    taskEvents.value = await loadTaskEvents(task.id)
  } finally {
    actionLoading.value = null
  }
}

async function runRerun(task: TaskItem) {
  actionLoading.value = 'rerun'
  try {
    await rerunTask(task)
    detailOpen.value = false
  } finally {
    actionLoading.value = null
  }
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
            <div class="flex items-center gap-2">
              <span class="font-medium text-ink">{{ row.title }}</span>
              <button
                type="button"
                class="rounded-full border border-line px-2 py-0.5 text-[11px] font-medium text-brand-600 transition-colors
                       hover:border-brand-200 hover:bg-brand-50"
                @click="openTaskDetail(row)"
              >
                详情
              </button>
            </div>
            <span class="line-clamp-2 text-xs text-ink-faint">{{ taskDetail(row) }}</span>
          </div>
        </template>
        <template #cell-source="{ row }">
          <AmTag :tone="metaForSource(row.source).tone" size="sm">
            <span class="inline-flex items-center gap-1">
              <Icon :icon="metaForSource(row.source).icon" :width="12" />
              {{ metaForSource(row.source).label }}
            </span>
          </AmTag>
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

    <AmModal
      v-model="detailOpen"
      title="任务详情"
      :subtitle="selectedTask?.title"
      icon="ph:list-checks-duotone"
    >
      <div v-if="selectedTask" class="max-h-[70vh] space-y-4 overflow-y-auto pr-1">
        <div class="grid grid-cols-2 gap-2 text-xs">
          <div class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/50 p-3">
            <p class="text-ink-faint">状态</p>
            <AmTag class="mt-1" :tone="statusMeta[selectedTask.status].tone" size="sm" dot>
              {{ statusMeta[selectedTask.status].label }}
            </AmTag>
          </div>
          <div class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/50 p-3">
            <p class="text-ink-faint">来源</p>
            <p class="mt-1 font-medium text-ink">{{ metaForSource(selectedTask.source).label }}</p>
          </div>
          <div class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/50 p-3">
            <p class="text-ink-faint">类型 / Worker</p>
            <p class="mt-1 font-mono text-[11px] text-ink">{{ selectedTask.kind }} / {{ selectedTask.workerType }}</p>
          </div>
          <div class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/50 p-3">
            <p class="text-ink-faint">尝试次数</p>
            <p class="mt-1 font-medium text-ink">{{ selectedTask.attempts }} / {{ selectedTask.maxAttempts }}</p>
          </div>
        </div>

        <div class="rounded-[var(--radius-xl2)] border border-line bg-surface p-3 text-xs">
          <p class="mb-2 font-semibold text-ink">关联关系</p>
          <div class="space-y-1 text-ink-faint">
            <p>任务 ID：<span class="font-mono text-ink">{{ selectedTask.id }}</span></p>
            <p>父任务：<span class="font-mono text-ink">{{ compactId(selectedTask.parentTaskId) }}</span></p>
            <p>
              计划步骤：
              <span class="text-ink">{{ selectedPlanItem?.label ?? selectedTask.planItemId ?? '无' }}</span>
            </p>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2 text-xs">
          <div class="rounded-[var(--radius-xl2)] border border-line bg-surface p-3">
            <p class="text-ink-faint">下次运行 / 重试</p>
            <p class="mt-1 font-medium text-ink">{{ relativeFutureLabel(selectedTask.nextRunAt ?? selectedTask.dueAt) }}</p>
            <p class="mt-1 font-mono text-[11px] text-ink-faint">{{ formatDateTime(selectedTask.nextRunAt ?? selectedTask.dueAt) }}</p>
          </div>
          <div class="rounded-[var(--radius-xl2)] border border-line bg-surface p-3">
            <p class="text-ink-faint">Lease / 完成</p>
            <p class="mt-1 font-medium text-ink">
              {{ selectedTask.finishedAt ? '已结束' : selectedTask.leaseExpiresAt ? 'Lease 有效' : selectedTask.lastHeartbeat ? '仅有心跳' : '无租约' }}
            </p>
            <p class="mt-1 font-mono text-[11px] text-ink-faint">{{ formatDateTime(selectedTask.finishedAt ?? selectedTask.leaseExpiresAt ?? selectedTask.lastHeartbeat) }}</p>
          </div>
        </div>

        <div class="rounded-[var(--radius-xl2)] border border-line bg-surface p-3 text-xs">
          <p class="mb-2 font-semibold text-ink">Worker Lease</p>
          <div class="grid gap-2 sm:grid-cols-3">
            <p class="min-w-0 text-ink-faint">
              Runner：<span class="font-mono text-ink">{{ selectedTask.runnerKind ?? 'in_process' }}</span>
            </p>
            <p class="min-w-0 text-ink-faint">
              Owner：<span class="font-mono text-ink">{{ selectedTask.leaseOwner ?? '无' }}</span>
            </p>
            <p class="min-w-0 text-ink-faint">
              Heartbeat：<span class="font-mono text-ink">{{ formatDateTime(selectedTask.lastHeartbeat) }}</span>
            </p>
          </div>
        </div>

        <div
          v-if="hasCheckpoint(selectedTask) || selectedTask.handoffSummary"
          class="rounded-[var(--radius-xl2)] border border-warning/25 bg-warning/5 p-3 text-xs"
        >
          <div class="mb-2 flex items-center justify-between gap-2">
            <p class="font-semibold text-ink">恢复 / Checkpoint</p>
            <AmTag v-if="checkpointText(selectedTask, 'phase')" tone="warning" size="sm">
              {{ checkpointText(selectedTask, 'phase') }}
            </AmTag>
          </div>
          <div class="grid gap-2 sm:grid-cols-3">
            <p class="min-w-0 text-ink-faint">
              原因：<span class="font-mono text-ink">{{ checkpointText(selectedTask, 'reason') || '无' }}</span>
            </p>
            <p class="min-w-0 text-ink-faint">
              恢复阶段：<span class="font-mono text-ink">{{ resumeText(selectedTask, 'previousPhase') || resumeText(selectedTask, 'phase') || '无' }}</span>
            </p>
            <p class="min-w-0 text-ink-faint">
              最后事件：<span class="font-mono text-ink">{{ resumeText(selectedTask, 'lastEventType') || '无' }}</span>
            </p>
          </div>
          <div
            v-if="hasApprovalCheckpoint(selectedTask)"
            class="mt-3 border-t border-warning/15 pt-3"
          >
            <div class="mb-2 flex flex-wrap items-center gap-2">
              <AmTag tone="warning" size="sm">
                <Icon icon="ph:shield-warning-duotone" :width="12" />
                {{ approvalStateLabel(selectedTask) }}
              </AmTag>
              <AmTag v-if="approvalToolName(selectedTask)" tone="neutral" size="sm">
                {{ approvalToolName(selectedTask) }}
              </AmTag>
              <AmTag v-if="approvalRiskLevel(selectedTask)" :tone="approvalRiskLevel(selectedTask) === 'high' ? 'danger' : 'warning'" size="sm">
                {{ approvalRiskLevel(selectedTask) }}
              </AmTag>
              <AmTag v-if="approvalExpiry(selectedTask)" :tone="approvalExpiryTone(selectedTask)" size="sm">
                {{ relativeFutureLabel(approvalExpiry(selectedTask)) }}
              </AmTag>
            </div>
            <p v-if="approvalActionLabel(selectedTask)" class="text-ink-soft">
              动作：<span class="font-mono text-ink">{{ approvalActionLabel(selectedTask) }}</span>
            </p>
            <p v-if="approvalActionKey(selectedTask)" class="mt-1 break-all text-ink-faint">
              Key：<span class="font-mono text-ink-soft">{{ approvalActionKey(selectedTask) }}</span>
            </p>
            <p v-if="approvalExpiry(selectedTask)" class="mt-1 text-ink-faint">
              有效期：<span class="font-mono text-ink-soft">{{ formatDateTime(approvalExpiry(selectedTask)) }}</span>
            </p>
            <div v-if="approvalRiskLabels(selectedTask).length" class="mt-2 flex flex-wrap gap-1">
              <AmTag
                v-for="label in approvalRiskLabels(selectedTask)"
                :key="label"
                tone="neutral"
                size="sm"
              >
                {{ label }}
              </AmTag>
            </div>
          </div>
          <p v-if="selectedTask.handoffSummary" class="mt-2 whitespace-pre-wrap text-ink-soft">{{ selectedTask.handoffSummary }}</p>
          <p v-if="checkpointPreview(selectedTask)" class="mt-2 whitespace-pre-wrap rounded-[var(--radius-xl2)] bg-white/60 p-2 text-ink-soft">
            {{ checkpointPreview(selectedTask) }}
          </p>
          <pre
            v-if="hasCheckpoint(selectedTask)"
            class="mt-2 max-h-28 overflow-auto rounded-[var(--radius-xl2)] bg-white/60 p-2 text-[11px] text-ink-faint"
          >{{ formatMetadata(selectedTask.checkpoint) }}</pre>
        </div>

        <div v-if="selectedTask.detail || selectedTask.result || selectedTask.error" class="space-y-2">
          <div v-if="selectedTask.detail" class="rounded-[var(--radius-xl2)] border border-line bg-surface p-3">
            <p class="mb-1 text-xs font-semibold text-ink">任务内容</p>
            <p class="whitespace-pre-wrap text-xs text-ink-soft">{{ selectedTask.detail }}</p>
          </div>
          <div v-if="selectedTask.result" class="rounded-[var(--radius-xl2)] border border-success/20 bg-success/5 p-3">
            <p class="mb-1 text-xs font-semibold text-success">结果</p>
            <p class="whitespace-pre-wrap text-xs text-ink-soft">{{ selectedTask.result }}</p>
          </div>
          <div v-if="selectedTask.error || selectedTask.blockedReason" class="rounded-[var(--radius-xl2)] border border-danger/20 bg-danger/5 p-3">
            <p class="mb-1 text-xs font-semibold text-danger">失败 / 阻塞原因</p>
            <p class="whitespace-pre-wrap text-xs text-ink-soft">{{ selectedTask.error || selectedTask.blockedReason }}</p>
          </div>
        </div>

        <div v-if="selectedTask.artifacts.length" class="rounded-[var(--radius-xl2)] border border-line bg-surface p-3">
          <p class="mb-2 text-xs font-semibold text-ink">Artifacts</p>
          <div class="space-y-2">
            <div
              v-for="(artifact, index) in selectedTask.artifacts"
              :key="`${artifact.type}-${index}`"
              class="rounded-[var(--radius-xl2)] border border-line bg-surface-muted/60 p-2"
            >
              <div class="mb-1 flex items-center justify-between gap-2">
                <AmTag :tone="artifactTone(artifact)" size="sm">
                  <Icon :icon="artifactIcon(artifact)" :width="12" />
                  {{ artifactLabel(artifact) }}
                </AmTag>
                <span class="truncate text-[11px] font-medium text-ink">{{ artifact.title ?? 'Artifact' }}</span>
              </div>
              <a
                v-if="artifact.url"
                :href="artifact.url"
                target="_blank"
                rel="noreferrer"
                class="text-xs text-brand-600 hover:underline"
              >
                {{ artifact.url }}
              </a>
              <p v-else-if="artifact.path" class="font-mono text-[11px] text-ink-soft">{{ artifact.path }}</p>
              <div
                v-if="artifactResumePolicy(artifact)"
                class="mt-2 rounded-[var(--radius-xl2)] border border-brand-100 bg-white/70 p-2 text-[11px]"
              >
                <div class="mb-1 flex flex-wrap items-center gap-2">
                  <AmTag :tone="resumePolicyTone(artifactResumePolicy(artifact))" size="sm">
                    <Icon :icon="resumePolicyIcon(artifactResumePolicy(artifact))" :width="12" />
                    {{ resumePolicyLabel(artifactResumePolicy(artifact)) }}
                  </AmTag>
                  <AmTag
                    v-if="artifactManifestVerification(artifact)"
                    :tone="manifestStatusTone(artifactManifestVerification(artifact))"
                    size="sm"
                  >
                    {{ manifestStatusLabel(artifactManifestVerification(artifact)) }}
                  </AmTag>
                </div>
                <p v-if="resumePolicyReason(artifactResumePolicy(artifact))" class="text-ink-soft">
                  {{ resumePolicyReason(artifactResumePolicy(artifact)) }}
                </p>
                <p v-if="resumePolicyPaths(artifactResumePolicy(artifact)).length" class="mt-1 font-mono text-ink-faint">
                  {{ resumePolicyPaths(artifactResumePolicy(artifact)).join(', ') }}
                </p>
                <ul v-if="resumePolicyInstructions(artifactResumePolicy(artifact)).length" class="mt-1 space-y-0.5 text-ink-faint">
                  <li
                    v-for="instruction in resumePolicyInstructions(artifactResumePolicy(artifact))"
                    :key="instruction"
                  >
                    {{ instruction }}
                  </li>
                </ul>
              </div>
              <pre
                v-if="artifactBody(artifact)"
                class="mt-1 max-h-28 overflow-auto whitespace-pre-wrap rounded-[var(--radius-xl2)] bg-white/60 p-2 text-[11px] text-ink-soft"
              >{{ artifactBody(artifact) }}</pre>
              <pre
                v-if="!artifactResumePolicy(artifact) && formatMetadata(artifact.metadata)"
                class="mt-1 max-h-24 overflow-auto rounded-[var(--radius-xl2)] bg-white/60 p-2 text-[11px] text-ink-faint"
              >{{ formatMetadata(artifact.metadata) }}</pre>
            </div>
          </div>
        </div>

        <div class="rounded-[var(--radius-xl2)] border border-line bg-surface p-3">
          <div class="mb-3 flex items-center justify-between">
            <p class="text-xs font-semibold text-ink">事件时间线</p>
            <span v-if="eventsLoading" class="text-[11px] text-ink-faint">加载中...</span>
          </div>
          <div v-if="!eventsLoading && !taskEvents.length" class="text-xs text-ink-faint">暂无事件</div>
          <div v-else class="space-y-3">
            <div v-for="event in taskEvents" :key="event.eventId" class="flex gap-3">
              <div class="mt-1 size-2 rounded-full bg-brand-400" />
              <div class="min-w-0 flex-1">
                <div class="flex flex-wrap items-center gap-2">
                  <AmTag :tone="eventTone[event.type] ?? 'neutral'" size="sm">{{ eventLabel[event.type] ?? event.type }}</AmTag>
                  <span class="font-mono text-[11px] text-ink-faint">{{ formatDateTime(event.createdAt) }}</span>
                  <span v-if="event.status" class="text-[11px] text-ink-faint">{{ event.status }}</span>
                </div>
                <p v-if="event.message" class="mt-1 text-xs text-ink-soft">{{ event.message }}</p>
                <pre v-if="formatMetadata(event.metadata)" class="mt-1 max-h-24 overflow-auto rounded bg-surface-muted p-2 text-[11px] text-ink-faint">{{ formatMetadata(event.metadata) }}</pre>
              </div>
            </div>
          </div>
        </div>
      </div>

      <template #footer>
        <AmButton
          v-if="selectedTask && ['queued', 'running'].includes(selectedTask.status)"
          variant="danger"
          size="sm"
          icon="ph:x-circle-duotone"
          :loading="actionLoading === 'cancel'"
          @click="runCancel(selectedTask)"
        >
          取消
        </AmButton>
        <AmButton
          v-if="selectedTask && selectedTask.status === 'blocked' && selectedTask.reviewRequired"
          variant="primary"
          size="sm"
          icon="ph:check-circle-duotone"
          :loading="actionLoading === 'approve'"
          @click="runApprove(selectedTask)"
        >
          审核通过
        </AmButton>
        <AmButton
          v-if="selectedTask && selectedTask.status === 'blocked'"
          variant="secondary"
          size="sm"
          icon="ph:play-circle-duotone"
          :loading="actionLoading === 'resume'"
          @click="runResume(selectedTask)"
        >
          恢复执行
        </AmButton>
        <AmButton
          v-if="selectedTask && ['failed', 'cancelled', 'succeeded'].includes(selectedTask.status)"
          variant="secondary"
          size="sm"
          icon="ph:arrow-clockwise-duotone"
          :loading="actionLoading === 'rerun'"
          @click="runRerun(selectedTask)"
        >
          重新运行
        </AmButton>
      </template>
    </AmModal>
  </section>
</template>
