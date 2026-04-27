# LangGraph: Planning, Replanning, and Parallel Execution

LangGraph is LangChain's stateful agent runtime. Unlike a single-loop ReAct agent, it expresses agent behavior as a directed **graph of nodes** that read and write to a shared, typed **state**. This shape makes planning patterns natural: a "planner" node populates a structured plan field; "executor" nodes consume one item at a time and append results; a "replanner" node decides whether to revise the plan or finish. Optional **checkpointers** persist state per super-step, enabling pause/resume, time-travel, and human-in-the-loop interrupts.

This report covers the three canonical planning patterns shipped as LangGraph tutorials — **Plan-and-Execute**, **ReWOO**, and **LLMCompiler** — and the lower-level primitives (`Send`, reducers, supervisor) that support replanning and parallel sub-agent execution.

## 1. LangGraph's Planning Model: graph of nodes + state

The core abstraction is `StateGraph(StateType)`. `StateType` is a `TypedDict` whose fields are channels — values keyed by field name with optional **reducers** that determine how concurrent writes merge. Without a reducer, a node's return dict overwrites the field; with a reducer like `operator.add` or `add_messages`, multiple writers (parallel branches) safely accumulate.

Key primitives used by planning agents:

- `add_node(name, fn)` — `fn(state) -> dict_of_updates`.
- `add_edge(src, dst)` and `add_conditional_edges(src, router_fn, [allowed_dsts])` — control flow; the router returns the next node name (or `END`).
- `START` and `END` — sentinel nodes.
- **Reducers via `Annotated`**: `past_steps: Annotated[List[Tuple], operator.add]` makes returns from any node *append* rather than replace.
- **Checkpointer** (e.g. `MemorySaver`, SQLite, Postgres) — saves a `StateSnapshot` after every super-step. Required for `interrupt()`, `astream`-resume, and time-travel (`graph.invoke(None, config={"configurable": {"thread_id": ..., "checkpoint_id": ...}})`).
- **`Send(node, state)`** — used inside conditional-edge functions to dynamically fan out: returning `[Send("worker", {...}), Send("worker", {...})]` invokes `worker` once per `Send` in parallel; the per-`Send` state can differ from the overall graph state.
- **`interrupt(value)`** — pauses the graph at a node and surfaces `value` to the caller; resumed via `Command(resume=...)`. The whole pre-interrupt state is checkpointed, so resume re-enters the node with new input.

Because every node's IO is just dict-in / dict-out, plans, intermediate results, and final answers are all "just fields" — no hidden controller state.

## 2. Plan-and-Execute pattern

**Source:** `langgraph/docs/docs/tutorials/plan-and-execute/plan-and-execute.ipynb` (downloaded from commit `23961cf`). This is the simplest planning agent: one big plan up front, execute step-by-step, replan after each step.

### State

```python
import operator
from typing import Annotated, List, Tuple
from typing_extensions import TypedDict

class PlanExecute(TypedDict):
    input: str
    plan: List[str]
    past_steps: Annotated[List[Tuple], operator.add]
    response: str
```

`past_steps` is the audit trail: each tuple is `(step_text, agent_response)`. The `operator.add` reducer means every executor invocation appends a new entry without clobbering history. `response` being non-empty is the termination signal.

### Plan schema (Pydantic)

```python
from pydantic import BaseModel, Field

class Plan(BaseModel):
    """Plan to follow in future"""
    steps: List[str] = Field(
        description="different steps to follow, should be in sorted order"
    )
```

The planner LLM is bound with `.with_structured_output(Plan)` so the model is forced to emit a valid `Plan`. No regex parsing, no fallback.

### Planner system prompt (verbatim)

```python
planner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """For the given objective, come up with a simple step by step plan. \
This plan should involve individual tasks, that if executed correctly will yield the correct answer. Do not add any superfluous steps. \
The result of the final step should be the final answer. Make sure that each step has all the information needed - do not skip steps.""",
        ),
        ("placeholder", "{messages}"),
    ]
)
planner = planner_prompt | ChatOpenAI(
    model="gpt-4o", temperature=0
).with_structured_output(Plan)
```

