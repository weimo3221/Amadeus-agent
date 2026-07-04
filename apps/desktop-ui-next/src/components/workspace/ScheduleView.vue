<script setup lang="ts">
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmTag from '@/components/ui/AmTag.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'

const { state } = useRuntime()
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
            <p class="truncate text-[13px] font-semibold text-ink">{{ job.title }}</p>
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
