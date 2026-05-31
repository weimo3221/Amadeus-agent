# Server App

Local agent runtime process.

Responsibilities:

- WebSocket and HTTP endpoints for the desktop app
- LLM provider adapters
- agent loop
- tool execution
- memory persistence
- behavior events for the Live2D character

Start with a Node.js runtime. Add Python workers later only if a specific agent framework requires it.
