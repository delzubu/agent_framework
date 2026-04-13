"""Write llm-channel events to per-agent log files."""

from __future__ import annotations

import json
from pathlib import Path

from agent_framework.tracing import TraceEvent


class LlmTraceFileSubscriber:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def consume(self, event: TraceEvent) -> None:
        if event.channel != "llm":
            return
        agent_id = event.payload.get("agent_id") or event.context.agent_id or "llm-trace"
        safe = str(agent_id).replace("/", "_").replace("\\", "_")
        path = self.output_dir / f"{safe}.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{event.timestamp}] {event.kind}\n")
            handle.write(json.dumps(event.payload, indent=2, ensure_ascii=False))
            handle.write("\n\n")


__all__ = ["LlmTraceFileSubscriber"]
