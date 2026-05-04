"""First-class code-defined workflow agent runtime."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_framework.agents.agent import Agent
from agent_framework.agents.agent_result import AgentResult
from agent_framework.agents.workflow import ProgrammaticWorkflow


@dataclass(slots=True)
class WorkflowAgent(Agent):
    """Agent runtime whose control flow is defined by a ProgrammaticWorkflow."""

    workflow: ProgrammaticWorkflow | None = None

    def configure_workflow(self, runtime_metadata: dict[str, Any]) -> None:
        if self.source_path is None:
            raise ValueError(f"Cannot resolve workflow for {self.agent_id} without source path.")
        raw_workflow = runtime_metadata.get("workflow")
        if not isinstance(raw_workflow, dict):
            raise ValueError(f"Workflow agent {self.agent_id!r} requires a 'workflow' metadata object.")
        raw_path = str(raw_workflow.get("path", "")).strip()
        if not raw_path:
            raise ValueError(f"Workflow agent {self.agent_id!r} requires workflow.path in sidecar metadata.")
        workflow_path = Path(raw_path)
        if not workflow_path.is_absolute():
            workflow_path = self.source_path.parent / workflow_path
        workflow_path = workflow_path.resolve()
        if not workflow_path.exists():
            raise ValueError(f"Workflow module for {self.agent_id!r} was not found at {workflow_path}.")

        module_name = f"agent_workflow_{self.agent_id}_{uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, workflow_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load workflow module from {workflow_path}.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        build_workflow = getattr(module, "build_workflow", None)
        if not callable(build_workflow):
            raise ValueError(f"Workflow module {workflow_path} must export callable 'build_workflow'.")
        workflow = build_workflow(self)
        if not isinstance(workflow, ProgrammaticWorkflow):
            raise ValueError(
                f"Workflow module {workflow_path} returned {type(workflow).__name__}, "
                "expected ProgrammaticWorkflow."
            )
        self.workflow = workflow

    def run(
        self,
        *,
        host,
        parameters: dict[str, Any] | None = None,
        caller_id: str | None = None,
        parent_run_id: str | None = None,
        rendered_prompt_override: str | None = None,
        conversation_messages: tuple[dict[str, str], ...] | None = None,
        prompt_fragments: tuple[str, ...] | None = None,
        run_id: str | None = None,
        in_parallel_batch: bool = False,
        planning_override: bool | None = None,
    ) -> AgentResult:
        if self.workflow is None:
            raise ValueError(f"Workflow agent {self.agent_id!r} has no configured workflow.")
        run = self._create_run(
            parameters or {},
            run_id=run_id,
            parent_run_id=parent_run_id,
            in_parallel_batch=in_parallel_batch,
            rendered_prompt_override=rendered_prompt_override,
            conversation_messages=conversation_messages,
            prompt_fragments=prompt_fragments,
        )
        register_run = getattr(host, "register_run", None)
        if callable(register_run):
            register_run(
                run_id=run.run_id,
                agent_id=self.agent_id,
                caller_id=caller_id,
                parent_run_id=parent_run_id,
            )
        normalize_memory_parameters = getattr(host, "normalize_memory_parameters", None)
        if callable(normalize_memory_parameters):
            normalized_seed_parameters = normalize_memory_parameters(
                agent_id=self.agent_id,
                run_id=run.run_id,
                parameters=run.seed_parameters,
            )
            if normalized_seed_parameters != run.seed_parameters:
                run.seed_parameters = normalized_seed_parameters
                if rendered_prompt_override is None:
                    run.rendered_prompt = self._render_seed_prompt(run.seed_parameters)

        self.refresh_parameter_state(run)
        from agent_framework.agent_event_publisher import agent_events

        initial_context = self.build_context(host=host, run=run)
        system_sources: list[str] = []
        if self.source_path is not None:
            system_sources.append(str(self.source_path))
        user_sources = (str(self.source_path),) if self.source_path is not None else ()
        agent_events.audit_agent_call_started(
            run_id=run.run_id,
            parent_run_id=parent_run_id,
            caller_id=caller_id,
            agent_name=self.agent_id,
            system_prompt=initial_context.system_prompt,
            system_prompt_sources=tuple(system_sources),
            user_prompt=initial_context.user_prompt,
            user_prompt_sources=user_sources,
        )
        try:
            early_result = self._run_pre_agent_hooks(host=host, run=run, caller_id=caller_id)
            if early_result is not None:
                return self._run_post_agent_hooks(
                    host=host,
                    run=run,
                    caller_id=caller_id,
                    result=early_result,
                )[0]
            self.refresh_parameter_state(run)
            agent_events.audit_parameters_bound(
                run_id=run.run_id,
                agent_id=self.agent_id,
                bound_parameters=dict(run.parameter_values or {}),
            )
            result = self.execute_programmatic_workflow(
                host=host,
                run=run,
                caller_id=caller_id,
                workflow=self.workflow,
            )
            return self._run_post_agent_hooks(
                host=host,
                run=run,
                caller_id=caller_id,
                result=result,
            )[0]
        finally:
            usage_summary = {"usage_self": {}, "usage_inclusive": {}}
            finish_runtime_usage = getattr(host, "finish_runtime_usage", None)
            if callable(finish_runtime_usage):
                usage_summary = finish_runtime_usage(run_id=run.run_id)
            agent_events.audit_agent_call_finished(
                run_id=run.run_id,
                usage_self=usage_summary.get("usage_self"),
                usage_inclusive=usage_summary.get("usage_inclusive"),
            )


__all__ = ["WorkflowAgent"]
