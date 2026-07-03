<script setup lang="ts">
import { ref } from 'vue'
import AppBackground from '@/components/layout/AppBackground.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import WorkspaceView from '@/components/workspace/WorkspaceView.vue'
import TasksView from '@/components/workspace/TasksView.vue'
import SkillsView from '@/components/workspace/SkillsView.vue'
import ScheduleView from '@/components/workspace/ScheduleView.vue'
import MemoryView from '@/components/workspace/MemoryView.vue'
import SettingsView from '@/components/workspace/SettingsView.vue'
import { useRuntime } from '@/composables/useRuntime'

const { state, selectSession, createSession, deleteSession } = useRuntime()

const activeNav = ref('chat')

function onNavigate(key: string) {
  activeNav.value = key
}
</script>

<template>
  <div class="relative flex h-full flex-col text-ink">
    <AppBackground />

    <div class="flex min-h-0 flex-1 gap-4 p-4">
      <AppSidebar
        :active="activeNav"
        :connection="state.connection"
        :role-name="state.roleName"
        :task-count="state.tasks.length"
        :skill-count="state.skills.length"
        :scheduled-count="state.scheduledCount"
        @navigate="onNavigate"
      />

      <main class="flex min-h-0 flex-1 flex-col gap-4">
        <AppHeader
          :sessions="state.sessions"
          :active-id="state.activeSessionId"
          :connection="state.connection"
          @select="selectSession"
          @create="createSession"
          @delete="deleteSession"
          @open-settings="onNavigate('settings')"
        />

        <WorkspaceView v-if="activeNav === 'chat'" class="min-h-0 flex-1" @navigate="onNavigate" />
        <TasksView v-else-if="activeNav === 'tasks'" class="min-h-0 flex-1" />
        <SkillsView v-else-if="activeNav === 'skills'" class="min-h-0 flex-1" />
        <ScheduleView v-else-if="activeNav === 'schedule'" class="min-h-0 flex-1" />
        <MemoryView v-else-if="activeNav === 'memory'" class="min-h-0 flex-1" />
        <SettingsView v-else-if="activeNav === 'settings'" class="min-h-0 flex-1" />
        <WorkspaceView v-else class="min-h-0 flex-1" @navigate="onNavigate" />
      </main>
    </div>
  </div>
</template>
