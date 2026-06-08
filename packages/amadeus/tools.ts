import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { basename, dirname, extname, isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export interface ToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export interface ToolSchema {
  type: 'function'
  function: {
    name: string
    description: string
    parameters: Record<string, unknown>
  }
}

export interface ToolContext {
  sessionId: string
}

export interface PythonToolBackend {
  baseUrl: string
  timeoutMs?: number
}

export type ToolPermission = 'allow' | 'ask' | 'deny'

export interface RegisteredTool {
  name: string
  displayName: string
  permission: ToolPermission
  enabled: boolean
  schema: ToolSchema
  describeRequest?: (args: Record<string, unknown>) => string
  execute: (args: Record<string, unknown>, context: ToolContext) => string | Promise<string>
}

export interface ToolPermissionState {
  name: string
  displayName: string
  enabled: boolean
  permission: ToolPermission
}

interface PythonToolResponse {
  ok?: boolean
  result?: unknown
  error?: string
}

interface ToolConfigEntry {
  enabled?: boolean
  permission?: string
}

const plannedToolConfigNames = new Set([
  'web_search',
  'open_url',
  'reminders',
  'mcp',
])

const packageDir = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(packageDir, '../..')
const skippedSearchDirs = new Set(['.git', 'node_modules', 'dist', 'out', 'build', '.vite', '__pycache__'])
const searchableExtensions = new Set([
  '.css',
  '.html',
  '.js',
  '.json',
  '.md',
  '.py',
  '.ts',
  '.tsx',
  '.txt',
  '.yaml',
  '.yml',
])

export function normalizePositiveInteger(value: unknown, fallback: number, min: number, max: number): number {
  const number = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(number)) {
    return fallback
  }

  return Math.max(min, Math.min(max, Math.floor(number)))
}

function isInside(path: string, parent: string): boolean {
  const relativePath = relative(parent, path)
  return relativePath === '' || (!!relativePath && !relativePath.startsWith('..') && !isAbsolute(relativePath))
}

function searchLocalFiles(args: Record<string, unknown>): string {
  const query = typeof args.query === 'string' ? args.query.trim() : ''
  if (!query) {
    return JSON.stringify({ error: 'query is required' })
  }

  const maxResults = normalizePositiveInteger(args.maxResults, 10, 1, 30)
  const requestedRoot = typeof args.root === 'string' && args.root.trim() ? args.root.trim() : '.'
  const searchRoot = resolve(repoRoot, requestedRoot)
  if (!isInside(searchRoot, repoRoot) || !existsSync(searchRoot)) {
    return JSON.stringify({ error: 'root must be inside the project workspace' })
  }

  const normalizedQuery = query.toLowerCase()
  const results: Array<{ path: string; line?: number; preview: string; match: 'path' | 'content' }> = []
  const pending = [searchRoot]
  let scannedFiles = 0

  while (pending.length > 0 && results.length < maxResults && scannedFiles < 1000) {
    const current = pending.pop()!
    let stats
    try {
      stats = statSync(current)
    }
    catch {
      continue
    }

    if (stats.isDirectory()) {
      if (skippedSearchDirs.has(basename(current))) {
        continue
      }

      try {
        for (const entry of readdirSync(current)) {
          pending.push(resolve(current, entry))
        }
      }
      catch {
        continue
      }
      continue
    }

    if (!stats.isFile()) {
      continue
    }

    scannedFiles += 1
    const relativePath = relative(repoRoot, current).replace(/\\/g, '/')
    if (relativePath.toLowerCase().includes(normalizedQuery)) {
      results.push({ path: relativePath, preview: relativePath, match: 'path' })
      continue
    }

    if (stats.size > 256 * 1024 || !searchableExtensions.has(extname(current).toLowerCase())) {
      continue
    }

    let text = ''
    try {
      text = readFileSync(current, 'utf8')
    }
    catch {
      continue
    }

    const lines = text.split(/\r?\n/)
    const lineIndex = lines.findIndex((line) => line.toLowerCase().includes(normalizedQuery))
    if (lineIndex >= 0) {
      results.push({
        path: relativePath,
        line: lineIndex + 1,
        preview: lines[lineIndex].trim().slice(0, 240),
        match: 'content',
      })
    }
  }

  return JSON.stringify({
    query,
    root: relative(repoRoot, searchRoot).replace(/\\/g, '/') || '.',
    maxResults,
    results,
    scannedFiles,
  })
}

