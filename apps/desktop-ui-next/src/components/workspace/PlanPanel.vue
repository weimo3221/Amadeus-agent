<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import type { PlanItem, PlanStatus } from '@/types'
import { useRuntime } from '@/composables/useRuntime'

const props = defineProps<{
  items: PlanItem[]
  title?: string
  readonly?: boolean
  archived?: boolean
  incomplete?: boolean
  defaultCollapsed?: boolean
}>()

const { createTaskFromPlan } = useRuntime()
const creatingItemId = ref<string | null>(null)
const collapsed = ref(Boolean(props.defaultCollapsed))

const meta: Record<PlanStatus, { icon: string; ring: string; text: string }> = {
  done: { icon: 'ph:check-bold', ring: 'bg-success text-white', text: 'text-ink-faint line-through' },
  active: { icon: 'ph:dot-outline-fill', ring: 'bg-brand-500 text-white', text: 'text-ink font-medium' },
  pending: { icon: 'ph:circle', ring: 'bg-surface-muted text-ink-faint', text: 'text-ink-soft' },
}

const doneCount = computed(() => props.items.filter((i) => i.status === 'done').length)
const progress = computed(() =>
  props.items.length ? Math.round((doneCount.value / props.items.length) * 100) : 0,
)
const titleText = computed(() => props.title || (props.archived ? '本轮计划' : '当前计划'))

watch(
  () => props.defaultCollapsed,
  (value) => {
    collapsed.value = Boolean(value)
  },
)

async function runInBackground(item: PlanItem): Promise<void> {
  if (props.readonly || item.status === 'done' || creatingItemId.value) return
  creatingItemId.value = item.id
  try {
    await createTaskFromPlan(item)
  } finally {
    creatingItemId.value = null
  }
}
</script>

<template>
  <div
    class="rounded-[var(--radius-xl3)] border border-brand-100 bg-gradient-to-br from-brand-50/80 to-surface p-4"
  >
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <Icon icon="ph:list-checks-duotone" :width="18" class="text-brand-500" />
        <button
          v-if="archived"
          type="button"
          class="inline-flex items-center gap-1 text-sm font-semibold text-ink"
          @click="collapsed = !collapsed"
        >
          <Icon :icon="collapsed ? 'ph:caret-right-bold' : 'ph:caret-down-bold'" :width="12" />
          {{ titleText }}
        </button>
        <span v-else class="text-sm font-semibold text-ink">{{ titleText }}</span>
        <span v-if="incomplete" class="rounded-full bg-warning-soft px-2 py-0.5 text-[10px] font-medium text-warning">
          incomplete
        </span>
      </div>
      <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-brand-600">
        {{ doneCount }}/{{ items.length }}
      </span>
    </div>

    <!-- progress bar -->
    <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-brand-100/70">
      <div
        class="h-full rounded-full bg-gradient-to-r from-brand-400 to-brand-500 transition-all duration-500 ease-[var(--ease-soft)]"
        :style="{ width: `${progress}%` }"
      />
    </div>

    <ol v-if="!collapsed" class="mt-3 flex flex-col gap-1.5">
      <li
        v-for="item in items"
        :key="item.id"
        class="flex items-center gap-2.5 rounded-[var(--radius-xl2)] px-1.5 py-1"
      >
        <span
          class="grid size-5 shrink-0 place-items-center rounded-full text-[11px]"
          :class="meta[item.status].ring"
        >
          <Icon :icon="meta[item.status].icon" :width="12" />
        </span>
        <span class="min-w-0 flex-1 text-[13px]" :class="meta[item.status].text">{{ item.label }}</span>
        <button
          v-if="!readonly && item.status !== 'done'"
          type="button"
          class="inline-flex shrink-0 items-center gap-1 rounded-full border border-brand-100 bg-surface px-2 py-0.5 text-[11px]
                 font-medium text-brand-600 transition-all duration-200 hover:border-brand-200 hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="creatingItemId !== null"
          @click="runInBackground(item)"
        >
          <Icon :icon="creatingItemId === item.id ? 'ph:spinner-gap-duotone' : 'ph:play-circle-duotone'" :width="12" />
          后台执行
        </button>
      </li>
    </ol>
  </div>
</template>