### Replanner: structured `Act` discriminated union

```python
class Response(BaseModel):
    """Response to user."""
    response: str

class Act(BaseModel):
    """Action to perform."""
    action: Union[Response, Plan] = Field(
        description="Action to perform. If you want to respond to user, use Response. "
        "If you need to further use tools to get the answer, use Plan."
    )
```

This is the key trick: the **same** structured-output call decides "finish or replan?" by choosing which branch of the union to populate. No separate "should I stop?" classifier.

### Replanner prompt (verbatim)

```python
replanner_prompt = ChatPromptTemplate.from_template(
    """For the given objective, come up with a simple step by step plan. \
This plan should involve individual tasks, that if executed correctly will yield the correct answer. Do not add any superfluous steps. \
The result of the final step should be the final answer. Make sure that each step has all the information needed - do not skip steps.

Your objective was this:
{input}

Your original plan was this:
{plan}

You have currently done the follow steps:
{past_steps}

Update your plan accordingly. If no more steps are needed and you can return to the user, then respond with that. Otherwise, fill out the plan. Only add steps to the plan that still NEED to be done. Do not return previously done steps as part of the plan."""
)
```

### Nodes and graph

The executor uses a prebuilt `create_react_agent` so each step gets full ReAct tool access:

```python
async def execute_step(state: PlanExecute):
    plan = state["plan"]
    plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
    task = plan[0]
    task_formatted = f"""For the following plan:
{plan_str}\n\nYou are tasked with executing step {1}, {task}."""
    agent_response = await agent_executor.ainvoke(
        {"messages": [("user", task_formatted)]}
    )
    return {"past_steps": [(task, agent_response["messages"][-1].content)]}

async def plan_step(state: PlanExecute):
    plan = await planner.ainvoke({"messages": [("user", state["input"])]})
    return {"plan": plan.steps}

async def replan_step(state: PlanExecute):
    output = await replanner.ainvoke(state)
    if isinstance(output.action, Response):
        return {"response": output.action.response}
    else:
        return {"plan": output.action.steps}

def should_end(state: PlanExecute):
    return END if state.get("response") else "agent"

workflow = StateGraph(PlanExecute)
workflow.add_node("planner", plan_step)
workflow.add_node("agent", execute_step)
workflow.add_node("replan", replan_step)
workflow.add_edge(START, "planner")
workflow.add_edge("planner", "agent")
workflow.add_edge("agent", "replan")
workflow.add_conditional_edges("replan", should_end, ["agent", END])
```

The executor only consumes `plan[0]`, but writes the result into `past_steps`. The replanner returns a fresh `plan` (excluding completed steps) — meaning `plan` is **fully overwritten** each round (no reducer), while `past_steps` **accumulates** (with reducer). This separation of "live plan" vs "audit log" is the central design idea.

## 3. ReWOO pattern

**Source:** `_rewoo_cells.txt`. ReWOO ("Reasoning WithOut Observation") generates the **entire plan and all tool calls in one shot**, then executes them sequentially with **variable substitution** (`#E1`, `#E2`, …) so later tools can reference earlier results without reprompting the planner.

### State

```python
class ReWOO(TypedDict):
    task: str
    plan_string: str   # raw planner output (for the solver prompt)
    steps: List        # parsed: [(plan_text, "#E1", "Tool", "args")]
    results: dict      # {"#E1": "...", "#E2": "..."}
    result: str
```

### Planner prompt (verbatim, with variable-substitution example)