async function executePythonTool(
  backend: PythonToolBackend,
  toolName: string,
  args: Record<string, unknown>,
): Promise<string> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), backend.timeoutMs ?? 30000)

  try {
    const response = await fetch(`${backend.baseUrl.replace(/\/$/, '')}/tools/execute`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        toolName,
        args,
      }),
      signal: controller.signal,
    })

    const payload = await response.json().catch(() => undefined) as PythonToolResponse | undefined
    if (!response.ok || !payload?.ok) {
      const error = payload?.error || response.statusText || 'Python tool execution failed'
      return JSON.stringify({ error })
    }

    return JSON.stringify(payload.result ?? {})
  }
  catch (error) {
    return JSON.stringify({
      error: error instanceof Error ? error.message : 'Python tool execution failed',
    })
  }
  finally {
    clearTimeout(timeout)
  }
}

function parseBoolean(value: string): boolean | undefined {
  if (value === 'true') {
    return true
  }

  if (value === 'false') {
    return false
  }

  return undefined
}

function parseToolsConfig(path: string): Record<string, ToolConfigEntry> {
  if (!existsSync(path)) {
    console.warn(`Tool config not found: ${path}`)
    return {}
  }

  const entries: Record<string, ToolConfigEntry> = {}
  const lines = readFileSync(path, 'utf8').split(/\r?\n/)
  let inTools = false
  let currentTool: string | undefined

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+#.*$/, '')
    if (!line.trim()) {
      continue
    }

    const indent = line.match(/^\s*/)?.[0].length ?? 0
    const trimmed = line.trim()

    if (indent === 0) {
      inTools = trimmed === 'tools:'
      currentTool = undefined
      continue
    }

    if (!inTools) {
      continue
    }

    if (indent === 2 && trimmed.endsWith(':')) {
      currentTool = trimmed.slice(0, -1)
      entries[currentTool] = {}
      continue
    }

    if (indent !== 4 || !currentTool) {
      continue
    }

    const separator = trimmed.indexOf(':')
    if (separator < 0) {
      continue
    }

    const key = trimmed.slice(0, separator).trim()
    const value = trimmed.slice(separator + 1).trim()

    if (key === 'enabled') {
      entries[currentTool].enabled = parseBoolean(value)
      continue
    }

    if (key === 'permission') {
      entries[currentTool].permission = value
    }
  }

  return entries
}

function resolveConfiguredToolName(name: string): string {
  if (name === 'time') {
    return 'get_current_time'
  }

  return name
}

export function applyToolConfig(registry: Record<string, RegisteredTool>, path: string): void {
  const entries = parseToolsConfig(path)
  const applied = new Set<string>()

  for (const [configuredName, entry] of Object.entries(entries)) {
    const toolName = resolveConfiguredToolName(configuredName)
    const tool = registry[toolName]

    if (!tool) {
      if (plannedToolConfigNames.has(configuredName) && entry.enabled === false) {
        continue
      }

      if (plannedToolConfigNames.has(configuredName)) {
        console.warn(`Tool configured but not implemented yet: ${configuredName}`)
        continue
      }

      console.warn(`Unknown tool in configs/tools.yaml: ${configuredName}`)
      continue
    }

    if (applied.has(toolName)) {
      console.warn(`Duplicate tool config for ${toolName}; keeping the first valid entry.`)
      continue
    }

    applied.add(toolName)

    if (entry.enabled === undefined && Object.hasOwn(entry, 'enabled')) {
      console.warn(`Invalid enabled value for tool ${configuredName}; expected true or false.`)
    }
    else if (entry.enabled !== undefined) {
      tool.enabled = entry.enabled
    }

    if (entry.permission !== undefined) {
      if (entry.permission === 'allow' || entry.permission === 'ask' || entry.permission === 'deny') {
        tool.permission = entry.permission
      }
      else {
        console.warn(`Invalid permission for tool ${configuredName}: ${entry.permission}. Expected allow, ask, or deny.`)
      }
    }
  }
}

