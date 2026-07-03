<script setup lang="ts" generic="T extends Record<string, any>">
import AmEmptyState from './AmEmptyState.vue'
import AmLoading from './AmLoading.vue'

interface Column<Row> {
  key: string
  title: string
  width?: string
  align?: 'left' | 'center' | 'right'
  cellClass?: (row: Row) => string
}

withDefaults(
  defineProps<{
    columns: Column<T>[]
    rows: T[]
    rowKey?: string
    loading?: boolean
    emptyTitle?: string
    emptyDescription?: string
    emptyIcon?: string
  }>(),
  {
    rowKey: 'id',
    emptyTitle: '暂无数据',
  },
)

const alignClass: Record<string, string> = {
  left: 'text-left',
  center: 'text-center',
  right: 'text-right',
}
</script>

<template>
  <div class="overflow-hidden rounded-[var(--radius-xl3)] border border-line bg-surface">
    <div v-if="loading" class="p-2">
      <AmLoading :rows="4" />
    </div>

    <AmEmptyState
      v-else-if="!rows.length"
      :icon="emptyIcon"
      :title="emptyTitle"
      :description="emptyDescription"
    />

    <table v-else class="w-full border-collapse text-sm">
      <thead>
        <tr class="bg-surface-muted/60">
          <th
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-ink-faint"
            :class="alignClass[col.align ?? 'left']"
            :style="col.width ? { width: col.width } : undefined"
          >
            {{ col.title }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, index) in rows"
          :key="String(row[rowKey] ?? index)"
          class="border-t border-line/70 transition-colors duration-150 ease-[var(--ease-soft)]
                 hover:bg-brand-50/60"
        >
          <td
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-3 text-ink-soft align-middle"
            :class="[alignClass[col.align ?? 'left'], col.cellClass ? col.cellClass(row) : '']"
          >
            <slot :name="`cell-${col.key}`" :row="row" :value="row[col.key]">
              {{ row[col.key] }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
