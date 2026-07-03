const query = new URLSearchParams(window.location.search)

export const AGENT_HTTP_URL =
  query.get('agentHttpUrl') || import.meta.env.VITE_AGENT_HTTP_URL || 'http://127.0.0.1:8790'

export const BASE_AGENT_WS_URL =
  query.get('agentWsUrl') || import.meta.env.VITE_AGENT_WS_URL || 'ws://127.0.0.1:8788/ws'

export const SESSION_ID =
  query.get('sessionId') || import.meta.env.VITE_AMADEUS_SESSION_ID || 'companion:default'

export function wsUrlForSurface(url: string, surface: string, sessionId: string): string {
  const parsed = new URL(url)
  parsed.searchParams.set('surface', surface)
  parsed.searchParams.set('sessionId', sessionId)
  return parsed.toString()
}
