<script setup lang="ts">
// Soft ambient background: pastel gradient blobs + subtle twinkling dots.
// Purely decorative; sits behind the app shell and never intercepts pointer events.
const stars = [
  { top: '14%', left: '22%', size: 5, delay: '0s' },
  { top: '26%', left: '68%', size: 4, delay: '0.8s' },
  { top: '58%', left: '12%', size: 3, delay: '1.6s' },
  { top: '72%', left: '82%', size: 5, delay: '0.4s' },
  { top: '40%', left: '48%', size: 3, delay: '2.2s' },
  { top: '84%', left: '38%', size: 4, delay: '1.2s' },
]
</script>

<template>
  <div class="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
    <!-- base wash -->
    <div
      class="absolute inset-0"
      style="
        background:
          radial-gradient(1200px 600px at 12% -6%, #efe9ff 0%, transparent 55%),
          radial-gradient(1000px 560px at 96% 4%, #e6f4ff 0%, transparent 52%),
          radial-gradient(900px 620px at 78% 108%, #ffeaf4 0%, transparent 52%),
          linear-gradient(180deg, #f8f6ff 0%, #f5f7ff 48%, #f4fbff 100%);
      "
    />
    <!-- floating blobs -->
    <div
      class="absolute -left-24 top-10 size-80 rounded-full blur-3xl opacity-60 animate-[var(--animate-float-slow)]"
      style="background: radial-gradient(circle, rgba(183, 166, 255, 0.55), transparent 70%)"
    />
    <div
      class="absolute right-[-6rem] top-40 size-96 rounded-full blur-3xl opacity-50 animate-[var(--animate-float-slower)]"
      style="background: radial-gradient(circle, rgba(124, 198, 248, 0.5), transparent 70%)"
    />
    <div
      class="absolute bottom-[-8rem] left-1/3 size-96 rounded-full blur-3xl opacity-45 animate-[var(--animate-float-slow)]"
      style="background: radial-gradient(circle, rgba(255, 158, 201, 0.45), transparent 70%)"
    />
    <!-- twinkling dots -->
    <span
      v-for="(s, i) in stars"
      :key="i"
      class="absolute rounded-full bg-white animate-[var(--animate-twinkle)]"
      :style="{
        top: s.top,
        left: s.left,
        width: `${s.size}px`,
        height: `${s.size}px`,
        animationDelay: s.delay,
        boxShadow: '0 0 8px rgba(255,255,255,0.9)',
      }"
    />
  </div>
</template>
