import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
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
const maxReadFileBytes = 512 * 1024
const searchTargets = new Set(['all', 'files', 'content'])
const defaultReadFileChars = 12000
const maxReadFileChars = 20000
const defaultReadFileLineLimit = 200
const maxReadFileLineLimit = 1000
const maxPatchFileBytes = 512 * 1024
const maxPatchTextBytes = 512 * 1024
const maxPatchDiffChars = 6000

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

function isRestrictedPath(path: string): boolean {
  return relative(repoRoot, path)
    .split(/[\\/]/)
    .some((part) => skippedSearchDirs.has(part))
}

function countOccurrences(content: string, needle: string): number {
  if (!needle) {
    return 0
  }

  let count = 0
  let index = content.indexOf(needle)
  while (index !== -1) {
    count += 1
    index = content.indexOf(needle, index + needle.length)
  }
  return count
}

function normalizePatchLineEndings(content: string, oldText: string, newText: string): { oldText: string, newText: string } {
  if (content.includes(oldText)) {
    return { oldText, newText }
  }

  if (content.includes('\r\n') && !oldText.includes('\r\n')) {
    const oldCrLf = oldText.replace(/\n/g, '\r\n')
    const newCrLf = newText.replace(/\n/g, '\r\n')
    if (content.includes(oldCrLf)) {
      return { oldText: oldCrLf, newText: newCrLf }
    }
  }

  return { oldText, newText }
}

function diffPreview(path: string, before: string, after: string): { diff: string, diffTruncated: boolean } {
  const beforeLines = before.split(/\r?\n/)
  const afterLines = after.split(/\r?\n/)
  const lines = [`--- a/${path}`, `+++ b/${path}`]
  const maxLines = Math.max(beforeLines.length, afterLines.length)
  for (let index = 0; index < maxLines; index += 1) {
    const beforeLine = beforeLines[index]
    const afterLine = afterLines[index]
    if (beforeLine === afterLine) {
      if (beforeLine !== undefined) {
        lines.push(` ${beforeLine}`)
      }
      continue
    }
    if (beforeLine !== undefined) {
      lines.push(`-${beforeLine}`)
    }
    if (afterLine !== undefined) {
      lines.push(`+${afterLine}`)
    }
  }

  const diff = `${lines.join('\n')}\n`
  if (diff.length <= maxPatchDiffChars) {
    return { diff, diffTruncated: false }
  }
  return { diff: diff.slice(0, maxPatchDiffChars), diffTruncated: true }
}

function searchLocalFiles(args: Record<string, unknown>): string {
  const query = typeof args.query === 'string' ? args.query.trim() : ''
  if (!query) {
    return JSON.stringify({ error: 'query is required' })
  }

  const target = typeof args.target === 'string' && searchTargets.has(args.target) ? args.target : 'all'
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
    if ((target === 'all' || target === 'files') && relativePath.toLowerCase().includes(normalizedQuery)) {
      results.push({ path: relativePath, preview: relativePath, match: 'path' })
      continue
    }

    if (target === 'files') {
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
    target,
    root: relative(repoRoot, searchRoot).replace(/\\/g, '/') || '.',
    maxResults,
    results,
    scannedFiles,
  })
}

