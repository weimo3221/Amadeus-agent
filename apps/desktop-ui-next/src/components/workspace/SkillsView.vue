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
      <span class="grid size-10 place-items-center rounded-[var(--radius-xl2)] bg-success/10 text-success">
        <Icon icon="ph:sparkle-duotone" :width="20" />
      </span>
      <div>
        <p class="text-[15px] font-semibold text-ink">技能</p>
        <p class="text-xs text-ink-faint">共 {{ state.skills.length }} 个可用技能</p>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-6">
      <AmEmptyState
        v-if="!state.skills.length"
        icon="ph:sparkle-duotone"
        title="暂无可用技能"
        description="连接运行时后，技能会在这里列出"
      />
      <div v-else class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <div
          v-for="sk in state.skills"
          :key="sk.id"
          class="group rounded-[var(--radius-xl3)] border border-line bg-surface p-4 transition-all duration-200 ease-[var(--ease-soft)]
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
            <span
              v-if="sk.score !== undefined"
              class="rounded-full bg-success-soft px-2 py-0.5 text-[11px] font-semibold text-success"
            >
              {{ Math.round(sk.score * 100) }}%
            </span>
          </div>
          <p class="mt-2.5 text-xs leading-relaxed text-ink-soft">{{ sk.summary }}</p>
        </div>
      </div>
    </div>
  </section>
</template>
