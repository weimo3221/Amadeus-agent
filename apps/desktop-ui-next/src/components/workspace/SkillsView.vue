<script setup lang="ts">
import { computed } from 'vue'
import { Icon } from '@iconify/vue'
import { useRuntime } from '@/composables/useRuntime'
import AmTag from '@/components/ui/AmTag.vue'
import AmEmptyState from '@/components/ui/AmEmptyState.vue'

const { state, toggleSuggestedSkill } = useRuntime()

const suggestedCount = computed(() => state.suggestedSkillIds.length)

function skillStatus(id: string) {
  const active = state.activeSkills.find((skill) => skill.id === id || skill.displayName === id)
  if (active?.status === 'active') return { label: 'Active', tone: 'success' as const }
  if (active?.status === 'loading') return { label: 'Activating', tone: 'info' as const }
  if (active?.status === 'failed') return { label: active.failureCode || 'Failed', tone: 'danger' as const }
  if (state.suggestedSkillIds.includes(id)) return { label: 'Suggested', tone: 'brand' as const }
  return { label: 'Available', tone: 'neutral' as const }
}
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
        <p class="text-xs text-ink-faint">
          {{ state.skills.length }} 个 available，{{ suggestedCount }} 个 suggested，{{ state.activeSkills.length }} 个本轮 active/attempted
        </p>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto p-6">
      <div
        v-if="state.activeSkills.length || suggestedCount"
        class="mb-4 rounded-[var(--radius-xl3)] border border-line bg-surface p-4"
      >
        <div class="flex items-center gap-2">
          <Icon icon="ph:traffic-sign-duotone" :width="18" class="text-brand-500" />
          <span class="text-sm font-semibold text-ink">技能状态语义</span>
        </div>
        <p class="mt-2 text-xs leading-relaxed text-ink-soft">
          Available 表示运行时已安装；Suggested 表示你希望本轮优先考虑；Active 只在模型调用
          <code class="font-mono">skill_view</code> 并成功加载后出现。
        </p>
        <div class="mt-3 flex flex-wrap gap-2">
          <AmTag v-for="skill in state.activeSkills" :key="skill.id" :tone="skill.status === 'active' ? 'success' : skill.status === 'failed' ? 'danger' : 'info'" size="sm" dot>
            {{ skill.displayName }} · {{ skill.status }}
          </AmTag>
        </div>
      </div>

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
                <div class="mt-1 flex flex-wrap gap-1.5">
                  <AmTag tone="neutral" size="sm">{{ sk.category }}</AmTag>
                  <AmTag :tone="skillStatus(sk.id).tone" size="sm" dot>{{ skillStatus(sk.id).label }}</AmTag>
                </div>
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
          <button
            type="button"
            class="mt-3 inline-flex items-center gap-1.5 rounded-[var(--radius-pill)] px-2.5 py-1.5 text-xs font-medium transition-all duration-200"
            :class="
              state.suggestedSkillIds.includes(sk.id)
                ? 'bg-brand-50 text-brand-700 hover:bg-brand-100'
                : 'bg-surface-muted text-ink-faint hover:bg-brand-50 hover:text-brand-700'
            "
            @click="toggleSuggestedSkill(sk.id)"
          >
            <Icon :icon="state.suggestedSkillIds.includes(sk.id) ? 'ph:check-circle-fill' : 'ph:plus-circle-duotone'" :width="14" />
            {{ state.suggestedSkillIds.includes(sk.id) ? '已作为建议技能' : '建议本轮使用' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
