"""Prompt-evaluation support for agent regression testing."""

from __future__ import annotations

import json
from datetime import datetime
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from openai import OpenAI

from agent_framework.agent import AgentResult
from agent_framework.host import AgentHost
from agent_framework.model import ModelContext


@dataclass(frozen=True, slots=True)
class EvaluationScene:
    """One evaluation scene containing a prompt and expected behavior."""

    prompt: str
    expected: str


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    """Parsed evaluation input file."""

    scenes: tuple[EvaluationScene, ...]
    evaluator_prompt: str
    schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class RecordedInteraction:
    """One runtime interaction captured during evaluation."""

    kind: str
    caller_id: str
    callee_id: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "kind": self.kind,
            "caller_id": self.caller_id,
            "callee_id": self.callee_id,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class PromptScore:
    """Per-prompt evaluation result."""

    prompt: str
    expected: str
    agent_output: str
    llm_evaluator_output: str
    llm_evaluator_payload: dict[str, object]
    schema_evaluator_output: str
    interactions: tuple[dict[str, Any], ...]
    llm_score: float
    schema_score: float
    overall_score: float


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Structured result from one evaluator-LLM scoring pass."""

    score: float
    output_text: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class OpenAiEvaluationScene:
    """One raw-input evaluation scene for the OpenAI evaluator."""

    input_json: dict[str, Any] | list[Any] | str
    expected_output: Any
    evaluation_criteria: str
    format_evaluation: str = ""


@dataclass(frozen=True, slots=True)
class OpenAiEvaluationInput:
    """Parsed raw-input evaluation file."""

    scenes: tuple[OpenAiEvaluationScene, ...]


@dataclass(frozen=True, slots=True)
class FormatEvaluationResult:
    """Outcome of a format evaluator callback."""

    score: float
    output_text: str
    normalized_output: str


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """Aggregate evaluation output."""

    prompt_scores: tuple[PromptScore, ...]
    overall_score: float

    def to_json(self) -> str:
        """Serialize the evaluation summary for CLI output."""
        payload = {
            "prompt_scores": [
                {
                    "prompt": item.prompt,
                    "expected": item.expected,
                    "agent_output": item.agent_output,
                    "llm_evaluator_output": item.llm_evaluator_output,
                    "llm_evaluator_payload": item.llm_evaluator_payload,
                    "schema_evaluator_output": item.schema_evaluator_output,
                    "interactions": list(item.interactions),
                    "llm_score": item.llm_score,
                    "schema_score": item.schema_score,
                    "overall_score": item.overall_score,
                }
                for item in self.prompt_scores
            ],
            "overall_score": self.overall_score,
        }
        return json.dumps(payload, indent=2)

    def to_markdown_table(self) -> str:
        """Render a compact markdown summary table for CLI output."""
        lines = [
            "| scene | score 1 | score 2 | average | result |",
            "| ----- | ------- | ------- | ------- | ------ |",
        ]
        for index, item in enumerate(self.prompt_scores, start=1):
            result_text = str(item.llm_evaluator_payload.get("result", "")).replace("\n", " ").strip()
            lines.append(
                "| "
                f"scene {index} | {_format_score(item.llm_score)} | {_format_score(item.schema_score)} | {_format_score(item.overall_score)} | {_escape_markdown_cell(result_text)} |"
            )
        lines.append(f"| total |  |  | {_format_score(self.overall_score)} |  |")
        return "\n".join(lines)


class ResultJudge(Protocol):
    """Scores one agent result for quality on a 1-10 scale."""

    def score(
        self,
        *,
        evaluator_prompt: str,
        prompt: str,
        expected: str,
        result: str,
        interactions: tuple[dict[str, Any], ...],
    ) -> JudgeResult:
        """Return a structured judge result for one prompt/result pair."""


@dataclass(slots=True)
class OpenAiResultJudge:
    """OpenAI-backed evaluator for prompt-result quality."""

    api_key: str
    model_name: str

    def score(
        self,
        *,
        evaluator_prompt: str,
        prompt: str,
        expected: str,
        result: str,
        interactions: tuple[dict[str, Any], ...],
    ) -> JudgeResult:
        """Evaluate one prompt/result pair and return a normalized 1-10 score."""
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is required for prompt evaluation.")

        client = OpenAI(api_key=self.api_key)
        prompt_text = _build_judge_prompt(
            evaluator_prompt=evaluator_prompt,
            prompt=prompt,
            expected=expected,
            result=result,
            interactions=interactions,
        )
        response = client.responses.create(
            model=self.model_name,
            temperature=0,
            input=[
                {"role": "user", "content": prompt_text},
            ],
        )
        payload = json.loads(_normalize_json_text(response.output_text))
        return JudgeResult(
            score=float(payload["score"]),
            output_text=response.output_text,
            payload=payload,
        )


@dataclass(slots=True)
class RecordingAgentHost(AgentHost):
    """Agent host variant that records runtime interactions during evaluation."""

    recorded_interactions: list[RecordedInteraction] = field(default_factory=list)
    auto_input_response: str = ""

    @classmethod
    def from_host(cls, host: AgentHost) -> "RecordingAgentHost":
        """Create a recording wrapper host that shares config and registries with a base host."""
        return cls(
            config=host.config,
            model_driver=host.model_driver,
            input_reader=lambda prompt: "",
            output_writer=host.output_writer,
            agent_registry=host.agent_registry,
            tool_registry=host.tool_registry,
        )

    def snapshot_interactions(self) -> tuple[dict[str, Any], ...]:
        """Return the recorded interactions in JSON-serializable form."""
        return tuple(item.to_dict() for item in self.recorded_interactions)

    def call_subagent(
        self,
        *,
        caller,
        callee_id: str,
        parameters: dict[str, Any],
        parent_run_id: str | None = None,
    ):
        """Record a subagent call and its result."""
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="subagent_call",
                caller_id=caller.agent_id,
                callee_id=callee_id,
                payload={"parameters": dict(parameters), "parent_run_id": parent_run_id},
            )
        )
        result = super().call_subagent(
            caller=caller,
            callee_id=callee_id,
            parameters=parameters,
            parent_run_id=parent_run_id,
        )
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="subagent_result",
                caller_id=caller.agent_id,
                callee_id=callee_id,
                payload={"status": result.status, "message": result.message},
            )
        )
        return result

    def execute_tool(self, tool_name: str, parameters: dict[str, Any]) -> str:
        """Record a tool call and its result."""
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="tool_call",
                caller_id="agent",
                callee_id=tool_name,
                payload={"parameters": dict(parameters)},
            )
        )
        result = super().execute_tool(tool_name, parameters)
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="tool_result",
                caller_id=tool_name,
                callee_id="agent",
                payload={"result": result},
            )
        )
        return result

    def resolve_callback(self, *, caller_id: str, callee, prompt: str) -> str:
        """Record a callback request and avoid interactive roundtrips at the host boundary."""
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="callback_request",
                caller_id=callee.agent_id,
                callee_id=caller_id,
                payload={"prompt": prompt},
            )
        )
        if caller_id == "host":
            answer = self.auto_input_response
        else:
            answer = super().resolve_callback(caller_id=caller_id, callee=callee, prompt=prompt)
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="callback_response",
                caller_id=caller_id,
                callee_id=callee.agent_id,
                payload={"message": answer},
            )
        )
        return answer

    def request_user_input(self, prompt: str) -> str:
        """Record direct user-input requests without interactive prompting."""
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="host_input_request",
                caller_id="agent",
                callee_id="host",
                payload={"prompt": prompt},
            )
        )
        self.recorded_interactions.append(
            RecordedInteraction(
                kind="host_input_response",
                caller_id="host",
                callee_id="agent",
                payload={"message": self.auto_input_response},
            )
        )
        return self.auto_input_response


@dataclass(slots=True)
class AgentPromptEvaluator:
    """Runs agent prompts and aggregates evaluation scores."""

    host: AgentHost
    judge: ResultJudge
    agent_id: str | None = None

    def evaluate_file(self, path: str | Path, *, agent_id: str | None = None) -> EvaluationSummary:
        """Parse an XML evaluation file and evaluate all contained prompts."""
        source_path = Path(path)
        evaluation_input = self.parse_input_file(source_path)
        summary = self.evaluate_input(evaluation_input, agent_id=agent_id)
        output_path = self._write_output_artifact(source_path, summary)
        output_writer = getattr(self.host, "output_writer", None)
        if callable(output_writer):
            output_writer(f"Wrote evaluation artifact to {output_path}")
        return summary

    def evaluate_input(
        self,
        evaluation_input: EvaluationInput,
        *,
        agent_id: str | None = None,
    ) -> EvaluationSummary:
        """Evaluate all prompts from a parsed evaluation input."""
        resolved_agent_id = agent_id or self.agent_id
        prompt_scores = tuple(
            self._evaluate_prompt(
                prompt=scene.prompt,
                expected=scene.expected,
                evaluator_prompt=evaluation_input.evaluator_prompt,
                schema=evaluation_input.schema,
                agent_id=resolved_agent_id,
            )
            for scene in evaluation_input.scenes
        )
        overall_score = sum(item.overall_score for item in prompt_scores) / len(prompt_scores)
        return EvaluationSummary(prompt_scores=prompt_scores, overall_score=overall_score)

    @staticmethod
    def parse_input_file(path: str | Path) -> EvaluationInput:
        """Parse the XML evaluator input file."""
        root = ET.fromstring(Path(path).read_text(encoding="utf-8"))
        scenes = []
        for element in root.findall(".//scene"):
            prompt = _extract_prompt_content(element.find("prompt"))
            expected = (element.findtext("expected") or "").strip()
            if prompt:
                scenes.append(EvaluationScene(prompt=prompt, expected=expected))
        evaluator = (root.findtext(".//evaluator") or "").strip()
        schema_text = (root.findtext(".//schema") or "").strip()
        if not scenes:
            raise ValueError("Evaluation input must contain at least one <scene> with <prompt>.")
        if not evaluator:
            raise ValueError("Evaluation input must contain one <evaluator> segment.")
        if not schema_text:
            raise ValueError("Evaluation input must contain one <schema> segment.")
        return EvaluationInput(
            scenes=tuple(scenes),
            evaluator_prompt=evaluator,
            schema=json.loads(schema_text),
        )

    def _evaluate_prompt(
        self,
        *,
        prompt: str,
        expected: str,
        evaluator_prompt: str,
        schema: dict[str, object],
        agent_id: str | None,
    ) -> PromptScore:
        """Evaluate one prompt against both LLM and schema scoring."""
        result, interactions = self._run_evaluation_agent(prompt=prompt, agent_id=agent_id)
        llm_result = self.judge.score(
            evaluator_prompt=evaluator_prompt,
            prompt=prompt,
            expected=expected,
            result=result.message,
            interactions=interactions,
        )
        schema_score, schema_output = self._schema_score(result.message, schema)
        overall_score = (llm_result.score + schema_score) / 2
        return PromptScore(
            prompt=prompt,
            expected=expected,
            agent_output=result.message,
            llm_evaluator_output=llm_result.output_text,
            llm_evaluator_payload=llm_result.payload,
            schema_evaluator_output=schema_output,
            interactions=interactions,
            llm_score=llm_result.score,
            schema_score=schema_score,
            overall_score=overall_score,
        )

    def _run_evaluation_agent(
        self,
        *,
        prompt: str,
        agent_id: str | None,
    ) -> tuple[Any, tuple[dict[str, Any], ...]]:
        """Run one evaluation prompt and capture its interaction trace when supported."""
        if isinstance(self.host, AgentHost):
            recording_host = RecordingAgentHost.from_host(self.host)
            result = (
                recording_host.run_agent(agent_id, initial_instruction=prompt)
                if agent_id
                else recording_host.run_root(initial_instruction=prompt)
            )
            return result, recording_host.snapshot_interactions()

        if agent_id and hasattr(self.host, "run_agent"):
            result = self.host.run_agent(agent_id, initial_instruction=prompt)
        else:
            result = self.host.run_root(initial_instruction=prompt)
        interactions = tuple(getattr(self.host, "recorded_interactions", ()))
        return result, interactions

    def _write_output_artifact(self, source_path: Path, summary: EvaluationSummary) -> Path:
        """Persist a timestamped JSON evaluation artifact next to the XML file."""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_path = source_path.with_name(f"{source_path.stem}_{timestamp}.out.json")
        output_path.write_text(summary.to_json(), encoding="utf-8")
        return output_path

    @staticmethod
    def _schema_score(result_text: str, schema: dict[str, object]) -> tuple[float, str]:
        """Return score and human-readable schema evaluation output."""
        try:
            payload = json.loads(result_text)
            validate_json_schema(instance=payload, schema=schema)
        except json.JSONDecodeError as exc:
            return 0.0, f"JSON parse failed: {exc}"
        except JsonSchemaValidationError as exc:
            return 0.0, f"Schema validation failed: {exc.message}"
        return 10.0, "Schema validation passed."


FormatEvaluator = Callable[[str], FormatEvaluationResult]


def validate_json_object_output(result_text: str) -> FormatEvaluationResult:
    """Validate that the result is a JSON object and return a normalized string."""
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError as exc:
        return FormatEvaluationResult(
            score=0.0,
            output_text=f"JSON parse failed: {exc}",
            normalized_output=result_text,
        )
    if not isinstance(payload, dict):
        return FormatEvaluationResult(
            score=0.0,
            output_text="JSON validation failed: result is not a JSON object.",
            normalized_output=result_text,
        )
    return FormatEvaluationResult(
        score=10.0,
        output_text="JSON object validation passed.",
        normalized_output=json.dumps(payload, indent=2, sort_keys=True),
    )


def validate_json_output(result_text: str) -> FormatEvaluationResult:
    """Validate that the result is valid JSON and return a normalized string."""
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError as exc:
        return FormatEvaluationResult(
            score=0.0,
            output_text=f"JSON parse failed: {exc}",
            normalized_output=result_text,
        )
    return FormatEvaluationResult(
        score=10.0,
        output_text="JSON validation passed.",
        normalized_output=json.dumps(payload, indent=2, sort_keys=True),
    )


DEFAULT_FORMAT_EVALUATORS: dict[str, FormatEvaluator] = {
    "json": validate_json_output,
    "json_object": validate_json_object_output,
}


@dataclass(slots=True)
class OpenAiConversationEvaluator:
    """Evaluate raw agent conversation inputs without parameter mapping."""

    host: AgentHost
    judge: ResultJudge
    agent_id: str
    format_evaluators: dict[str, FormatEvaluator] = field(default_factory=lambda: dict(DEFAULT_FORMAT_EVALUATORS))

    def evaluate_file(self, path: str | Path) -> EvaluationSummary:
        """Parse a JSON evaluation file and evaluate all contained scenes."""
        source_path = Path(path)
        evaluation_input = self.parse_input_file(source_path)
        summary = self.evaluate_input(evaluation_input)
        output_path = self._write_output_artifact(source_path, summary)
        output_writer = getattr(self.host, "output_writer", None)
        if callable(output_writer):
            output_writer(f"Wrote evaluation artifact to {output_path}")
        return summary

    @staticmethod
    def parse_input_file(path: str | Path) -> OpenAiEvaluationInput:
        """Parse the JSON evaluator input file."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        scenes_payload = payload.get("scenes")
        if not isinstance(scenes_payload, list) or not scenes_payload:
            raise ValueError("OpenAI evaluation input must contain a non-empty 'scenes' array.")

        scenes: list[OpenAiEvaluationScene] = []
        for item in scenes_payload:
            if not isinstance(item, dict):
                raise ValueError("Each evaluation scene must be a JSON object.")
            if "input_json" not in item:
                raise ValueError("Each evaluation scene must contain 'input_json'.")
            scenes.append(
                OpenAiEvaluationScene(
                    input_json=item["input_json"],
                    expected_output=item.get("expected_output", ""),
                    evaluation_criteria=str(item.get("evaluation_criteria", "")).strip(),
                    format_evaluation=str(item.get("format_evaluation", "")).strip(),
                )
            )
        return OpenAiEvaluationInput(scenes=tuple(scenes))

    def evaluate_input(self, evaluation_input: OpenAiEvaluationInput) -> EvaluationSummary:
        """Evaluate all raw-input scenes from a parsed JSON file."""
        prompt_scores = tuple(self._evaluate_scene(scene) for scene in evaluation_input.scenes)
        overall_score = sum(item.overall_score for item in prompt_scores) / len(prompt_scores)
        return EvaluationSummary(prompt_scores=prompt_scores, overall_score=overall_score)

    def _evaluate_scene(self, scene: OpenAiEvaluationScene) -> PromptScore:
        """Evaluate one raw-input scene."""
        result, interactions = self._run_openai_scene(scene.input_json)
        format_result = self._run_format_evaluator(scene.format_evaluation, result.message)
        llm_result = self.judge.score(
            evaluator_prompt=scene.evaluation_criteria,
            prompt=json.dumps(scene.input_json, indent=2) if not isinstance(scene.input_json, str) else scene.input_json,
            expected=_stringify_expected_output(scene.expected_output),
            result=format_result.normalized_output,
            interactions=interactions,
        )
        overall_score = (llm_result.score + format_result.score) / 2
        return PromptScore(
            prompt=json.dumps(scene.input_json, indent=2) if not isinstance(scene.input_json, str) else scene.input_json,
            expected=_stringify_expected_output(scene.expected_output),
            agent_output=result.message,
            llm_evaluator_output=llm_result.output_text,
            llm_evaluator_payload=llm_result.payload,
            schema_evaluator_output=format_result.output_text,
            interactions=interactions,
            llm_score=llm_result.score,
            schema_score=format_result.score,
            overall_score=overall_score,
        )

    def _run_openai_scene(
        self,
        input_json: dict[str, Any] | list[Any] | str,
    ) -> tuple[Any, tuple[dict[str, Any], ...]]:
        """Run one raw-input scene using the exact OpenAI input payload."""
        agent = self.host.get_agent(self.agent_id)
        provider_input = _normalize_openai_provider_input(input_json)
        response = self.host.get_model_driver(agent).decide(
            agent_id=agent.agent_id,
            provider_name=agent.provider_name,
            model_names=agent.model_names,
            temperature=agent.temperature,
            context=ModelContext(
                system_prompt="",
                user_prompt="",
                exact_input_payload=provider_input,
            ),
        )
        return AgentResult(status="completed", message=response.raw_text), ()

    def _run_format_evaluator(self, evaluator_name: str, result_text: str) -> FormatEvaluationResult:
        """Run one named format evaluator or return the raw result unchanged."""
        if not evaluator_name:
            return FormatEvaluationResult(
                score=10.0,
                output_text="No format evaluation configured.",
                normalized_output=result_text,
            )
        evaluator = self.format_evaluators.get(evaluator_name)
        if evaluator is None:
            raise KeyError(f"Unknown format evaluator: {evaluator_name}")
        return evaluator(result_text)

    def _write_output_artifact(self, source_path: Path, summary: EvaluationSummary) -> Path:
        """Persist a timestamped JSON evaluation artifact next to the source file."""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_path = source_path.with_name(f"{source_path.stem}_{timestamp}.out.json")
        output_path.write_text(summary.to_json(), encoding="utf-8")
        return output_path


