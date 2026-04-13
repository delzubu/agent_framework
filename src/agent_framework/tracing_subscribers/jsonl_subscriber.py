"""Append each TraceEvent as one JSON line (JSONL)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from agent_framework.tracing import TraceEvent


class JsonlTraceSubscriber:
    def __init__(self, output_path: Path) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def consume(self, event: TraceEvent) -> None:
        with self.output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


__all__ = ["JsonlTraceSubscriber"]