function readLocalFile(args: Record<string, unknown>): string {
  const pathText = typeof args.path === 'string' ? args.path.trim() : ''
  if (!pathText) {
    return JSON.stringify({ error: 'path is required' })
  }

  const targetPath = resolve(repoRoot, pathText)
  if (!isInside(targetPath, repoRoot)) {
    return JSON.stringify({ error: 'path must be inside the project workspace' })
  }

  if (!existsSync(targetPath)) {
    return JSON.stringify({ error: 'path must point to an existing file' })
  }

  let stats
  try {
    stats = statSync(targetPath)
  }
  catch {
    return JSON.stringify({ error: 'could not inspect file' })
  }

  if (!stats.isFile()) {
    return JSON.stringify({ error: 'path must point to an existing file' })
  }

  if (!searchableExtensions.has(extname(targetPath).toLowerCase())) {
    return JSON.stringify({ error: 'file type is not readable by this tool' })
  }

  if (stats.size > maxReadFileBytes) {
    return JSON.stringify({ error: 'file is too large to read safely' })
  }

  const startLine = args.startLine !== undefined
    ? normalizePositiveInteger(args.startLine, 1, 1, 1_000_000)
    : args.offset !== undefined
      ? normalizePositiveInteger(args.offset, 0, 0, 1_000_000) + 1
      : 1
  const lineLimit = args.lineLimit !== undefined
    ? normalizePositiveInteger(args.lineLimit, defaultReadFileLineLimit, 1, maxReadFileLineLimit)
    : args.limit !== undefined
      ? normalizePositiveInteger(args.limit, defaultReadFileLineLimit, 1, maxReadFileLineLimit)
      : defaultReadFileLineLimit
  const maxChars = normalizePositiveInteger(args.maxChars, defaultReadFileChars, 1, maxReadFileChars)
  let content = ''
  try {
    content = readFileSync(targetPath, 'utf8')
  }
  catch {
    return JSON.stringify({ error: 'file is not readable as utf-8 text' })
  }

  const lines = content.split(/\r?\n/)
  const totalLines = lines.length === 1 && lines[0] === '' ? 0 : lines.length
  const startIndex = Math.min(startLine - 1, totalLines)
  const endIndex = Math.min(startIndex + lineLimit, totalLines)
  const selectedLines = lines.slice(startIndex, endIndex)
  const renderedLines: string[] = []
  let truncatedByChars = false

  for (let index = 0; index < selectedLines.length; index += 1) {
    const lineNumber = startIndex + index + 1
    const renderedLine = `${String(lineNumber).padStart(6, ' ')} | ${selectedLines[index]}`
    const current = renderedLines.join('\n')
    const projected = current ? `${current}\n${renderedLine}` : renderedLine
    if (projected.length > maxChars) {
      const remaining = maxChars - (current.length + (current ? 1 : 0))
      if (remaining > 0) {
        renderedLines.push(renderedLine.slice(0, remaining))
      }
      truncatedByChars = true
      break
    }
    renderedLines.push(renderedLine)
  }

  const returnedLineCount = renderedLines.length
  const returnedEndLine = startIndex + returnedLineCount
  return JSON.stringify({
    path: relative(repoRoot, targetPath).replace(/\\/g, '/'),
    sizeBytes: stats.size,
    charCount: content.length,
    totalLines,
    startLine: totalLines ? startIndex + 1 : 1,
    endLine: returnedEndLine,
    lineCount: returnedLineCount,
    lineLimit,
    maxChars,
    hasMore: endIndex < totalLines || truncatedByChars,
    truncated: truncatedByChars,
    content: renderedLines.join('\n'),
  })
}

