<script setup lang="ts">
import { ref } from 'vue'
import AppBackground from '@/components/layout/AppBackground.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import WorkspaceView from '@/components/workspace/WorkspaceView.vue'
import AmModal from '@/components/ui/AmModal.vue'
import AmButton from '@/components/ui/AmButton.vue'
import AmInput from '@/components/ui/AmInput.vue'
import AmSelect from '@/components/ui/AmSelect.vue'
import { sessions as mockSessions } from '@/mock/data'
import type { ConnectionState, SessionItem } from '@/types'

const sessions = ref<SessionItem[]>([...mockSessions])
const activeId = ref(sessions.value[0]?.id ?? '')
const activeNav = ref('chat')
const connection = ref<ConnectionState>('online')

const settingsOpen = ref(false)
const roleName = ref('未来星')
const provider = ref('openrouter')
const providerOptions = [
  { label: 'OpenRouter', value: 'openrouter' },
  { label: 'OpenAI', value: 'openai' },
  { label: 'Anthropic', value: 'anthropic' },
  { label: 'DeepSeek', value: 'deepseek' },
]

function selectSession(id: string) {
  activeId.value = id
  sessions.value = sessions.value.map((s) => ({ ...s, active: s.id === id }))
}

function createSession() {
  const id = `s-${Date.now()}`
  sessions.value = [
    { id, title: '新的会话', roleName: 'Coding Assistant', messageCount: 0, updatedAt: '刚刚', active: true },
    ...sessions.value.map((s) => ({ ...s, active: false })),
  ]
  activeId.value = id
}
</script>

<template>
  <div class="relative flex h-full flex-col text-ink">
    <AppBackground />

    <div class="flex min-h-0 flex-1 gap-4 p-4">
      <AppSidebar
        :active="activeNav"
        :connection="connection"
        @navigate="activeNav = $event"
      />

      <main class="flex min-h-0 flex-1 flex-col gap-4">
        <AppHeader
          :sessions="sessions"
          :active-id="activeId"
          :connection="connection"
          @select="selectSession"
          @create="createSession"
          @open-settings="settingsOpen = true"
        />

        <WorkspaceView class="min-h-0 flex-1" />
      </main>
    </div>

    <!-- Settings modal demo -->
    <AmModal
      v-model="settingsOpen"
      title="角色与模型"
      subtitle="配置当前会话使用的智能体"
      icon="ph:sliders-horizontal-duotone"
    >
      <div class="space-y-4">
        <div class="space-y-1.5">
          <label class="text-xs font-medium text-ink-soft">角色名称</label>
          <AmInput v-model="roleName" icon="ph:user-circle-duotone" placeholder="给你的智能体起个名字" />
        </div>
        <div class="space-y-1.5">
          <label class="text-xs font-medium text-ink-soft">模型服务商</label>
          <AmSelect v-model="provider" :options="providerOptions" />
        </div>
        <div class="rounded-[var(--radius-xl2)] bg-brand-50/60 p-3 text-xs leading-relaxed text-brand-700">
          切换服务商后，会话会自动使用对应默认模型。API Key 可在「设置 · 模型」中配置。
        </div>
      </div>

      <template #footer>
        <AmButton variant="ghost" size="sm" @click="settingsOpen = false">取消</AmButton>
        <AmButton variant="primary" size="sm" icon="ph:check-bold" @click="settingsOpen = false">
          保存
        </AmButton>
      </template>
    </AmModal>
  </div>
</template>
