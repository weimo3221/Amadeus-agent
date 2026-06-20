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
  fetchImpl?: typeof fetch
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

export interface PythonToolList {
  tools: ToolPermissionState[]
  schemas: ToolSchema[]
}

interface PythonToolResponse {
  ok?: boolean
  result?: unknown
  error?: string
}

interface PythonToolListResponse {
  ok?: boolean
  tools?: unknown
  schemas?: unknown
}

function runtimeEndpoint(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, '')}${path}`
}

function isToolPermissionState(value: unknown): value is ToolPermissionState {
  if (!value || typeof value !== 'object') {
    return false
  }

  const candidate = value as Partial<ToolPermissionState>
  return (
    typeof candidate.name === 'string'
    && typeof candidate.displayName === 'string'
    && typeof candidate.enabled === 'boolean'
    && (candidate.permission === 'allow' || candidate.permission === 'ask' || candidate.permission === 'deny')
  )
}

function isToolSchema(value: unknown): value is ToolSchema {
  if (!value || typeof value !== 'object') {
    return false
  }

  const candidate = value as Partial<ToolSchema>
  const fn = candidate.function
  return (
    candidate.type === 'function'
    && !!fn
    && typeof fn === 'object'
    && typeof fn.name === 'string'
    && typeof fn.description === 'string'
    && !!fn.parameters
    && typeof fn.parameters === 'object'
  )
}

export async function fetchPythonToolList(backend: PythonToolBackend): Promise<PythonToolList | undefined> {
  const fetchImpl = backend.fetchImpl ?? fetch
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), backend.timeoutMs ?? 3000)

  try {
    const response = await fetchImpl(runtimeEndpoint(backend.baseUrl, '/tools/list'), {
      method: 'GET',
      signal: controller.signal,
    })
    const payload = await response.json().catch(() => undefined) as PythonToolListResponse | undefined

    if (!response.ok || !payload?.ok || !Array.isArray(payload.tools) || !Array.isArray(payload.schemas)) {
      return undefined
    }

    const tools = payload.tools.filter(isToolPermissionState)
    const schemas = payload.schemas.filter(isToolSchema)
    if (tools.length !== payload.tools.length || schemas.length !== payload.schemas.length) {
      return undefined
    }

    return { tools, schemas }
  }
  catch {
    return undefined
  }
  finally {
    clearTimeout(timeout)
  }
}

export async function fetchPythonToolPermissions(backend: PythonToolBackend): Promise<ToolPermissionState[] | undefined> {
  const toolList = await fetchPythonToolList(backend)
  return toolList?.tools
}

export async function executePythonTool(
  backend: PythonToolBackend,
  toolName: string,
  args: Record<string, unknown>,
): Promise<string> {
  const fetchImpl = backend.fetchImpl ?? fetch
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), backend.timeoutMs ?? 30000)

  try {
    const response = await fetchImpl(runtimeEndpoint(backend.baseUrl, '/tools/execute'), {
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