```python
prompt = """For the following task, make plans that can solve the problem step by step. For each plan, indicate \
which external tool together with tool input to retrieve evidence. You can store the evidence into a \
variable #E that can be called by later tools. (Plan, #E1, Plan, #E2, Plan, ...)

Tools can be one of the following:
(1) Google[input]: Worker that searches results from Google. Useful when you need to find short
and succinct answers about a specific topic. The input should be a search query.
(2) LLM[input]: A pretrained LLM like yourself. Useful when you need to act with general
world knowledge and common sense. Prioritize it when you are confident in solving the problem
yourself. Input can be any instruction.

For example,
Task: Thomas, Toby, and Rebecca worked a total of 157 hours in one week. Thomas worked x
hours. Toby worked 10 hours less than twice what Thomas worked, and Rebecca worked 8 hours
less than Toby. How many hours did Rebecca work?
Plan: Given Thomas worked x hours, translate the problem into algebraic expressions and solve
with Wolfram Alpha. #E1 = WolframAlpha[Solve x + (2x − 10) + ((2x − 10) − 8) = 157]
Plan: Find out the number of hours Thomas worked. #E2 = LLM[What is x, given #E1]
Plan: Calculate the number of hours Rebecca worked. #E3 = Calculator[(2 ∗ #E2 − 10) − 8]

Begin! 
Describe your plans with rich details. Each Plan should be followed by only one #E.

Task: {task}"""
```

The plan is parsed from free-form text by a regex:

```python
regex_pattern = r"Plan:\s*(.+)\s*(#E\d+)\s*=\s*(\w+)\s*\[([^\]]+)\]"
```

### Variable substitution at execution time

```python
def tool_execution(state: ReWOO):
    _step = _get_current_task(state)
    _, step_name, tool, tool_input = state["steps"][_step - 1]
    _results = state.get("results") or {}
    for k, v in _results.items():
        tool_input = tool_input.replace(k, v)   # substitute #E1, #E2 ...
    if tool == "Google":
        result = search.invoke(tool_input)
    elif tool == "LLM":
        result = model.invoke(tool_input)
    _results[step_name] = str(result)
    return {"results": _results}
```

### Solver prompt (verbatim)

After all steps execute, a **single** "solver" call composes the answer from the recorded plan + evidence:

```python
solve_prompt = """Solve the following task or problem. To solve the problem, we have made step-by-step Plan and \
retrieved corresponding Evidence to each Plan. Use them with caution since long evidence might \
contain irrelevant information.

{plan}

Now solve the question or task according to provided Evidence above. Respond with the answer
directly with no extra words.

Task: {task}
Response:"""
```

### Graph

```python
graph = StateGraph(ReWOO)
graph.add_node("plan", get_plan)
graph.add_node("tool", tool_execution)
graph.add_node("solve", solve)
graph.add_edge(START, "plan")
graph.add_edge("plan", "tool")
graph.add_conditional_edges("tool", _route)   # loop back or go to solve
graph.add_edge("solve", END)
```

`_route` checks `len(results) == len(steps)` and either loops back to `tool` or proceeds to `solve`. ReWOO has **no replanner** — the cost is one planner call + one solver call, regardless of step count. The trade-off: if the plan is wrong, the agent can't recover mid-run.

## 4. LLMCompiler pattern

**Source:** `_compiler_cells.txt`. LLMCompiler (Kim et al., 2023) is the most ambitious of the three: the planner streams a **DAG** of tasks with explicit dependencies; a **task-fetching unit** schedules tasks for parallel execution as soon as their deps resolve; a **joiner** decides whether to answer or replan.

### Plan format (streamed)

```
1. tool_1(arg1="arg1", arg2=3.5, ...)
Thought: I then want to find out Y by using tool_2
2. tool_2(arg1="", arg2="${1}")
3. join()<END_OF_PLAN>
```

`${1}` is a back-reference to task #1's output (resolved at execute time, like ReWOO's `#E1`). `join()` is a synthetic terminal "tool" that triggers the joiner. The planner is sourced from LangChain Hub: `prompt = hub.pull("wfh/llm-compiler")`.

### Replan prompt fragment (verbatim)

