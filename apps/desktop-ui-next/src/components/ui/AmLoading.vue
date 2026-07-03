<script setup lang="ts">
withDefaults(
  defineProps<{
    label?: string
    rows?: number
  }>(),
  {
    rows: 3,
  },
)
</script>

<template>
  <div class="flex flex-col gap-3 p-2 animate-[var(--animate-fade-in)]">
    <div
      v-for="n in rows"
      :key="n"
      class="flex items-center gap-3"
    >
      <div class="size-10 shrink-0 animate-pulse rounded-[var(--radius-xl2)] bg-surface-muted" />
      <div class="flex-1 space-y-2">
        <div class="h-3 w-1/3 animate-pulse rounded-full bg-surface-muted" />
        <div class="h-3 w-3/4 animate-pulse rounded-full bg-surface-muted" />
      </div>
    </div>
    <p v-if="label" class="text-center text-xs text-ink-faint">{{ label }}</p>
  </div>
</template>
