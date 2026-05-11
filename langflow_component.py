"""
Langflow custom component — DeepAgent.

Drop this file into your Langflow custom_components/ directory.
The component wraps the Deep Agent FastAPI server, consuming SSE and
returning the full assistant response as a Langflow Message.

Inputs:
  message     — chat input text (from Chat Input component)
  thread_id   — conversation thread (leave blank to auto-create per session)
  server_url  — base URL of the FastAPI server, e.g. http://localhost:8000

Usage in Langflow:
  Chat Input → DeepAgent → Chat Output
"""

from __future__ import annotations

import json
import uuid

import httpx
from langflow.custom import Component
from langflow.inputs import MessageTextInput, StrInput
from langflow.schema import Data
from langflow.template import Output


class DeepAgentComponent(Component):
    display_name = "DeepAgent"
    description  = "Streams responses from the Deep Agent FastAPI server."
    icon         = "bot"

    inputs = [
        MessageTextInput(
            name="message",
            display_name="Message",
            info="The user message to send to the agent.",
        ),
        StrInput(
            name="thread_id",
            display_name="Thread ID",
            info="Conversation thread ID. Leave blank to auto-generate per session.",
            value="",
            advanced=True,
        ),
        StrInput(
            name="server_url",
            display_name="Server URL",
            info="Base URL of the Deep Agent FastAPI server.",
            value="http://localhost:8000",
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="run_agent"),
    ]

    def run_agent(self) -> Data:
        thread_id = self.thread_id or str(uuid.uuid4())
        server    = self.server_url.rstrip("/")
        message   = self.message if isinstance(self.message, str) else self.message.text

        # Ensure thread exists
        httpx.post(f"{server}/threads", json={}, timeout=10)

        tokens: list[str] = []
        tool_calls: list[str] = []

        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{server}/threads/{thread_id}/run",
                json={"message": message, "thread_id": thread_id},
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload:
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type")
                    if etype == "token":
                        tokens.append(event.get("content", ""))
                        # Stream partial content to Langflow UI
                        self.status = "".join(tokens)
                    elif etype == "tool_start":
                        tool_calls.append(f"[tool: {event.get('name')}]")
                    elif etype == "done":
                        break
                    elif etype == "error":
                        raise RuntimeError(event.get("message", "Agent error"))

        full_response = "".join(tokens)
        return Data(
            data={
                "text":       full_response,
                "thread_id":  thread_id,
                "tool_calls": tool_calls,
            }
        )
