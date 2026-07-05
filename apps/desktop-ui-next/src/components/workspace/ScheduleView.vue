<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmTag from '@/components/ui/AmTag.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmInput from '@/components/ui/AmInput.vue'
import AmSelect from '@/components/ui/AmSelect.vue'

const { state, createScheduledJob } = useRuntime()

const creating = ref(false)
const createError = ref('')
const form = reactive({
  title: '',
  message: '',
  schedule: '',
  mode: 'message' as 'message' | 'agent_task',
  repeatCount: '',
})

const modeOptions = [
  { label: '只发送消息', value: 'message' },
  { label: '到点执行后台任务', value: 'agent_task' },
]

const canSubmit = computed(() => form.message.trim() && form.schedule.trim())

async function submitSchedule() {
  if (!canSubmit.value || creating.value) return
  creating.value = true
  createError.value = ''
  try {
    const ok = await createScheduledJob({
      title: form.title.trim() || undefined,
      message: form.message.trim(),
      schedule: form.schedule.trim(),
      mode: form.mode,
      repeatCount: form.repeatCount ? Number(form.repeatCount) : null,
    })
    if (!ok) {
      createError.value = '创建失败，请检查时间表达式'
      return
    }
    form.title = ''
    form.message = ''
    form.schedule = ''
    form.mode = 'message'
    form.repeatCount = ''
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '创建失败'
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <section
    class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[var(--radius-xl4)] border border-white/70
           bg-surface/80 shadow-[var(--shadow-card)] backdrop-blur-xl"
  >
    <div class="flex items-center gap-3 border-b border-line/70 px-6 py-4">
      <span class="grid size-10 place-items-center rounded-[var(--radius-xl2)] bg-warning/10 text-[#b9791a]">
        <Icon icon="ph:alarm-duotone" :width="20" />
      </span>
      <div>
        <p class="text-[15px] font-semibold text-ink">定时任务</p>
        <p class="text-xs text-ink-faint">共 {{ state.scheduledJobs.length }} 个定时任务</p>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-6">
      <form
        class="mb-5 rounded-[var(--radius-xl3)] border border-line bg-surface p-4 shadow-[var(--shadow-soft)]"
        @submit.prevent="submitSchedule"
      >
        <div class="mb-3 flex items-center justify-between gap-3">
          <div>
            <p class="text-[13px] font-semibold text-ink">创建定时任务</p>
            <p class="text-xs text-ink-faint">例如：every 10m、in 1h、2026-07-05 18:30</p>
          </div>
          <AmTag :tone="form.mode === 'agent_task' ? 'brand' : 'neutral'" size="sm">
            {{ form.mode === 'agent_task' ? '会创建后台任务' : '会直接发消息' }}
          </AmTag>
        </div>
        <div class="grid gap-3 md:grid-cols-2">
          <AmInput v-model="form.title" placeholder="标题，可选" icon="ph:text-aa-duotone" />
          <AmInput v-model="form.schedule" placeholder="时间表达式，例如 every 10m" icon="ph:clock-duotone" />
          <AmSelect v-model="form.mode" :options="modeOptions" />
          <AmInput v-model="form.repeatCount" placeholder="重复次数，可选" icon="ph:repeat-duotone" type="number" />
        </div>
        <textarea
          v-model="form.message"
          rows="3"
          class="mt-3 w-full resize-none rounded-[var(--radius-xl2)] border border-line bg-surface px-3 py-2 text-sm text-ink
                 outline-none transition-all duration-200 placeholder:text-ink-faint hover:border-brand-200
                 focus:border-brand-300 focus:shadow-[var(--shadow-glow)]"
          :placeholder="form.mode === 'agent_task' ? '到点交给后台任务执行的 prompt' : '到点发送到会话里的消息'"
        />
        <div class="mt-3 flex items-center justify-between gap-3">
          <p class="text-xs text-ink-faint">
            {{ form.mode === 'agent_task' ? '触发后会在任务页生成一条可追踪任务。' : '触发后会写入一条 assistant 消息。' }}
          </p>
          <AmButton type="submit" size="sm" icon="ph:plus-circle-duotone" :loading="creating" :disabled="!canSubmit">
            创建
          </AmButton>
        </div>
        <p v-if="createError" class="mt-2 text-xs text-danger">{{ createError }}</p>
      </form>

      <AmEmptyState
        v-if="!state.scheduledJobs.length"
        icon="ph:alarm-duotone"
        title="暂无定时任务"
        description="创建定时任务后会显示在这里"
      />
      <div v-else class="space-y-3">
        <div
          v-for="job in state.scheduledJobs"
          :key="job.id"
          class="flex items-center gap-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-4 transition-all duration-200 ease-[var(--ease-soft)]
                 hover:border-brand-200 hover:shadow-[var(--shadow-soft)]"
        >
          <span class="grid size-10 shrink-0 place-items-center rounded-[var(--radius-xl2)] bg-warning-soft text-[#b9791a]">
            <Icon icon="ph:clock-countdown-duotone" :width="20" />
          </span>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <p class="truncate text-[13px] font-semibold text-ink">{{ job.title }}</p>
              <AmTag :tone="job.mode === 'agent_task' ? 'brand' : 'neutral'" size="sm">
                {{ job.mode === 'agent_task' ? '触发后台任务' : '只发送消息' }}
              </AmTag>
            </div>
            <p class="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-faint">
              <span v-if="job.schedule" class="inline-flex items-center gap-1">
                <Icon icon="ph:calendar-dots-duotone" :width="13" />{{ job.schedule }}
              </span>
              <span v-if="job.nextRun" class="inline-flex items-center gap-1">
                <Icon icon="ph:arrow-clockwise-duotone" :width="13" />下次 {{ job.nextRun }}
              </span>
              <span v-if="job.lastRun" class="inline-flex items-center gap-1">
                <Icon icon="ph:check-circle-duotone" :width="13" />上次 {{ job.lastRun }}
              </span>
              <span v-if="job.repeat" class="inline-flex items-center gap-1">
                <Icon icon="ph:repeat-duotone" :width="13" />重复 {{ job.repeat }} 次
              </span>
              <span v-if="job.completedRuns" class="inline-flex items-center gap-1">
                <Icon icon="ph:list-checks-duotone" :width="13" />已执行 {{ job.completedRuns }} 次
              </span>
              <span v-if="job.lastTaskId" class="inline-flex items-center gap-1 font-mono">
                <Icon icon="ph:flow-arrow-duotone" :width="13" />task {{ job.lastTaskId.slice(0, 8) }}
              </span>
            </p>
          </div>
          <AmTag :tone="job.statusTone" size="sm" dot>
            {{ job.statusLabel }}
          </AmTag>
        </div>
      </div>
    </div>
  </section>
</template>
