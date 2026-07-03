<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Icon } from '@iconify/vue'

interface Option {
  label: string
  value: string
}

const props = defineProps<{
  modelValue: string
  options: Option[]
  placeholder?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)

const selectedLabel = computed(
  () => props.options.find((o) => o.value === props.modelValue)?.label ?? props.placeholder ?? '请选择',
)

function choose(value: string) {
  emit('update:modelValue', value)
  open.value = false
}

function onDocClick(event: MouseEvent) {
  if (root.value && !root.value.contains(event.target as Node)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div ref="root" class="relative">
    <button
      type="button"
      class="flex h-10 w-full items-center justify-between gap-2 rounded-[var(--radius-xl2)] border border-line bg-surface px-3
             text-sm text-ink transition-all duration-200 ease-[var(--ease-soft)]
             hover:border-brand-200 hover:shadow-[var(--shadow-soft)]"
      :class="open ? 'border-brand-300 shadow-[var(--shadow-glow)]' : ''"
      @click="open = !open"
    >
      <span :class="modelValue ? '' : 'text-ink-faint'">{{ selectedLabel }}</span>
      <Icon
        icon="ph:caret-down-bold"
        :width="15"
        class="text-ink-faint transition-transform duration-200"
        :class="open ? 'rotate-180' : ''"
      />
    </button>

    <transition
      enter-active-class="transition duration-200 ease-[var(--ease-soft)]"
      enter-from-class="opacity-0 -translate-y-1 scale-[0.98]"
      leave-active-class="transition duration-150 ease-[var(--ease-soft)]"
      leave-to-class="opacity-0 -translate-y-1 scale-[0.98]"
    >
      <ul
        v-if="open"
        class="glass-card absolute z-30 mt-2 w-full overflow-hidden rounded-[var(--radius-xl3)] p-1.5"
      >
        <li v-for="opt in options" :key="opt.value">
          <button
            type="button"
            class="flex w-full items-center justify-between gap-2 rounded-[var(--radius-xl2)] px-3 py-2 text-sm
                   transition-colors duration-150 hover:bg-brand-50"
            :class="opt.value === modelValue ? 'bg-brand-50 text-brand-700 font-medium' : 'text-ink-soft'"
            @click="choose(opt.value)"
          >
            {{ opt.label }}
            <Icon
              v-if="opt.value === modelValue"
              icon="ph:check-bold"
              :width="15"
              class="text-brand-500"
            />
          </button>
        </li>
      </ul>
    </transition>
  </div>
</template>
