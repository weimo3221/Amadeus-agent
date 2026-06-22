import { createReadStream, existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { basename, extname, resolve, sep } from 'node:path'
import type { ServerResponse } from 'node:http'

const SUPPORTED_LIVE2D_SUFFIXES = new Set([
  '.json',
  '.moc3',
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.wav',
  '.mp3',
])

const MIME_TYPES = new Map<string, string>([
  ['.json', 'application/json; charset=utf-8'],
  ['.moc3', 'application/octet-stream'],
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.webp', 'image/webp'],
  ['.wav', 'audio/wav'],
  ['.mp3', 'audio/mpeg'],
])

export interface Live2DModelConfig {
  id: string
  path: string
  url: string
}

export interface Live2DModelListItem {
  id: string
  path: string
  url: string
  active: boolean
}

interface HarnessLive2DConfig {
  modelId: string
  modelPath: string
}

export class LocalLive2DModelLibrary {
  constructor(
    private readonly rootDir: string,
    private readonly publicBaseUrl: string,
    private readonly harnessesConfigPath: string,
  ) {}

  configuredModel(): Live2DModelConfig | undefined {
    const config = parseHarnessesConfig(this.harnessesConfigPath)
    if (config.modelPath) {
      const normalized = this.normalizeModelPath(config.modelPath)
      if (this.resolvePublicPath(normalized)) {
        return {
          id: config.modelId,
          path: normalized,
          url: this.modelUrl(normalized),
        }
      }
    }

    const discovered = this.findModel(config.modelId)
    if (!discovered) {
      return undefined
    }

    return {
      id: config.modelId,
      path: discovered,
      url: this.modelUrl(discovered),
    }
  }

  listModels(): Live2DModelListItem[] {
    const active = this.configuredModel()
    const root = resolve(this.rootDir)
    if (!existsSync(root) || !statSync(root).isDirectory()) {
      return []
    }

    return readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => {
        const modelPath = this.findModel(entry.name)
        if (!modelPath) {
          return undefined
        }
        return {
          id: entry.name,
          path: modelPath,
          url: this.modelUrl(modelPath),
          active: active?.id === entry.name && active.path === modelPath,
        }
      })
      .filter((entry): entry is Live2DModelListItem => Boolean(entry))
      .sort((a, b) => a.id.localeCompare(b.id))
  }

  selectModel(modelId: string): Live2DModelConfig | undefined {
    const normalizedId = modelId.trim()
    if (!/^[a-zA-Z0-9._-]+$/.test(normalizedId)) {
      return undefined
    }

    const modelPath = this.findModel(normalizedId)
    if (!modelPath) {
      return undefined
    }

    this.persistConfiguredModel(normalizedId, modelPath)
    return {
      id: normalizedId,
      path: modelPath,
      url: this.modelUrl(modelPath),
    }
  }

  resolvePublicPath(relativePath: string): string | undefined {
    const normalized = this.normalizeModelPath(relativePath)
    const candidate = resolve(this.rootDir, normalized)
    const root = resolve(this.rootDir)
    if (candidate !== root && !candidate.startsWith(`${root}${sep}`)) {
      return undefined
    }

    if (!existsSync(candidate) || !statSync(candidate).isFile()) {
      return undefined
    }

    if (!SUPPORTED_LIVE2D_SUFFIXES.has(extname(candidate).toLowerCase())) {
      return undefined
    }

    return candidate
  }

  contentType(filePath: string): string {
    return MIME_TYPES.get(extname(filePath).toLowerCase()) ?? 'application/octet-stream'
  }

  normalizeModelPath(path: string): string {
    let normalized = path.replaceAll('\\', '/').replace(/^\/+/, '')
    const prefix = 'models/live2d/'
    if (normalized.startsWith(prefix)) {
      normalized = normalized.slice(prefix.length)
    }
    return normalized
  }

  private findModel(modelId: string): string | undefined {
    const modelDir = resolve(this.rootDir, modelId)
    if (!existsSync(modelDir) || !statSync(modelDir).isDirectory()) {
      return undefined
    }

    const direct = findFirstModel3Json(modelDir)
    if (!direct) {
      return undefined
    }

    return direct.slice(resolve(this.rootDir).length + 1).replaceAll(sep, '/')
  }

  private modelUrl(relativePath: string): string {
    return `${this.publicBaseUrl.replace(/\/$/, '')}/live2d/models/${encodeURI(relativePath)}`
  }

  private persistConfiguredModel(modelId: string, modelPath: string): void {
    const current = existsSync(this.harnessesConfigPath)
      ? readFileSync(this.harnessesConfigPath, 'utf8')
      : ''
    const next = updateHarnessLive2DModelConfig(current, modelId, modelPath)
    writeFileSync(this.harnessesConfigPath, next, 'utf8')
  }
}

export function writeLive2DConfig(response: ServerResponse, library: LocalLive2DModelLibrary): void {
  const model = library.configuredModel()
  if (!model) {
    writeJson(response, 404, { ok: false, error: 'live2d_model_not_configured' })
    return
  }

  writeJson(response, 200, { ok: true, model })
}

