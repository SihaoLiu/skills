#!/usr/bin/env python3

import json
from typing import Optional


class StructuredOutput:
    """Extract a final answer while retaining structured events as transcript."""

    def __init__(self, backend: str, transcript_output: bool = True) -> None:
        self.backend = backend
        self.transcript_output = transcript_output
        self.buffer = bytearray()
        self.final_text: Optional[str] = None

    def feed(self, content: bytes) -> None:
        self.buffer.extend(content)
        while True:
            newline = self.buffer.find(b"\n")
            if newline < 0:
                return
            line = bytes(self.buffer[:newline]).strip()
            del self.buffer[: newline + 1]
            self._inspect_line(line)

    def finish(self) -> bytes:
        line = bytes(self.buffer).strip()
        self.buffer.clear()
        if line:
            self._inspect_line(line)
        if self.final_text is None:
            return b""
        content = self.final_text.encode("utf-8", "replace")
        return content if content.endswith(b"\n") else content + b"\n"

    def _inspect_line(self, line: bytes) -> None:
        if line.startswith(b"\x1e"):
            line = line[1:].lstrip()
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(event, dict):
            return

        if self.backend in ("claude", "glm"):
            result = event.get("result") if event.get("type") == "result" else None
            if isinstance(result, str):
                self.final_text = result
            return

        if self.backend == "codex" and event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    self.final_text = text
            return

        if self.backend == "kimi" and event.get("role") == "assistant":
            content = event.get("content")
            if isinstance(content, str):
                self.final_text = content
