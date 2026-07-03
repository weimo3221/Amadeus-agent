# desktop-ui-next

Next-generation Amadeus **Main UI** — a Vue 3 workbench that connects to the live
Amadeus runtime.

This app started as a design exploration and now wires into the real backend: it
streams chat over the WebSocket bridge and reads/writes session, role, skill, and
memory state over the Python runtime's HTTP API. It defines the target look and
feel (soft-gradient, light glassmorphism, rounded cards, light anime aesthetic)
plus a reusable component set and shared design tokens.

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

## Runtime connection

The UI talks to two backends (defaults shown), configurable via env vars or URL
query params:

| Purpose            | Default                     | Env var                   | Query param    |
| ------------------ | --------------------------- | ------------------------- | -------------- |
| Chat WebSocket     | `ws://127.0.0.1:8788/ws`    | `VITE_AGENT_WS_URL`       | `agentWsUrl`   |
| Runtime HTTP API   | `http://127.0.0.1:8790`     | `VITE_AGENT_HTTP_URL`     | `agentHttpUrl` |
| Session id         | `companion:default`         | `VITE_AMADEUS_SESSION_ID` | `sessionId`    |

The WebSocket is opened per surface as
`ws://127.0.0.1:8788/ws?surface=main-ui&sessionId=<id>`. Runtime state is fetched
directly from the Python runtime over HTTP; endpoints in use include:

- `GET /sessions`, `POST /sessions`, `DELETE /sessions/:id` (archive)
- `GET /roles`, `PUT /roles/:id` (name / persona / style / provider / model)
- `GET /skills/list`
- `GET /memory/items`

Some endpoints (`/tasks`, `/scheduled-jobs`, `/sessions/:id/plan`) may not be
available depending on runtime state; the UI degrades gracefully to empty lists.

## Structure

```text
src/
  styles/main.css          # design tokens (color / radius / spacing / shadow / motion)
  types.ts                 # shared UI types
  runtime/                 # backend connection layer
    config.ts              #   resolves WS / HTTP URLs + session id
    client.ts              #   WebSocket chat client
    http.ts                #   runtime HTTP API (sessions / roles / skills / memory)
  composables/
    useRuntime.ts          # singleton store: bootstrap + reactive runtime state
  components/
    ui/                    # reusable Am* component library
      AmButton  AmInput  AmSelect  AmCard  AmTag
      AmTabs    AmTable   AmModal   AmEmptyState  AmLoading
    layout/                # AppBackground / AppSidebar / AppHeader
    workspace/             # SessionSwitcher / ChatMessage / ChatComposer / PlanPanel
                           # WorkspaceView (overview)
                           # TasksView / SkillsView / ScheduleView / MemoryView / SettingsView
  App.vue                  # sidebar navigation drives the active workspace view
```

Sidebar navigation (`AppSidebar` → `App.vue` `activeNav`) switches the main area
between the chat workbench (`WorkspaceView`) and the dedicated views
(`TasksView`, `SkillsView`, `ScheduleView`, `MemoryView`, `SettingsView`).

## Design tokens

Tokens live in `src/styles/main.css` under a Tailwind v4 `@theme` block:
brand palette, accent tints, semantic colors, rounded radii (14 / 18 / 24px / pill),
an 8 / 12 / 16 / 24px spacing scale, soft/card/float/glow shadows, and shared
motion (`--ease-soft`, twinkle / rise-in animations).

> Note: because spacing tokens such as `--spacing-md: 16px` are declared in
> `@theme`, avoid Tailwind sizing utilities like `max-w-md` (they resolve to the
> token value). Use arbitrary values such as `max-w-[440px]` instead.