When replanning, the same planner is reconfigured by `partial()`-binding a `replan` instruction:

```python
replanner_prompt = base_prompt.partial(
    replan=' - You are given "Previous Plan" which is the plan that the previous agent created along with the execution results '
    "(given as Observation) of each plan and a general thought (given as Thought) about the executed results."
    'You MUST use these information to create the next plan under "Current Plan".\n'
    ' - When starting the Current Plan, you should start with "Thought" that outlines the strategy for the next plan.\n'
    " - In the Current Plan, you should NEVER repeat the actions that are already executed in the Previous Plan.\n"
    " - You must continue the task index from the end of the previous one. Do not repeat task indices.",
    ...
)
```

A `RunnableBranch` chooses planner-vs-replanner at each call by inspecting whether the last message is a `SystemMessage` (which the joiner emits when it wants a replan).

### Parallel scheduler (excerpt)

```python
@as_runnable
def schedule_tasks(scheduler_input: SchedulerInput) -> List[FunctionMessage]:
    tasks = scheduler_input["tasks"]
    observations = _get_observations(scheduler_input["messages"])  # idx -> result
    futures = []
    with ThreadPoolExecutor() as executor:
        for task in tasks:
            deps = task["dependencies"]
            if deps and any(dep not in observations for dep in deps):
                futures.append(executor.submit(
                    schedule_pending_task, task, observations, retry_after))
            else:
                schedule_task.invoke({"task": task, "observations": observations})
        wait(futures)
    ...
```

`schedule_pending_task` polls every 0.25s until its deps land in `observations`. Because the planner **streams** tasks, the scheduler begins running independent tasks before the planner has even finished emitting the plan.

### Variable resolver

```python
def _resolve_arg(arg, observations):
    ID_PATTERN = r"\$\{?(\d+)\}?"
    def replace_match(m):
        idx = int(m.group(1))
        return str(observations.get(idx, m.group(0)))
    if isinstance(arg, str):
        return re.sub(ID_PATTERN, replace_match, arg)
    elif isinstance(arg, list):
        return [_resolve_arg(a, observations) for a in arg]
    else:
        return str(arg)
```

### Joiner: replan-or-finish via Pydantic union

```python
class FinalResponse(BaseModel):
    response: str

class Replan(BaseModel):
    feedback: str = Field(description="Analysis of the previous attempts and recommendations on what needs to be fixed.")

class JoinOutputs(BaseModel):
    """Decide whether to replan or whether you can return the final response."""
    thought: str = Field(description="The chain of thought reasoning for the selected action")
    action: Union[FinalResponse, Replan]

joiner_prompt = hub.pull("wfh/llm-compiler-joiner").partial(examples="")
runnable = joiner_prompt | llm.with_structured_output(JoinOutputs, method="function_calling")
```

If the joiner returns `Replan`, its `feedback` is wrapped in a `SystemMessage` and appended to state — which the planner's `RunnableBranch` detects and switches into replanner mode.

### Graph

```python
graph_builder = StateGraph(State)
graph_builder.add_node("plan_and_schedule", plan_and_schedule)
graph_builder.add_node("join", joiner)
graph_builder.add_edge("plan_and_schedule", "join")
graph_builder.add_conditional_edges("join", should_continue)  # END or "plan_and_schedule"
graph_builder.add_edge(START, "plan_and_schedule")
```

State is just `{messages: Annotated[list, add_messages]}`. Tool results are stored as `FunctionMessage` with `additional_kwargs={"idx": k}`, so `_get_observations` can rebuild the observation dict from message history on each pass.

## 5. Replanning Mechanisms

The three patterns illustrate three different replanning strategies:

| Pattern | When it replans | Mechanism |
|---|---|---|
| **Plan-and-Execute** | After **every** step | Replanner sees `(input, plan, past_steps)` and returns a `Plan` (continue) or `Response` (done) |
| **ReWOO** | Never | Plan is one-shot; solver synthesizes answer from collected evidence |
| **LLMCompiler** | After each **batch** | Joiner returns `Replan(feedback)` or `FinalResponse(response)`; feedback becomes a `SystemMessage` that flips the planner into replan mode |