function patchLocalFile(args: Record<string, unknown>): string {
  const pathText = typeof args.path === 'string' ? args.path.trim() : ''
  if (!pathText) {
    return JSON.stringify({ error: 'path is required' })
  }

  const oldArg = typeof args.oldText === 'string' ? args.oldText : typeof args.old_string === 'string' ? args.old_string : undefined
  const newArg = typeof args.newText === 'string' ? args.newText : typeof args.new_string === 'string' ? args.new_string : undefined
  if (oldArg === undefined || newArg === undefined) {
    return JSON.stringify({ error: 'oldText and newText are required' })
  }
  if (!oldArg) {
    return JSON.stringify({ error: 'oldText cannot be empty' })
  }
  if (oldArg === newArg) {
    return JSON.stringify({ error: 'oldText and newText are identical' })
  }

  const targetPath = resolve(repoRoot, pathText)
  if (!isInside(targetPath, repoRoot)) {
    return JSON.stringify({ error: 'path must be inside the project workspace' })
  }
  if (isRestrictedPath(targetPath)) {
    return JSON.stringify({ error: 'path is restricted and cannot be patched' })
  }
  if (!existsSync(targetPath)) {
    return JSON.stringify({ error: 'path must point to an existing file' })
  }

  let stats
  try {
    stats = statSync(targetPath)
  }
  catch {
    return JSON.stringify({ error: 'could not inspect file' })
  }

  if (!stats.isFile()) {
    return JSON.stringify({ error: 'path must point to an existing file' })
  }
  if (!searchableExtensions.has(extname(targetPath).toLowerCase())) {
    return JSON.stringify({ error: 'file type is not patchable by this tool' })
  }
  if (stats.size > maxPatchFileBytes) {
    return JSON.stringify({ error: 'file is too large to patch safely' })
  }

  let content = ''
  try {
    content = readFileSync(targetPath, 'utf8')
  }
  catch {
    return JSON.stringify({ error: 'file is not readable as utf-8 text' })
  }

  const { oldText, newText } = normalizePatchLineEndings(content, oldArg, newArg)
  const matchCount = countOccurrences(content, oldText)
  const replaceAll = typeof args.replaceAll === 'boolean'
    ? args.replaceAll
    : typeof args.replace_all === 'boolean'
      ? args.replace_all
      : false

  if (matchCount === 0) {
    return JSON.stringify({ error: 'oldText was not found; use read_file to verify the current file contents' })
  }
  if (matchCount > 1 && !replaceAll) {
    return JSON.stringify({
      error: 'oldText matched multiple times; include more surrounding context or set replaceAll=true',
      matchCount,
    })
  }

  const newContent = replaceAll ? content.split(oldText).join(newText) : content.replace(oldText, newText)
  const newSizeBytes = Buffer.byteLength(newContent, 'utf8')
  if (newSizeBytes > maxPatchTextBytes) {
    return JSON.stringify({ error: 'patched file would be too large' })
  }

  const relativePath = relative(repoRoot, targetPath).replace(/\\/g, '/')
  const { diff, diffTruncated } = diffPreview(relativePath, content, newContent)
  try {
    writeFileSync(targetPath, newContent, 'utf8')
  }
  catch {
    return JSON.stringify({ error: 'could not write patched file' })
  }

  return JSON.stringify({
    path: relativePath,
    changed: true,
    replacements: replaceAll ? matchCount : 1,
    replaceAll,
    sizeBytesBefore: stats.size,
    sizeBytesAfter: newSizeBytes,
    diff,
    diffTruncated,
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
    search_files: {
      name: 'search_files',
      displayName: 'Searching local files',
      permission: 'ask',
      enabled: true,
      schema: {
        type: 'function',
        function: {
          name: 'search_files',
          description: 'Search workspace-relative filenames and small text file contents. Use target="files" for path/name search, target="content" for text search, or target="all" when either can satisfy the request.',
          parameters: {
            type: 'object',
            properties: {
              query: {
                type: 'string',
                description: 'Search text to match in paths or file contents.',
              },
              target: {
                type: 'string',
                enum: ['all', 'files', 'content'],
                description: 'Search mode. Use "files" for filenames/paths, "content" for text contents, and "all" for both. Defaults to "all".',
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
      execute: executeTool('search_files', searchLocalFiles),
    },
    local_file_search: {
      name: 'local_file_search',
      displayName: 'Searching local files',
      permission: 'ask',
      enabled: false,
      schema: {
        type: 'function',
        function: {
          name: 'local_file_search',
          description: 'Legacy alias for search_files. Prefer search_files for new calls.',
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
    read_file: {
      name: 'read_file',
      displayName: 'Reading local file',
      permission: 'ask',
      enabled: true,
      schema: {
        type: 'function',
        function: {
          name: 'read_file',
          description: 'Read a small UTF-8 text file inside the project workspace. Use this after search_files when the user needs the contents of a specific project file.',
          parameters: {
            type: 'object',
            properties: {
              path: {
                type: 'string',
                description: 'Workspace-relative file path to read.',
              },
              maxChars: {
                type: 'number',
                description: 'Maximum rendered characters to return from this explicit read window. Defaults to 12000 and is capped at 20000.',
              },
              startLine: {
                type: 'number',
                description: '1-based first line to read. Defaults to 1.',
              },
              lineLimit: {
                type: 'number',
                description: 'Maximum lines to return. Defaults to 200 and is capped at 1000.',
              },
            },
            required: ['path'],
            additionalProperties: false,
          },
        },
      },
      describeRequest: (args) => {
        const path = typeof args.path === 'string' && args.path.trim() ? args.path.trim() : '(empty path)'
        return `Allow Amadeus to read local project file ${path}?`
      },
      execute: executeTool('read_file', readLocalFile),
    },
    patch: {
      name: 'patch',
      displayName: 'Patching local file',
      permission: 'ask',
      enabled: true,
      schema: {
        type: 'function',
        function: {
          name: 'patch',
          description: 'Apply a safe single-file text replacement inside the project workspace. Use this for local edits after read_file. oldText must uniquely identify the target text unless replaceAll=true.',
          parameters: {
            type: 'object',
            properties: {
              path: {
                type: 'string',
                description: 'Workspace-relative file path to patch.',
              },
              oldText: {
                type: 'string',
                description: 'Exact text to replace. Include enough surrounding context to make it unique.',
              },
              newText: {
                type: 'string',
                description: 'Replacement text.',
              },
              replaceAll: {
                type: 'boolean',
                description: 'Replace every occurrence. Defaults to false; by default oldText must be unique.',
              },
            },
            required: ['path', 'oldText', 'newText'],
            additionalProperties: false,
          },
        },
      },
      describeRequest: (args) => {
        const path = typeof args.path === 'string' && args.path.trim() ? args.path.trim() : '(empty path)'
        return `Allow Amadeus to patch local project file ${path}?`
      },
      execute: executeTool('patch', patchLocalFile),
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