def _normalize_json_text(raw_text: str) -> str:
    """Extract JSON text from plain or fenced model responses."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    return text


def _stringify_expected_output(expected_output: Any) -> str:
    """Normalize expected-output payloads into judge-friendly text."""
    if isinstance(expected_output, str):
        return expected_output
    return json.dumps(expected_output, indent=2, sort_keys=True)


def _normalize_openai_provider_input(input_json: dict[str, Any] | list[Any] | str) -> dict[str, Any] | list[Any] | str:
    """Return an exact OpenAI `input` payload from the scene without remapping."""
    if isinstance(input_json, (str, list)):
        return input_json
    if not isinstance(input_json, dict):
        raise ValueError("Scene input_json must be the exact OpenAI input payload as a string, object, or array.")
    if "input" in input_json:
        return input_json["input"]
    return input_json


def _extract_prompt_content(prompt_element: ET.Element | None) -> str:
    """Reconstruct prompt text from a prompt element with nested XML content."""
    if prompt_element is None:
        return ""

    parts: list[str] = []
    if prompt_element.text:
        parts.append(prompt_element.text)

    for child in prompt_element:
        parts.append(ET.tostring(child, encoding="unicode"))

    return "".join(parts).strip()


def _format_score(value: float) -> str:
    """Format scores compactly for human-readable summary tables."""
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _escape_markdown_cell(value: str) -> str:
    """Escape table-breaking characters in markdown cell content."""
    return value.replace("|", "\\|")


def _build_judge_prompt(
    *,
    evaluator_prompt: str,
    prompt: str,
    expected: str,
    result: str,
    interactions: tuple[dict[str, Any], ...],
) -> str:
    """Construct the evaluator prompt from the XML evaluator text and scene data."""
    return (
        "You should evaluate the prompt response and give the result of the evaluation in the json format shown below (result). "
        "Check the agent output against agent input, the recorded interactions, and the criteria given below, score 10 only when it entirely fulfills the evaluation criteria.\n\n"
        f"additional criteria ({evaluator_prompt})\n\n"
        "<result>\n"
        "{\n"
        '  "score": integer 1-10,\n'
        '  "matches": "text describing what was matching",\n'
        '  "failures": "text describing what was failing",\n'
        '  "result": "text describing the overall evaluation of results"\n'
        "}\n"
        "</result>\n"
        f"<input>{prompt}</input>\n"
        f"<output>{result}</output>\n"
        f"<interactions>{json.dumps(list(interactions), indent=2)}</interactions>\n"
        f"<criteria>{expected}</criteria>"
    )


__all__ = [
    "AgentPromptEvaluator",
    "EvaluationInput",
    "EvaluationScene",
    "EvaluationSummary",
    "FormatEvaluationResult",
    "JudgeResult",
    "OpenAiConversationEvaluator",
    "OpenAiEvaluationInput",
    "OpenAiEvaluationScene",
    "OpenAiResultJudge",
    "PromptScore",
    "RecordedInteraction",
    "RecordingAgentHost",
    "validate_json_object_output",
]