Beyond model-driven replanning, LangGraph supports **human-driven replanning** via two checkpointer-backed mechanisms:

- **`interrupt(value)`** inside any node pauses the graph after checkpointing. The driver receives `value` (e.g. a proposed plan) and resumes with `Command(resume=user_input)`. The node body re-executes with the resumed value available, so the natural pattern is to put `interrupt` after the planner and let a human approve/edit the plan before execution.
- **Time-travel**: with a checkpointer and a `thread_id`, every super-step is a `StateSnapshot`. Calling `graph.update_state(config, values, as_node=...)` rewrites a past checkpoint, then `graph.invoke(None, config={"configurable": {"checkpoint_id": ...}})` re-runs from there. This lets you fork an execution: replay with an edited plan, swap a tool result, or branch on an alternate decision. Per LangGraph docs, interrupts always re-trigger during time-travel — so HITL approval points are preserved when forking.

## 6. Parallel sub-agents and skills

LangGraph offers two distinct ways to run things in parallel:

### Send API — dynamic fan-out / map-reduce

`Send(node_name, state_for_that_node)` returned from a conditional-edge function dispatches an independent execution path per `Send`. The fanned-out node receives a custom state (not the global graph state). Combined with a list-reducer on the result field, this is the canonical map-reduce pattern:

```python
class OverallState(TypedDict):
    topic: str
    subjects: list
    jokes: Annotated[list, operator.add]   # accumulator across parallel branches
    best_joke: str

def continue_to_jokes(state: OverallState):
    return [Send("generate_joke", {"subject": s}) for s in state["subjects"]]

graph.add_conditional_edges("generate_subjects", continue_to_jokes, ["generate_joke"])
graph.add_edge("generate_joke", "best_joke")   # runs once after all Sends complete
```

Concurrency cap: `graph.invoke(inputs, config={"max_concurrency": 50})`. Each `Send` runs in its own thread / async task. This is the LangGraph-native way to dispatch a batch of sub-agents from a planner: produce `[Send("subagent", {...}), ...]` per planned step.

### Supervisor pattern

`langgraph_supervisor.create_supervisor(agents, model, prompt=...)` (and the more general hand-rolled supervisor) builds a graph where a coordinator LLM picks the next agent each turn. Each worker is typically built with `create_react_agent(llm, tools, prompt=...)`. The supervisor calls a structured-output LLM with a `next: Literal["worker_a", "worker_b", "FINISH"]` schema and routes via `add_conditional_edges`. Workers communicate through the shared `messages` channel (with `add_messages` reducer). Hierarchical teams just nest supervisors as sub-graphs.

### Sub-graph composition

Any compiled graph is itself a `Runnable` and can be added as a node in another graph (`parent.add_node("team_a", team_a_subgraph)`). This is how hierarchical teams compose without losing state isolation — the sub-graph runs with its own state schema and merges only its declared output channels back to the parent.

## 7. Key takeaways for replication

For an engineer porting these ideas to a markdown-defined agent runtime:

1. **Make `plan` and `past_steps` first-class state fields**, not implicit conversation history. A reducer-equipped accumulator (`past_steps += [(step, result)]`) and an overwriteable live plan are the minimum vocabulary. In our framework this means promoting them out of the conversation `messages` tuple into explicit decision-loop state the agent can inspect each turn.

2. **Use structured output with a discriminated union for the replan-or-finish decision.** Plan-and-Execute's `Act = Union[Plan, Response]` and LLMCompiler's `JoinOutputs.action = Union[FinalResponse, Replan]` both fold "should I stop?" into "what do I emit?" — eliminating a separate classifier and a class of "agent finished but didn't say so" bugs. This aligns with our framework's strict-decision-JSON policy: extend `AgentDecision` with a `replan` kind that carries either a new plan or a final message, no fallback.

