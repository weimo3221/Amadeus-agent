# desktop-ui-next

Standalone design prototype for the next Amadeus **Main UI** visual language.

This is a **design exploration only**. It does not connect to the WebSocket bridge
or the Python runtime — it renders the chat workbench from local mock data with
simulated interaction states. The goal is to define the target look and feel
(soft-gradient, light glassmorphism, rounded cards, light anime aesthetic) plus a
reusable component set and shared design tokens, then fold that direction back into
the real `apps/desktop` Main UI renderer.

## Stack

- Vue 3 (`<script setup lang="ts">`)
- Vite 7 + TypeScript
- Tailwind CSS v4 (`@theme` design tokens, `@tailwindcss/vite`)
- `@iconify/vue` (Phosphor `ph:` line / duotone icons)

## Run

```bash
npm install                       # from repo root (npm workspaces)
npm --workspace apps/desktop-ui-next run dev
# or, inside apps/desktop-ui-next:
npm run dev        # vite dev server on http://127.0.0.1:5178
npm run typecheck  # vue-tsc --noEmit
npm run build      # type check + production build
```

## Structure

```text
src/
  styles/main.css          # design tokens (color / radius / spacing / shadow / motion)
  types.ts                 # shared UI types
  mock/data.ts             # mock sessions / messages / plan / tasks / skills
  components/
    ui/                    # reusable Am* component library
      AmButton  AmInput  AmSelect  AmCard  AmTag
      AmTabs    AmTable   AmModal   AmEmptyState  AmLoading
    layout/                # AppBackground / AppSidebar / AppHeader
    workspace/             # SessionSwitcher / ChatMessage / ChatComposer
                           # PlanPanel / StatusTiles / WorkspaceView
  App.vue                  # assembles the workbench
```

## Design tokens

Tokens live in `src/styles/main.css` under a Tailwind v4 `@theme` block:
brand palette, accent tints, semantic colors, rounded radii (14 / 18 / 24px / pill),
an 8 / 12 / 16 / 24px spacing scale, soft/card/float/glow shadows, and shared
motion (`--ease-soft`, twinkle / rise-in animations).

> Note: because spacing tokens such as `--spacing-md: 16px` are declared in
> `@theme`, avoid Tailwind sizing utilities like `max-w-md` (they resolve to the
> token value). Use arbitrary values such as `max-w-[440px]` instead.