export function createDefaultToolRegistry(options: {
  pythonBackend?: PythonToolBackend
} = {}): Record<string, RegisteredTool> {
  const executeTool = (
    toolName: string,
    fallback: (args: Record<string, unknown>) => string,
  ): RegisteredTool['execute'] => {
    if (!options.pythonBackend) {
      return fallback
    }

    return (args) => executePythonTool(options.pythonBackend!, toolName, args)
  }

  return {
    get_current_time: {
      name: 'get_current_time',
      displayName: 'Reading current time',
      permission: 'allow',
      enabled: true,
      schema: {
        type: 'function',
        function: {
          name: 'get_current_time',
          description: 'Get the current local date and time. Use this when the user asks about current time, date, today, now, or scheduling context.',
          parameters: {
            type: 'object',
            properties: {
              timeZone: {
                type: 'string',
                description: 'IANA timezone. Defaults to Asia/Shanghai.',
              },
            },
            additionalProperties: false,
          },
        },
      },
      execute: executeTool('get_current_time', (args) => {
        const timeZone = typeof args.timeZone === 'string' && args.timeZone ? args.timeZone : 'Asia/Shanghai'
        const now = new Date()
        const formatter = new Intl.DateTimeFormat('zh-CN', {
          timeZone,
          dateStyle: 'full',
          timeStyle: 'medium',
        })
        return JSON.stringify({
          iso: now.toISOString(),
          timeZone,
          formatted: formatter.format(now),
        })
      }),
    },
    roll_dice: {
      name: 'roll_dice',
      displayName: 'Rolling dice',
      permission: 'ask',
      enabled: true,
      schema: {
        type: 'function',
        function: {
          name: 'roll_dice',
          description: 'Roll dice and return the random results. Use this when the user asks to roll dice.',
          parameters: {
            type: 'object',
            properties: {
              sides: {
                type: 'number',
                description: 'Number of sides per die. Defaults to 6.',
              },
              count: {
                type: 'number',
                description: 'Number of dice to roll. Defaults to 1 and is capped at 20.',
              },
            },
            additionalProperties: false,
          },
        },
      },
      describeRequest: (args) => {
        const sides = normalizePositiveInteger(args.sides, 6, 2, 1000)
        const count = normalizePositiveInteger(args.count, 1, 1, 20)
        return `Allow Amadeus to roll ${count} d${sides}?`
      },
      execute: executeTool('roll_dice', (args) => {
        const sides = normalizePositiveInteger(args.sides, 6, 2, 1000)
        const count = normalizePositiveInteger(args.count, 1, 1, 20)
        const rolls = Array.from({ length: count }, () => Math.floor(Math.random() * sides) + 1)
        return JSON.stringify({
          sides,
          count,
          rolls,
          total: rolls.reduce((sum, value) => sum + value, 0),
        })
      }),
    },
    local_file_search: {
      name: 'local_file_search',
      displayName: 'Searching local files',
      permission: 'ask',
      enabled: true,
      schema: {
        type: 'function',
        function: {
          name: 'local_file_search',
          description: 'Search filenames and small text files inside the project workspace. Use this when the user asks to find local project files, docs, code, configuration, or notes.',
          parameters: {
            type: 'object',
            properties: {
              query: {
                type: 'string',
                description: 'Search text to match in paths or file contents.',
              },
              root: {
                type: 'string',
                description: 'Optional workspace-relative directory to search. Defaults to the project root.',
              },
              maxResults: {
                type: 'number',
                description: 'Maximum results to return. Defaults to 10 and is capped at 30.',
              },
            },
            required: ['query'],
            additionalProperties: false,
          },
        },
      },
      describeRequest: (args) => {
        const query = typeof args.query === 'string' && args.query.trim() ? args.query.trim() : '(empty query)'
        const root = typeof args.root === 'string' && args.root.trim() ? args.root.trim() : '.'
        return `Allow Amadeus to search local project files under ${root} for "${query}"?`
      },
      execute: executeTool('local_file_search', searchLocalFiles),
    },
  }
}

export function getEnabledToolSchemas(registry: Record<string, RegisteredTool>): ToolSchema[] {
  return Object.values(registry)
    .filter((tool) => tool.enabled && tool.permission !== 'deny')
    .map((tool) => tool.schema)
}

export function getToolPermissionState(registry: Record<string, RegisteredTool>): ToolPermissionState[] {
  return Object.values(registry).map((tool) => ({
    name: tool.name,
    displayName: tool.displayName,
    enabled: tool.enabled,
    permission: tool.permission,
  }))
}