3. **Separate planner and executor models.** Plan-and-Execute uses GPT-4o for planning and a cheaper model inside the ReAct executor. The runtime should let an agent override `model` per sub-call (we already have `AGENT_MODELS=...`), and a planning agent should explicitly delegate to a smaller-model executor agent.

4. **Variable substitution lets one planner call drive many tool calls.** ReWOO's `#E1` and LLMCompiler's `${1}` syntaxes are the same idea: the planner emits symbolic refs; an interpreter resolves them at execute time using a `dict[step_id, result]`. This avoids re-prompting the planner between every tool call — the major token-cost win of these patterns. A first version can use a simple `re.sub` resolver as in ReWOO; harden later with a real grammar.

5. **Express parallelism declaratively, not procedurally.** LangGraph's `Send` returns *data* (a list of dispatch records) from a conditional edge; the runtime handles concurrency, joining, and reducer merge. For our `call_subagents` decision, the planner should emit a list of `(subagent_id, parameters, output_key)` records and the host should fan out with bounded concurrency (we already have `SUBAGENT_MAX_PARALLELISM`) and write each result under `output_key` into a reducer-merged dict — never let the planner serialize the orchestration loop itself.

6. **Persist state per super-step to enable HITL and replay.** A checkpointer turns "pause for approval" into a one-line `interrupt(plan)` and turns "what if I'd taken the other branch?" into a free debugging tool. Our `ConversationStore` already persists `messages`; extend it (or add a sibling) to snapshot the full decision-loop state (plan, past_steps, pending sub-agent batch) keyed by `(conversation_id, step_id)` so a callback can resume into a modified state.

7. **Stream the plan; start executing before it finishes.** LLMCompiler's biggest perf win comes from `planner.stream(messages)` feeding `schedule_tasks` while the LLM is still generating. Independent tasks dispatch as soon as their dependencies resolve — the planner's tail latency overlaps with the first executions. Worth keeping in mind even if v1 is non-streaming: design the plan schema as a list of independent records, not a single blob, so streaming is a later mechanical change.

8. **Keep the plan format simple and parser-tolerant.** The LLMCompiler tutorial explicitly calls out plan-parser fragility ("could be made more robust by using streaming tool calling"). Pydantic + `with_structured_output` (Plan-and-Execute) is the most reliable; regex-over-free-text (ReWOO, LLMCompiler) is faster to prototype but brittle. Given our project's "no silent repair" rule, **prefer structured output and fail loudly** on schema violations rather than regex-rescuing bad plans.

## Sources

- [Plan-and-Execute notebook (langchain-ai/langgraph @ 23961cf)](https://github.com/langchain-ai/langgraph/blob/23961cff61a42b52525f3b20b4094d8d2fba1744/docs/docs/tutorials/plan-and-execute/plan-and-execute.ipynb)
- [LLMCompiler tutorial (local cache `_compiler_cells.txt`)](https://github.com/langchain-ai/langgraph/tree/main/examples/llm-compiler)
- [ReWOO tutorial (local cache `_rewoo_cells.txt`)](https://github.com/langchain-ai/langgraph/tree/main/examples/rewoo)
- [LangGraph Send API and map-reduce](https://langchain-ai.github.io/langgraphjs/how-tos/map-reduce/)
- [LangGraph low-level concepts: state, reducers, checkpointer](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- [Hierarchical agent teams tutorial](https://langchain-ai.github.io/langgraph/tutorials/multi_agent/hierarchical_agent_teams/)
- [langgraph-supervisor reference](https://reference.langchain.com/python/langgraph/supervisor/)
- [Time-travel docs](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
- [Plan-and-Execute Agents (LangChain blog)](https://blog.langchain.com/planning-agents/)
- [Making it easier to build human-in-the-loop agents with interrupt](https://blog.langchain.com/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt/)