export function writeLive2DModels(response: ServerResponse, library: LocalLive2DModelLibrary): void {
  writeJson(response, 200, {
    ok: true,
    models: library.listModels(),
    activeModel: library.configuredModel(),
  })
}

export function writeLive2DSelection(
  response: ServerResponse,
  library: LocalLive2DModelLibrary,
  payload: unknown,
): void {
  const modelId = isRecord(payload) && typeof payload.modelId === 'string'
    ? payload.modelId
    : ''
  const model = library.selectModel(modelId)
  if (!model) {
    writeJson(response, 400, { ok: false, error: 'live2d_model_not_found' })
    return
  }

  writeJson(response, 200, { ok: true, model })
}

export function writeLive2DModelFile(response: ServerResponse, library: LocalLive2DModelLibrary, relativePath: string): void {
  const filePath = library.resolvePublicPath(decodeURIComponent(relativePath))
  if (!filePath) {
    writeJson(response, 404, { ok: false, error: 'live2d_model_file_not_found' })
    return
  }

  response.writeHead(200, {
    'Content-Type': library.contentType(filePath),
    'Access-Control-Allow-Origin': '*',
  })
  createReadStream(filePath).pipe(response)
}

function writeJson(response: ServerResponse, status: number, payload: Record<string, unknown>): void {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  })
  response.end(JSON.stringify(payload))
}

function parseHarnessesConfig(path: string): HarnessLive2DConfig {
  if (!existsSync(path)) {
    return { modelId: 'default', modelPath: '' }
  }

  const lines = readFileSync(path, 'utf8').split(/\r?\n/)
  let inHarnesses = false
  let inLive2D = false
  let inModel = false
  let modelId = 'default'
  let modelPath = ''

  for (const rawLine of lines) {
    const line = rawLine.split('#', 1)[0].trimEnd()
    if (!line.trim()) {
      continue
    }

    const indent = line.length - line.trimStart().length
    const trimmed = line.trim()
    if (indent === 0) {
      inHarnesses = trimmed === 'harnesses:'
      inLive2D = false
      inModel = false
      continue
    }

    if (!inHarnesses) {
      continue
    }

    if (indent === 2) {
      inLive2D = trimmed === 'live2d:'
      inModel = false
      continue
    }

    if (!inLive2D) {
      continue
    }

    if (indent === 4) {
      inModel = trimmed === 'model:'
      continue
    }

    if (indent === 6 && inModel && trimmed.includes(':')) {
      const [key, ...rest] = trimmed.split(':')
      const value = parseYamlScalar(rest.join(':').trim())
      if (key === 'id') {
        modelId = value || modelId
      }
      if (key === 'path') {
        modelPath = value
      }
    }
  }

  return { modelId, modelPath }
}

function parseYamlScalar(value: string): string {
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1)
  }
  return value
}

function updateHarnessLive2DModelConfig(content: string, modelId: string, modelPath: string): string {
  if (!content.trim()) {
    return [
      'harnesses:',
      '  live2d:',
      '    enabled: true',
      '    adapter: desktop-live2d',
      '    model:',
      `      id: ${modelId}`,
      `      path: ${modelPath}`,
      '',
    ].join('\n')
  }

  const lines = content.split(/\r?\n/)
  let inHarnesses = false
  let inLive2D = false
  let inModel = false
  let sawId = false
  let sawPath = false

  for (let index = 0; index < lines.length; index += 1) {
    const rawLine = lines[index]
    const line = rawLine.split('#', 1)[0].trimEnd()
    const trimmed = line.trim()
    if (!trimmed) {
      continue
    }

    const indent = line.length - line.trimStart().length
    if (indent === 0) {
      inHarnesses = trimmed === 'harnesses:'
      inLive2D = false
      inModel = false
      continue
    }

    if (!inHarnesses) {
      continue
    }

    if (indent === 2) {
      inLive2D = trimmed === 'live2d:'
      inModel = false
      continue
    }

    if (!inLive2D) {
      continue
    }

    if (indent === 4) {
      inModel = trimmed === 'model:'
      continue
    }

    if (indent === 6 && inModel && trimmed.includes(':')) {
      const [key] = trimmed.split(':')
      if (key === 'id') {
        lines[index] = `      id: ${modelId}`
        sawId = true
      }
      if (key === 'path') {
        lines[index] = `      path: ${modelPath}`
        sawPath = true
      }
    }
  }

  if (sawId && sawPath) {
    return `${lines.join('\n').replace(/\n*$/, '')}\n`
  }

  return [
    'harnesses:',
    '  live2d:',
    '    enabled: true',
    '    adapter: desktop-live2d',
    '    model:',
    `      id: ${modelId}`,
    `      path: ${modelPath}`,
    '',
  ].join('\n')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function findFirstModel3Json(root: string): string | undefined {
  const stack = [root]
  while (stack.length) {
    const dir = stack.shift()
    if (!dir) {
      continue
    }
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const fullPath = resolve(dir, entry.name)
      if (entry.isDirectory()) {
        stack.push(fullPath)
      }
      else if (entry.isFile() && basename(entry.name).endsWith('.model3.json')) {
        return fullPath
      }
    }
  }
  return undefined
}
