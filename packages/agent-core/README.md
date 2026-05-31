# Agent Core

LLM conversation loop and tool calling.

Initial scope:

- OpenAI-compatible chat provider
- streaming response
- tool call dispatch
- memory injection
- behavior side-channel events

Keep provider code behind adapters so local models and hosted APIs can share the same interface.
