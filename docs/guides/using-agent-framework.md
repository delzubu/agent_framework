# Using the agent framework

This guide takes you from zero to a fully working multi-agent system.  It assumes you have Python 3.11+, an API key for OpenAI or a compatible DIAL endpoint, and about an hour of patience for the first read-through.  After that, each chapter stands alone so you can jump to what you need.

There is no magic here.  An "agent" in this framework is a Markdown file that defines a prompt and a list of things the model is allowed to do.  The framework reads that file, calls the model, interprets the response, acts on it, and loops.  Everything else — sub-agents, tools, skills, callbacks, tracing — is built on top of that simple loop.  Once you understand the loop, the rest falls into place.

---

## How this guide is organised

The chapters follow the natural progression of building something real:

- **§1** gets you to a running agent in under ten minutes.
- **§2** explains the decision loop in depth so you stop treating the framework as a black box.
- **§3** covers tools — giving your agent real capabilities.
- **§4** covers sub-agents — splitting complex work across specialised agents.
- **§5** covers behaviors — extending agent logic with Python without forking the framework.
- **§6** covers skills and MCP — reusable instruction bundles and external tool servers.
- **§7** covers configuration — the `.env` layout, model fallbacks, per-agent overrides.
- **§8** covers the host — where everything comes together, and how to embed it in your code.
- **§9** covers tracing and debugging — seeing what went wrong without guessing.
- **§10** covers project structure — how to organise files as your project grows.

---

## 1. Create and run your first agent

### Why Markdown?

Most LLM frameworks ask you to write code that builds prompts.  This framework asks you to write prompts as Markdown files.  The distinction matters for two reasons.

First, a Markdown file is easier to review, diff, and share than a function that assembles a string.  When a product manager asks "what does this agent actually say to the model?", you hand them the `.md` file.  When a security reviewer asks "what is this agent allowed to do?", they read the `tools:` list in the frontmatter.  Nothing is hidden in Python string concatenation.

Second, the Markdown file is the agent's contract.  The framework enforces it: if you list a tool in `tools:` but the tool file is missing, you get an error.  If your prompt template uses `{{customer_name}}` but `customer_name` is not declared in `parameters:`, the agent fails to load.  The strictness is intentional — broken contracts should fail loudly at startup, not silently at 3am when a production agent calls a hallucinated tool name.

### The three-section format

Every agent file is split into exactly three sections by lines that contain only `---`:

```
---
<YAML frontmatter>
---
<system prompt>
---
<user prompt template>
```

If any section is missing, the agent will not load.  This is by design.

**Section 1 — YAML frontmatter** is the contract.  It declares the agent's identity, what it is allowed to do, and what parameters callers must supply.

**Section 2 — System prompt** is stable instruction text that the model always sees.  Write your persona, your constraints, your output format requirements, and any standing context here.  This section should not contain per-run variables.

**Section 3 — User prompt template** is rendered fresh every run.  It can contain `{{parameter_name}}` placeholders that are filled in from the invocation parameters.  This is what changes between calls.

### A minimal first agent

Create a project layout:

```
my_project/
  .env
  agents/
    root.md
  world/           (sandbox directory for file operations — optional for now)
```

**`agents/root.md`:**

```markdown
---
id: root
role: Assistant
description: A helpful general-purpose assistant.
parameters: {}
tools: []
subagents: []
skills: []
---
You are a helpful, honest, and concise assistant.
When you have finished answering, respond with a final_message decision.
Do not ask follow-up questions unless specifically requested.
---
{{instruction}}
```

Wait — the framework injects the user instruction automatically for root agents invoked from the CLI.  You do not need to declare a parameter called `instruction` for this to work; the CLI sets it directly.  You can simplify even further:

```markdown
---
id: root
role: Assistant
description: A helpful general-purpose assistant.
parameters: {}
tools: []
subagents: []
skills: []
---
You are a helpful, honest, and concise assistant.
Answer the user's question directly and completely.
---
{{instruction}}
```

**`.env`:**

```
DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...
DEFAULT_MODEL=gpt-4o-mini
AGENT_DIRECTORY=agents
WORLD_DIRECTORY=world
ROOT_AGENT=root
```

That is the entire setup.  Now run it:

```bash
pip install agent_framework

# Interactive console mode
python -m agent_framework --console --env .env

# One-shot mode
python -m agent_framework --env .env --instruction "What is the capital of Japan?"
```

In console mode you get a prompt where you can type instructions.  In one-shot mode the agent runs once and prints the result.

### What happens when you run it

The CLI creates an `AgentHost`, loads the agent registry from `AGENT_DIRECTORY`, picks the agent whose `id` matches `ROOT_AGENT`, and calls `host.run_agent("root", initial_instruction="...")`.

The framework then:

1. Renders the user prompt template (substituting `{{instruction}}` with your text).
2. Assembles the model context: system prompt + user prompt + any tool definitions.
3. Calls the model.
4. Parses the model's JSON response into an `AgentDecision`.
5. Acts on the decision (runs a tool, calls a sub-agent, returns the final message, or asks a question).
6. Loops back to step 2 unless the decision was `final_message`.

If the model returns `final_message`, the loop ends and the result is printed.

### Frontmatter fields in detail

The frontmatter is YAML.  Here is a fully annotated example:

```yaml
---
id: customer_support          # Required. Must match the filename stem (customer_support.md)
                              # and the ROOT_AGENT value in .env if this is the root agent.

role: Customer support agent  # Human-readable role name. Used in traces and sub-agent descriptions.

description: |                # Shown to parent agents as a capability description.
  Handles tier-1 customer support queries about orders, returns, and shipping.
  Escalates complex cases to a human agent via callback.

parameters:                   # Parameters that callers must supply per invocation.
  ticket_text:
    description: The raw text of the customer's support ticket.
    required: true
    type: string
  customer_tier:
    description: Customer tier (standard, premium, enterprise).
    required: false
    type: string
    default: standard

tools:                        # Tool ids this agent may call. Listed names must exist in TOOLS_DIRECTORY.
  - Read
  - WebFetch

subagents:                    # Child agent ids this agent may delegate to.
  - order_lookup
  - shipping_tracker

skills:                       # Skill names this agent may invoke.
  - refund_policy
  - shipping_policy

terminal_tools: []            # Tools that exit the loop immediately when called
                              # (see §3 for details). Usually left empty.
---
```

**Required fields:** `id`.  Everything else has a sensible default (empty lists, no parameters).

**`id` vs filename:** The `id` in frontmatter is what the framework uses at runtime.  The filename is how the file is discovered.  By convention keep them identical (e.g. `id: root` → `root.md`), but the framework does not enforce this.

**`parameters:`** Each parameter has a `description` (shown to callers), `required` (defaults to `true`), `type` (`string`, `integer`, `number`, `boolean`, `object`, `array`), and an optional `default`.  You can also specify a `schema` pointing to a JSON Schema file for complex structural validation.

### Your first agent with parameters

Let us make a more realistic agent — one that formats a meeting summary:

**`agents/summariser.md`:**

```markdown
---
id: summariser
role: Meeting summariser
description: Produces a structured meeting summary from raw notes.
parameters:
  notes:
    description: Raw meeting notes, unformatted.
    required: true
    type: string
  attendees:
    description: Comma-separated list of attendee names.
    required: false
    type: string
    default: ""
tools: []
subagents: []
skills: []
---
You are a professional meeting summariser.
Produce output in the following structure:

**Summary:** one paragraph overview.
**Key decisions:** bullet list.
**Action items:** bullet list with owner and deadline if mentioned.
**Next steps:** brief paragraph.

Be factual — only include information present in the provided notes.
Do not invent action items or decisions not mentioned.
---
Please summarise the following meeting notes.

Notes:
{{notes}}

{% if attendees %}
Attendees: {{attendees}}
{% endif %}
```

Note: the `{% if %}` block shown above is illustrative — the actual template engine is simple substitution, not Jinja.  For conditional content, write your template with the parameter always present and handle the empty string case in the system prompt:

```markdown
---
Summarise these meeting notes.

Notes:
{{notes}}

Attendees (may be empty if not provided): {{attendees}}
```

Run the summariser from the CLI by specifying the agent:

```bash
python -m agent_framework --env .env \
  --agent summariser \
  --instruction "Notes: Alice proposed the new API design. Bob agreed to write tests by Friday. Carol will update docs."
```

Or call it programmatically (more on this in §8):

```python
import asyncio
from agent_framework.host import AgentHost

host = AgentHost.from_env(".env")
result = host.run_agent(
    "summariser",
    parameters={
        "notes": "Alice proposed the new API design. Bob agreed to write tests by Friday.",
        "attendees": "Alice, Bob, Carol",
    },
)
print(result.message)
```

---

## 2. Understanding the decision loop

### Why JSON decisions?

When you ask an LLM "should you use a tool or answer directly?", the model needs a structured way to express that choice.  This framework uses a JSON object called an `AgentDecision`.  The model is instructed (via bundled system templates) to always respond with a JSON object that has a `kind` field.  The framework parses that object, validates it, and acts on it.

You do not write this JSON.  The bundled system prompt templates teach the model how to emit it.  Your job is to write the agent's Markdown contract and trust that the model will produce valid decisions.

If the model produces an invalid decision — wrong `kind`, missing required field, both `tool_name` and `subagent_id` set — the framework raises a `ValueError`.  It does not try to guess what the model meant.  This strictness is documented in `CLAUDE.md` and intentional: silent repair would hide prompt bugs.

### The five decision kinds

Every loop iteration ends with exactly one decision of one of these five kinds:

**`final_message`** — The agent is done.  The `message` field contains the answer.  The loop ends and `AgentResult` is returned to the caller.

```json
{
  "kind": "final_message",
  "message": "The capital of Japan is Tokyo.",
  "parameters": {}
}
```

**`call_tool`** — Invoke a registered tool.  The `tool_name` must be in the agent's `tools:` allow-list.  The `parameters` dict is passed to the tool's `invoke()` method.  The tool result is injected back into the conversation and the loop continues.

```json
{
  "kind": "call_tool",
  "tool_name": "Read",
  "parameters": {
    "file_path": "/project/config.yaml"
  },
  "message": "Reading the configuration file."
}
```

**`call_subagent`** — Delegate work to a child agent.  The `subagent_id` must be in the agent's `subagents:` allow-list.  The `parameters` dict is passed to the child agent as its invocation parameters.  The child's result is injected back and the loop continues.

```json
{
  "kind": "call_subagent",
  "subagent_id": "order_lookup",
  "parameters": {
    "order_id": "ORD-12345"
  },
  "message": "Looking up the order details."
}
```

**`callback`** — Ask a question.  Callbacks can go to the host (a human at the console, a web user) or to the caller agent.  The `intent` field clarifies what kind of question is being asked.  The answer comes back as a user message and the loop continues.

```json
{
  "kind": "callback",
  "intent": "information_request",
  "message": "What is the customer's preferred shipping address?",
  "parameters": {
    "parameter_name": "shipping_address"
  }
}
```

**`invoke_skill`** — Load and inject a named skill into the conversation.  Skills are instruction bundles stored in `skills/` directories.  When invoked, the skill's full content is injected as a user message and the loop continues.

```json
{
  "kind": "invoke_skill",
  "skill_name": "refund_policy",
  "message": "Loading the refund policy before answering.",
  "parameters": {}
}
```

### Callback intents

Callbacks carry an `intent` that tells the caller (human or parent agent) what kind of response is needed.  Intents can appear either as the `kind` field directly (the model emits `information_request` as the top-level kind) or as the `intent` field inside a `callback` decision — the framework normalises both forms.

| Intent | When to use |
|--------|-------------|
| `information_request` | Agent needs a value from the user (e.g. "what is the order number?") |
| `proposal_review` | Agent has a proposed action and wants approval (e.g. "shall I delete these files?") |
| `execution_recovery` | Something went wrong and the agent needs guidance to continue |
| `delegation_return` | Child agent returning to parent with a result or question |
| `policy_or_approval` | Agent needs authorisation for a sensitive action |
| `guardrail_trip` | Agent encountered a policy violation and is stopping to report it |

### Terminal tools

Sometimes you want the model to "call a tool" that is actually an exit point — a way for the agent to signal a structured outcome without executing any code.  List those tool names under `terminal_tools:` in the frontmatter.

When the model calls a terminal tool, the loop exits immediately without running the tool.  The `parameters` from the decision are serialised as JSON and returned as the `AgentResult.message`.  This is useful for agents that produce structured output (e.g. an extraction agent that "calls" a `submit_result` tool with the extracted fields).

```yaml
terminal_tools:
  - submit_result
  - escalate_to_human
```

When `escalate_to_human` is called as a terminal tool, the agent exits with `status="completed"` and `message = json.dumps(decision.parameters)`.  The caller reads the message JSON to get the structured escalation data.

### The loop in pseudocode

Understanding the loop precisely prevents a lot of confusion:

```
run = create_run(parameters)
refresh_parameter_state(run)

# Before-run behaviors fire here (can inject fragments or short-circuit)
early_result = run_before_run_behaviors(run)
if early_result is not None:
    return early_result

while True:
    decision = get_from_runtime_queue(run)  # internal pre-computed decisions first
    if decision is None:
        context = build_context(run)         # assemble messages for the model
        decision = call_model(context)       # one LLM call per iteration

    outcome = dispatch(decision)             # act on the decision

    if outcome is not None:                  # final_message or terminal_tool
        return run_after_run_behaviors(outcome)

    # outcome is None means: loop continues (tool result, subagent result,
    # callback answer, or skill injection was added to the conversation)
```

Each time through the loop the model sees the full growing conversation: its original system prompt, the user's initial instruction, and all the tool results / subagent results / callback answers that have accumulated.  The loop continues until the model emits `final_message` or a terminal tool.

---

## 3. Adding tools

### Why tools?

By default, an agent can only return text.  It cannot read files, run commands, call APIs, or do anything that requires leaving the model's context.  Tools fix this.

A tool is a named capability the model can invoke.  The framework makes the tool's name and parameter schema visible to the model (as part of the context it builds), so the model knows the tool exists and what arguments to pass.  When the model decides to call a tool, the framework validates the call against the agent's allow-list, runs the Python implementation, and feeds the result back into the conversation.

### Built-in tools

Seven tools are registered by default when you create a host.  You do not need to write any files for them — just add their names to an agent's `tools:` list:

| Tool | What it does | Permission check |
|------|-------------|-----------------|
| `Read` | Read a file | No — safe read-only |
| `Write` | Write or create a file | Yes — asks user before writing |
| `Edit` | Replace a substring in a file | Yes — asks user before modifying |
| `Bash` | Execute a shell command | Yes — asks user before executing |
| `Glob` | Find files matching a pattern | No — safe read-only |
| `Grep` | Search file contents with regex | No — safe read-only |
| `WebFetch` | Fetch a URL and return its text | Yes — asks user before network call |

"Permission check" means the tool calls `host.user_comm.request_permission()` before acting.  In console mode the user sees a `[y/n/a/d]` prompt.  `a` = allow all future calls of this tool in this session; `d` = deny all.  In headless mode (`NullUserCommunication`) all permission requests are auto-approved by default — be careful with this in production.

To give your agent the ability to read files and search the web:

```yaml
tools:
  - Read
  - Grep
  - WebFetch
```

The built-in tools operate within the `WORLD_DIRECTORY` you set in `.env`.  File paths are resolved relative to that sandbox, which prevents agents from accidentally reading your SSH keys.

### Writing a custom tool

A custom tool is a pair of files:

```
tools/
  send_email.md
  send_email.py
```

The `.md` file defines the contract.  The `.py` file implements it.

**`tools/send_email.md`:**

```markdown
---
id: send_email
description: Send an email to a recipient. Use this to notify users of outcomes.
parameters:
  - name: to_address
    description: The recipient's email address.
    required: true
    type: string
  - name: subject
    description: Email subject line.
    required: true
    type: string
  - name: body
    description: Email body text (plain text or Markdown).
    required: true
    type: string
  - name: cc_address
    description: Optional CC address.
    required: false
    type: string
---
Sends an email via the configured SMTP server.
Returns a confirmation message with the message ID on success.
```

**`tools/send_email.py`:**

```python
from __future__ import annotations

from typing import Any

from agent_framework.tool import Tool, ToolDefinition


def build_tool(definition: ToolDefinition) -> Tool:
    """Required entry point called by ToolRegistry when loading this tool."""
    return SendEmailTool(definition=definition)


class SendEmailTool(Tool):
    def invoke(self, arguments: dict[str, Any], host: Any) -> str:
        to_address = arguments["to_address"]
        subject = arguments["subject"]
        body = arguments["body"]
        cc = arguments.get("cc_address", "")

        # In a real implementation, call your email service here.
        # This example just logs and returns a confirmation.
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = "noreply@example.com"
        msg["To"] = to_address
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.set_content(body)

        # Uncomment for real sending:
        # with smtplib.SMTP("smtp.example.com", 587) as smtp:
        #     smtp.send_message(msg)

        return f"Email sent to {to_address} with subject '{subject}'. Message-ID: mock-001."
```

Key rules:
- The module must export a `build_tool(definition: ToolDefinition) -> Tool` function.
- The `Tool` subclass must implement `invoke(self, arguments: dict[str, Any], host: Any) -> str`.
- The `id` in the `.md` frontmatter must match the filename stem (`send_email`).
- The same `id` must appear in the agent's `tools:` list for the agent to use it.

To add the tool to an agent:

```yaml
tools:
  - Read
  - send_email
```

Then set `TOOLS_DIRECTORY=tools` in `.env`.

### Tools that need permission checks

To ask the user before acting (like the built-in `Write` tool does), call `host.user_comm.request_permission()` inside `invoke`:

```python
def invoke(self, arguments: dict[str, Any], host: Any) -> str:
    to_address = arguments["to_address"]
    subject = arguments["subject"]

    allowed = host.user_comm.request_permission(
        tool_name="send_email",
        action="send",
        resource=to_address,
        summary=f"Send email to {to_address}: {subject}",
    )
    if not allowed:
        return "Email cancelled by user."

    # ... proceed with sending
    return "Email sent."
```

`request_permission` is synchronous.  It blocks until the user answers.  In the evaluator web UI, the request appears in the Conversation pane.

### Tool parameter schema

For simple flat parameters, the `parameters:` list in the frontmatter is enough.  For complex nested arguments (e.g. a JSON object with varying shape), you can provide a full JSON Schema:

**`tools/create_order.md`:**

```markdown
---
id: create_order
description: Create a new order in the order management system.
parameters_schema:
  type: object
  properties:
    order:
      type: object
      properties:
        customer_id:
          type: string
          description: Customer identifier.
        items:
          type: array
          description: List of order line items.
          items:
            type: object
            properties:
              sku:
                type: string
              quantity:
                type: integer
              unit_price:
                type: number
      required: [customer_id, items]
  required: [order]
---
Creates a new order. Pass the full order object in the `order` field.
```

When `parameters_schema` is present in the frontmatter, it is used as-is as the JSON Schema for the tool's parameters, overriding the flat `parameters:` list.  This allows arbitrarily complex tool signatures.

### `MISSING_TOOL_POLICY`

If an agent's `tools:` list references a tool that cannot be loaded (file missing, Python error in the module), the behaviour depends on the `MISSING_TOOL_POLICY` setting in `.env`:

```
MISSING_TOOL_POLICY=graceful   # Skip the unloadable tool, log a warning, continue (default)
MISSING_TOOL_POLICY=strict     # Fail immediately with an error
```

Use `strict` in production when you want to catch tool configuration errors at startup.  Use `graceful` during development when you are iterating on tool files and some may be incomplete.

---

## 4. Sub-agents

### When to use sub-agents

A single agent with a long system prompt that tries to do everything is hard to test, hard to debug, and tends to produce inconsistent results.  Sub-agents let you break complex work into specialised units, each with its own focused prompt and limited capabilities.

Think of it like a team: the root agent is the project manager who understands the big picture and knows which specialist to call.  The sub-agents are the specialists who know their domain deeply but do not need to know anything about the other parts of the project.

A good rule of thumb: if your system prompt has more than one logically distinct task (e.g. "understand the customer's problem" and "look up the order status" and "compose a reply"), consider whether those could be separate agents with separate prompts.

### Defining a parent-child relationship

In the parent agent's frontmatter, list the child agents by id:

```yaml
# agents/customer_support.md
subagents:
  - order_lookup
  - refund_processor
```

In the child agent's file, write the focused prompt:

**`agents/order_lookup.md`:**

```markdown
---
id: order_lookup
role: Order lookup specialist
description: |
  Retrieves order details from the order management system.
  Input: order_id (string). Output: complete order record including status,
  items, shipping address, and tracking number.
parameters:
  order_id:
    description: The order identifier to look up.
    required: true
    type: string
tools:
  - WebFetch
subagents: []
skills: []
---
You are an order lookup specialist.
When given an order_id, retrieve the complete order record from the order management API.

API endpoint: https://api.example.com/orders/{order_id}
Authentication: use the Bearer token from the Authorization header.

Always return the full JSON record of the order.
If the order is not found, return an error message explaining the order_id was not found.
---
Look up order: {{order_id}}
```

The parent agent can now call this child by emitting a `call_subagent` decision:

```json
{
  "kind": "call_subagent",
  "subagent_id": "order_lookup",
  "parameters": {
    "order_id": "ORD-98765"
  },
  "message": "Retrieving the order details before composing a response."
}
```

The framework looks up the `order_lookup` agent, validates that the caller has it in its `subagents:` allow-list, and calls `agent.run(parameters={"order_id": "ORD-98765"})`.  The result comes back as a user message in the parent's conversation.

### Callbacks between agents

When a child agent needs more information from its parent, it emits a `callback` decision with `intent: delegation_return` (or `information_request` if it needs a specific value).

The parent agent implements `respond_to_callback()` through its behavior (see §5).  If no behavior implements it, the callback propagates to the host (i.e. to the human user).

This creates a natural request-response pattern between agents:

```
Parent: "look up order ORD-98765"
  → Child starts
  → Child: "I need the customer's date of birth to verify identity"  (callback)
  → Parent: "date of birth is 1985-03-12"
  → Child: "order found: ..." (final_message)
  → Parent continues with the order details
```

### Designing good sub-agent boundaries

**Good boundary:** Each sub-agent has one clear responsibility.  Its description precisely states its inputs and outputs.  It does not need to know anything about the larger system it is part of.

```yaml
# Good — clear single responsibility
description: |
  Verifies whether a customer is eligible for a refund.
  Input: order_id (string), reason (string).
  Output: "eligible" or "not_eligible" with explanation.
```

**Poor boundary:** A sub-agent that does "everything for order management."  Its prompt will be long, its outputs will be unpredictable, and the parent agent will not know how to interpret the result.

**Parameter matching:** The parameters you pass in the `call_subagent` decision must match what the child agent's `parameters:` section declares.  Required parameters that are missing will cause the child to request them via callback.

### Allowing the parent to see the child's sub-agents

Sub-agents can themselves have sub-agents.  The framework supports arbitrary depth.  Each level only knows about its immediate children — the parent does not have visibility into what the child calls internally.

This containment is a feature, not a limitation.  It means you can refactor a child agent's internals (changing its tools, sub-agents, or even replacing it entirely) without the parent knowing or caring.

---

## 5. Extending agents with behaviors

### What is a behavior?

A behavior is a small Python module that attaches to an agent at load time and hooks into the agent's lifecycle without modifying the framework or forking the agent class.  Behaviors are for cross-cutting concerns: logging, guardrails, retries, telemetry, response post-processing.

If your requirement is "before every tool call, log the tool name and arguments to our monitoring system", a behavior is the right place for that.  If your requirement is "this agent should only answer questions about cooking", that belongs in the system prompt.

### The sidecar JSON file

Behaviors are attached via a sidecar JSON file next to the agent's Markdown file:

```
agents/
  root.md
  root.json       ← sidecar, same name as the .md
```

**`agents/root.json`:**

```json
{
  "model": "gpt-4o",
  "temperature": 0.1,
  "provider": "openai",
  "behaviors": ["audit_logger", "guardrail"]
}
```

The sidecar JSON can set:

| Field | Type | Meaning |
|-------|------|---------|
| `model` | string or comma-separated string | Model(s) to use for this agent (overrides `DEFAULT_MODEL`) |
| `temperature` | float | Sampling temperature (default: 0.2) |
| `provider` | string | Provider name (default: `DEFAULT_PROVIDER`) |
| `behaviors` | list of strings | Behavior ids to attach, in order |
| `can_query_caller` | bool | Whether the agent may send callbacks to its caller (default: true) |
| `can_use_host_interaction` | bool | Whether the agent may request user input from the host (default: true) |

The `model` field accepts comma-separated fallbacks: `"gpt-4o,gpt-4o-mini"` means "try gpt-4o first, fall back to gpt-4o-mini on failure."

### Behavior resolution

Behavior ids are resolved to Python files in this order:

1. **Agent-local:** `agents/{behavior_id}.py` — a behavior next to the agent that uses it.
2. **Shared:** `behaviors/{behavior_id}.py` — a directory named `behaviors/` at the same level as the `agents/` directory.

So if your project looks like:

```
project/
  agents/
    root.md
    root.json
  behaviors/
    audit_logger.py
    guardrail.py
```

And `root.json` lists `"behaviors": ["audit_logger"]`, the framework finds `behaviors/audit_logger.py`.

### Writing a behavior

Every behavior module must export a `build_behavior() -> AgentBehavior` function:

```python
# behaviors/audit_logger.py
from __future__ import annotations

import logging
from typing import Any

from agent_framework.agents.agent_behavior import AgentBehavior
from agent_framework.agents.tool_start_event import ToolStartEvent
from agent_framework.agents.tool_end_event import ToolEndEvent
from agent_framework.agents.agent_hook_decision import AgentHookDecision
from agent_framework.agents.agent_end_hook_decision import AgentEndHookDecision

_LOGGER = logging.getLogger("audit_logger")


def build_behavior() -> AgentBehavior:
    return AuditLoggerBehavior()


class AuditLoggerBehavior(AgentBehavior):
    def attach(self, agent):
        """Subscribe to the hooks we care about."""
        agent.on_pre_tool += self._on_pre_tool
        agent.on_post_tool += self._on_post_tool

    def _on_pre_tool(self, event: ToolStartEvent):
        _LOGGER.info(
            "Tool call starting: agent=%s tool=%s input=%s",
            event.invocation.agent_id,
            event.tool_name,
            event.tool_input,
        )

    def _on_post_tool(self, event: ToolEndEvent):
        _LOGGER.info(
            "Tool call finished: agent=%s tool=%s result_preview=%s",
            event.invocation.agent_id,
            event.tool_name,
            event.result[:200],
        )
```

### Available hooks

The `attach(agent)` method receives the live `Agent` instance.  You subscribe to lifecycle hooks by appending callables to the agent's hook sequences:

| Hook | Event type | When it fires |
|------|-----------|--------------|
| `agent.on_pre_agent` | `AgentStartEvent` | Before the main loop begins |
| `agent.on_post_agent` | `AgentEndEvent` | After the main loop ends |
| `agent.on_pre_tool` | `ToolStartEvent` | Before a tool is executed |
| `agent.on_post_tool` | `ToolEndEvent` | After a tool returns |
| `agent.on_pre_subagent` | `SubagentStartEvent` | Before a child agent is called |
| `agent.on_post_subagent` | `SubagentEndEvent` | After a child agent returns |
| `agent.on_pre_skill` | `SkillStartEvent` | Before a skill is loaded |
| `agent.on_post_skill` | `SkillEndEvent` | After a skill is injected |
| `agent.on_pre_model` | `ModelStartEvent` | Before the LLM is called |
| `agent.on_post_model` | `ModelEndEvent` | After the LLM responds |

### The `AgentBehavior` base class methods

In addition to hooks, the base class has two overridable methods that are called by the framework directly:

**`before_run(agent, host, *, run, caller_id)`** — Called after initial parameter state is set up, before the main loop starts.  Returns `AgentHookDecision | None`.

```python
def before_run(self, agent, host, *, run, caller_id):
    # Check a required parameter before the loop starts
    if not run.parameter_values.get("customer_id"):
        # Short-circuit: return a result immediately without running the loop
        from agent_framework.agents.agent_result import AgentResult
        from agent_framework.agents.agent_hook_decision import AgentHookDecision
        return AgentHookDecision(
            final_result=AgentResult(
                status="failed",
                message="customer_id is required but was not provided.",
                prompt=run.rendered_prompt,
            )
        )
    return None  # continue normally
```

**`after_run(agent, host, *, run, caller_id, result)`** — Called after the agent produces a result.  Can replace the result, augment the prompt and request another loop iteration, or pass through unchanged.

```python
def after_run(self, agent, host, *, run, caller_id, result):
    # Post-process the result: strip preamble from the message
    import re
    cleaned = re.sub(r"^(Sure|Certainly|Of course)[,!]\s*", "", result.message)
    if cleaned != result.message:
        from agent_framework.agents.agent_result import AgentResult
        return AgentResult(
            status=result.status,
            message=cleaned,
            decision=result.decision,
            prompt=result.prompt,
        )
    return None  # unchanged
```

**`respond_to_callback(agent, host, *, callee_id, prompt)`** — Called when a child agent sends a callback to this agent.  Return a string answer, or `None` to let it propagate to the user.

```python
def respond_to_callback(self, agent, host, *, callee_id, prompt):
    # Automatically answer a specific question from a child agent
    if "date of birth" in prompt.lower():
        # Look up from the run's parameters
        return agent._last_run.parameter_values.get("date_of_birth", "Unknown")
    return None  # let it reach the user
```

### Guardrail example

A guardrail behavior intercepts tool calls and can block them:

```python
# behaviors/guardrail.py
from __future__ import annotations

from agent_framework.agents.agent_behavior import AgentBehavior
from agent_framework.agents.tool_start_event import ToolStartEvent
from agent_framework.agents.tool_hook_decision import ToolHookDecision


def build_behavior() -> AgentBehavior:
    return GuardrailBehavior()


class GuardrailBehavior(AgentBehavior):
    # Paths the agent is not allowed to read or write
    BLOCKED_PATHS = {"/etc/passwd", "/etc/shadow", "~/.ssh"}

    def attach(self, agent):
        agent.on_pre_tool += self._check_tool

    def _check_tool(self, event: ToolStartEvent) -> ToolHookDecision | None:
        if event.tool_name in ("Read", "Write", "Edit"):
            path = str(event.tool_input.get("file_path", ""))
            for blocked in self.BLOCKED_PATHS:
                if blocked in path:
                    return ToolHookDecision(
                        continue_run=True,          # do NOT exit the agent
                        final_result=None,          # do NOT end the run
                        system_message=f"Access to {path} is blocked by policy.",
                        updated_tool_input=None,    # do not change the input
                    )
        return None  # allow
```

When `on_pre_tool` returns a `ToolHookDecision` with `system_message`, that message is injected into the conversation as a `<system_message>` fragment, giving the model context about what happened.  The agent can then decide how to proceed.

---

## 6. Skills and MCP

### Skills — reusable instruction bundles

A skill is a set of instructions the model can load on demand.  Unlike a system prompt (which is always loaded), skills are only injected when the model explicitly invokes them.  This keeps the initial context small and lets the model reach for detailed instructions only when it needs them.

Good uses for skills:
- A detailed refund policy the support agent needs occasionally.
- A coding style guide the coding assistant should follow.
- A step-by-step procedure for a complex task the agent handles sometimes but not always.
- Reference material that is too long to put in the system prompt on every call.

Skills are NOT a good fit for:
- Instructions the agent always needs (put those in the system prompt).
- Dynamic data that changes per-invocation (pass those as parameters or tool results).

### Skill file structure

Skills live in a configured directory (`SKILLS_DIRECTORY` in `.env`).  Each skill is a `.md` file:

```
skills/
  refund_policy/
    SKILL.md
    policy_exceptions.md    ← additional files in the skill directory
  shipping_policy/
    SKILL.md
```

**`skills/refund_policy/SKILL.md`:**

```markdown
---
id: refund_policy
name: refund_policy
description: Complete refund eligibility rules and processing steps for customer service agents.
priority: 100
---

# Refund Policy

## Eligibility

A refund is eligible when:
- The item was received damaged or defective.
- The item was not as described in the product listing.
- The return is requested within 30 days of delivery.
- The item is in its original packaging.

## Non-refundable Items

- Digital downloads once accessed.
- Personalised or custom-made items.
- Sale items marked "final sale".

## Processing Steps

1. Verify the order number and purchase date.
2. Confirm the item is in the eligible category.
3. Check whether the 30-day window is still open.
4. If eligible: initiate refund in the order management system.
5. Send confirmation email to the customer.

Full exceptions list: see policy_exceptions.md in this skill directory.
```

The `description` field is what the model sees in the skills catalog — it decides whether to invoke the skill based on this text.  Write it precisely so the model invokes the skill when it actually needs it and not just because the description sounds vaguely relevant.

`priority` (integer, lower is higher priority in the catalog) controls which skills are dropped first when the catalog would exceed `SKILLS_CATALOG_MAX_TOKENS`.

### Agent configuration for skills

In the agent frontmatter, list the skill names the agent is allowed to invoke:

```yaml
skills:
  - refund_policy
  - shipping_policy
```

This is the safety boundary.  Even if more skills exist in the directory, the agent can only invoke the ones it is explicitly allowed to.

### How skill invocation works in practice

When the model decides it needs the refund policy, it emits:

```json
{
  "kind": "invoke_skill",
  "skill_name": "refund_policy",
  "message": "I need to check the refund policy before answering."
}
```

The framework loads the full `SKILL.md` content (and lists any additional files in the skill directory as references), injects it as a user message, and the loop continues.  On the next iteration, the model has the full policy in its context and can answer the customer's question.

The catalog (names + descriptions of all allowed skills) is injected at position 2 in the conversation — after the system prompt and user prompt, before any conversation history.  The model always knows what skills are available without needing to load their full content.

### MCP — Model Context Protocol

MCP lets you connect external tool servers to the framework.  Instead of writing a Python tool module for every external service, you run an MCP server (which can be in any language) and the framework bridges its tools into the agent's tool registry.

**Configuration:**

Create a `.mcp.json` file:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {}
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..."
      }
    }
  }
}
```

The `.mcp.json` file is auto-discovered by walking up from the current working directory.  You can also set `MCP_CONFIG_PATH` in `.env` to point to it explicitly.

**Enabling MCP:**

```
MCP_ENABLED=true
MCP_CONFIG_PATH=.mcp.json   # optional if in the project root or parent directories
```

After `await host.start()`, tools from MCP servers appear with qualified names like `mcp__filesystem__read_file` and `mcp__github__create_issue`.

**Using MCP tools in an agent:**

```yaml
tools:
  - Read              # built-in
  - mcp__github__create_issue
  - mcp__github__list_pull_requests
```

You must explicitly allow each MCP tool in the agent's `tools:` list — you do not get all MCP tools by adding the server; you choose which ones this agent may call.

**Setting `MCP_ENABLED=false`** turns off all MCP integration.  The host starts faster and no MCP processes are spawned.  Useful for environments where MCP servers are not available.

---

## 7. Configuration

### The `.env` file

The `.env` file is the central configuration for a framework deployment.  All paths are resolved relative to the directory containing the `.env` file, not the current working directory when you run Python.  This means you can `cd` anywhere and `python -m agent_framework --env /absolute/path/to/.env` and everything will resolve correctly.

### Full configuration reference

```
# ─── Provider ────────────────────────────────────────────────────────────────

DEFAULT_PROVIDER=openai      # or: dial
OPENAI_API_KEY=sk-...

# For DIAL (OpenAI-compatible endpoint):
# DEFAULT_PROVIDER=dial
# DIAL_BASE_URL=https://your-dial.example.com
# DIAL_API_VERSION=2024-10-21
# DIAL_API_KEY=...

# ─── Models ──────────────────────────────────────────────────────────────────

# Comma-separated list: first model is tried first, fallback on failure.
DEFAULT_MODEL=gpt-4o,gpt-4o-mini

# Per-agent overrides: pipe separates agents, comma separates fallback models.
# Syntax: agent_id=model1,model2|other_agent=model3
AGENT_MODELS=order_lookup=gpt-4o-mini|refund_processor=gpt-4o,gpt-4o-mini

# ─── Directories ─────────────────────────────────────────────────────────────

AGENT_DIRECTORY=agents
TOOLS_DIRECTORY=tools
WORLD_DIRECTORY=world          # sandbox root for file operations
ROOT_AGENT=root                # default agent id for CLI and evaluator

# Skills: one directory or multiple comma-separated directories
SKILLS_DIRECTORY=skills
# SKILLS_DIRECTORIES=skills/shared,skills/project-specific

# Skills catalog budget (tokens). Skills exceeding this are dropped
# by priority (lower priority number dropped first).
SKILLS_CATALOG_MAX_TOKENS=2000

# Commands (slash commands): one or multiple directories
# COMMANDS_DIRECTORY=commands
# COMMANDS_DIRECTORIES=commands/global,commands/project

# Evaluator initializers directory
# AGENT_EVAL_INITIALIZER_DIR=eval/initializers

# ─── Tools ───────────────────────────────────────────────────────────────────

# graceful: skip unloadable tools, log warning, continue (default)
# strict: fail immediately if any declared tool cannot be loaded
MISSING_TOOL_POLICY=graceful

# ─── MCP ─────────────────────────────────────────────────────────────────────

MCP_ENABLED=false              # set true to enable MCP
MCP_CONFIG_PATH=.mcp.json     # optional: explicit path to .mcp.json

# ─── Evaluation ──────────────────────────────────────────────────────────────

# Model used by the evaluator LLM. Defaults to DEFAULT_MODEL if not set.
AGENT_EVAL_MODEL=gpt-4o-mini
```

### Model fallback

When `DEFAULT_MODEL=gpt-4o,gpt-4o-mini`, the framework tries `gpt-4o` first.  If the call fails (rate limit, overload, model unavailable), it falls back to `gpt-4o-mini` for that request.  On subsequent requests it tries `gpt-4o` again — the fallback is per-call, not sticky.

Per-agent overrides in `AGENT_MODELS` take precedence over `DEFAULT_MODEL` for those specific agents.  This lets you use a cheap, fast model for high-volume agents and a more capable model for complex reasoning tasks:

```
AGENT_MODELS=triage=gpt-4o-mini|analysis=gpt-4o|writing=gpt-4o,gpt-4o-mini
```

Model overrides can also be set from the CLI:

```bash
python -m agent_framework --env .env --model gpt-4o --instruction "..."
# or with fallbacks:
python -m agent_framework --env .env --model gpt-4o,gpt-4o-mini --instruction "..."
```

### Agent-level model tuning

As an alternative to `AGENT_MODELS`, you can set the model directly in the sidecar JSON:

**`agents/analysis.json`:**

```json
{
  "model": "gpt-4o",
  "temperature": 0.0
}
```

Priority (highest to lowest):
1. CLI `--model` flag
2. Sidecar JSON `model` field
3. `AGENT_MODELS` in `.env`
4. `DEFAULT_MODEL` in `.env`

### DIAL provider

DIAL is an OpenAI-compatible LLM proxy.  Install the extra:

```bash
pip install "agent_framework[dial]"
```

Configure in `.env`:

```
DEFAULT_PROVIDER=dial
DIAL_BASE_URL=https://your-dial-instance.example.com
DIAL_API_VERSION=2024-10-21
DIAL_API_KEY=your-dial-key
DEFAULT_MODEL=gpt-4o
```

With DIAL, the model name is the deployment name.  Everything else — tools, sub-agents, skills — works identically.  See [Using DIAL](using-dial.md) for the full setup guide.

---

## 8. The host

### What is `AgentHost`?

`AgentHost` is the central runtime object.  It owns:

- The **agent registry** — discovers and caches agent files.
- The **tool registry** — discovers and caches tool files; registers built-ins.
- The **command registry** — discovers and caches slash commands.
- The **model driver** — the connection to the LLM provider.
- The **user communication** channel — how the host talks to a human (console, web, null).
- The **MCP manager** — optional, starts MCP servers after `host.start()`.
- The **runtime tracer** — the event bus for all trace events.
- The **audit tracer** — optional JSONL audit logger.
- The **skill registry** — discovers skills and serves the catalog.
- The **conversation store** — optional, for multi-turn sessions.

You never instantiate `AgentHost` directly.  Use one of the factory methods.

### Factory methods

**`AgentHost.from_env(env_path)`** — Creates a host from a `.env` file, for programmatic use.  Does not wire console I/O.

```python
from agent_framework.host import AgentHost

host = AgentHost.from_env(".env")
result = host.run_agent("root", initial_instruction="Hello!")
print(result.message)
```

**`AgentHost.from_env_console(env_path)`** — Creates a host wired to console I/O.  This is what the CLI uses.  It sets up `ConsoleUserCommunication` and calls `host.start()` synchronously.  Use this when you want an interactive session from a script.

```python
host = AgentHost.from_env_console(".env")
# From here you can run agents that ask questions to the user at the console
result = host.run_agent("root", initial_instruction="Summarise this document.")
```

**`AgentHost.create(model_driver=..., builtin_tools=True)`** — Creates a host without any `.env` file.  All configuration is supplied programmatically.  Useful for testing or embedding the framework in another service that has its own configuration system.

```python
from agent_framework.host import AgentHost
from agent_framework.model import OpenAiModelDriver
from agent_framework.config import HostConfig

config = HostConfig(
    default_provider="openai",
    default_model=("gpt-4o-mini",),
    agent_directories=("agents",),
    tool_directories=("tools",),
    world_directory="world",
    root_agent="root",
)
driver = OpenAiModelDriver(api_key="sk-...", base_url=None)
host = AgentHost.create(config=config, model_driver=driver)
result = host.run_agent("root", initial_instruction="Hello!")
```

### `run_agent` in detail

```python
result = host.run_agent(
    agent_id="customer_support",
    initial_instruction="My order ORD-123 hasn't arrived.",
    parameters={"customer_tier": "premium"},
)
```

`run_agent` finds the agent in the registry, renders its user prompt template with the supplied instruction and parameters, runs the decision loop, and returns an `AgentResult`.

`AgentResult` has:

| Field | Type | Meaning |
|-------|------|---------|
| `status` | `"completed"` or `"failed"` or `"stopped"` | Outcome of the run |
| `message` | `str` | Final answer or error message |
| `decision` | `AgentDecision | None` | The decision that ended the run |
| `prompt` | `str` | The rendered user prompt that was sent |

For terminal tool runs, `message` is the JSON of the decision's `parameters`.

### Headless API calls with `complete` and `complete_async`

Sometimes you want to call the model directly without running a full agent loop.  For example, in a pipeline where you have assembled the prompt yourself:

```python
from agent_framework.model import ModelContext

context = ModelContext(
    system_prompt="You are a JSON extractor.",
    user_prompt="Extract name and email from: Alice Chen, alice@example.com",
    messages=(
        {"role": "system", "content": "You are a JSON extractor."},
        {"role": "user", "content": "Extract name and email from: Alice Chen, alice@example.com"},
    ),
    response_mode="json_object",
    tools=(),
    subagents=(),
    skills=(),
)

response = host.complete(context, model_names=("gpt-4o-mini",))
print(response.payload)  # {"name": "Alice Chen", "email": "alice@example.com"}
```

`complete()` is synchronous.  `complete_async()` is the async version.  Both bypass the decision loop entirely — one call, one response.

### Multi-turn conversations with the conversation store

For applications where you want the agent to remember previous messages across calls, attach a conversation store:

```python
from agent_framework.conversation import InMemoryConversationStore

store = InMemoryConversationStore(ttl_seconds=3600)
host = AgentHost.from_env(".env")
host.conversation_store = store

# First call
result1 = host.run_agent(
    "root",
    initial_instruction="My name is Alice.",
    conversation_id="session-001",
)

# Second call — the agent remembers the previous turn
result2 = host.run_agent(
    "root",
    initial_instruction="What is my name?",
    conversation_id="session-001",
)
# result2.message will mention Alice
```

The conversation store persists message history keyed by `conversation_id`.  `InMemoryConversationStore` keeps everything in RAM with optional TTL.  For persistent storage across restarts, implement the `ConversationStore` protocol backed by a database.

### Embedding the host in FastAPI

```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from agent_framework.host import AgentHost

_host: AgentHost | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _host
    _host = AgentHost.from_env(".env")
    await _host.start()          # discovers registries, starts MCP if enabled
    yield
    await _host.aclose()         # shuts down MCP and async driver gracefully


app = FastAPI(lifespan=lifespan)


class RunRequest(BaseModel):
    instruction: str
    conversation_id: str | None = None


@app.post("/run")
async def run(req: RunRequest):
    if _host is None:
        raise HTTPException(503, "Host not ready")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _host.run_agent(
            "root",
            initial_instruction=req.instruction,
            conversation_id=req.conversation_id,
        ),
    )
    return {"status": result.status, "message": result.message}
```

Note: `run_agent` is synchronous (it drives a synchronous model driver).  When embedding in async frameworks, run it in a thread pool executor as shown.  Alternatively, use an async model driver (e.g. `DialChatCompletionsDriver`) and the `complete_async` path for full async execution.

### Host lifecycle

```
host = AgentHost.from_env(".env")
# At this point: config loaded, registries created but not discovered.
# No MCP servers started yet.

await host.start()
# Now: registries discovered (agent/tool files catalogued),
# MCP servers started and bridged into tool_registry.

# ... use the host ...

await host.aclose()
# MCP servers stopped. Async driver cleaned up. Thread pool shut down.
```

`from_env_console()` calls `start()` internally (synchronously).  If you use `from_env()` or `create()`, call `await host.start()` before the first `run_agent()` call to ensure registries are populated.

---

## 9. Tracing and debugging

### The three tracing layers

When something goes wrong, you need to see what actually happened.  The framework has three complementary layers, each at a different level of detail:

**Layer 1 — Unified runtime tracer:** Structured events for everything the agent does at the runtime level.  Tool calls, sub-agent calls, decisions, callbacks, model calls, session start/end.  This is the primary layer for debugging agent behaviour.

**Layer 2 — LLM trace:** Raw request and response payloads for every LLM call.  This is what you look at when the model is producing wrong decisions — you see exactly what messages it received and what it returned.

**Layer 3 — Audit JSONL:** Richer per-run records including the full LLM exchange, all callbacks, all tool calls, and skill invocations for each agent run.  Good for compliance, post-mortem analysis, and the bundled trace viewer.

You do not need all three layers at once.  Start with Layer 1 (or the evaluator UI which gives you Layer 1 visually), and only reach for Layer 2 when you suspect a prompt issue.

### Enabling tracing from the CLI

```bash
# Layer 1: write unified trace events to JSONL
python -m agent_framework \
  --env .env \
  --runtime-trace-jsonl ./logs/run.jsonl \
  --instruction "Hello!"

# Layer 2: log raw LLM requests and responses
python -m agent_framework \
  --env .env \
  --llm-trace file \
  --instruction "Hello!"

# Both layers simultaneously
python -m agent_framework \
  --env .env \
  --runtime-trace-jsonl ./logs/run.jsonl \
  --llm-trace both \
  --instruction "Hello!"
```

`--llm-trace` options:
- `console` — print to stderr
- `file` — write to `logs/llm-trace-{timestamp}.jsonl`
- `both` — console and file

### Attaching a tracer programmatically

```python
from agent_framework.host import AgentHost
from agent_framework.tracing import CompositeRuntimeTracer

class MySubscriber:
    def consume(self, event) -> None:
        print(f"[{event.channel}] {event.kind}: {event.summary}")

host = AgentHost.from_env(".env")
tracer = CompositeRuntimeTracer()
tracer.subscribe(MySubscriber())
host.runtime_tracer = tracer

result = host.run_agent("root", initial_instruction="Hello!")
```

Any object with a `consume(event: TraceEvent) -> None` method can be a subscriber.  You can have multiple subscribers on the same `CompositeRuntimeTracer`.

### Enabling the audit tracer

```python
host = AgentHost.from_env(".env")
host.enable_audit_trace(output_dir="logs/")

result = host.run_agent("root", initial_instruction="Hello!")
# After the run, logs/trace-{timestamp}.jsonl contains the full audit record
```

The audit JSONL can be opened in the bundled `trace_viewer.html`:

```bash
python -m http.server 8080 --directory .
# Open http://localhost:8080/trace_viewer.html in your browser
# Drag the JSONL file onto the page
```

### Using the evaluator UI for debugging

The evaluator web UI is the most powerful debugging tool.  It gives you a live hierarchical trace view, per-agent call frames, channel filtering, log level controls, and the ability to re-evaluate after changing prompts.

```bash
pip install "agent_framework[web]"
python -m agent_framework_evaluator web --env .env
# Open http://127.0.0.1:8123/
```

Run your agent in the UI.  Open the Spans pane.  Set the log level to `debug`.  You will see:

- Each agent call as a collapsible frame with a spinner while running
- Tool calls nested under the agent that called them
- Sub-agent calls nested under the parent agent
- LLM request and response events (with the full message list if you expand them)
- Evaluator trace events when you click Evaluate

This is much faster than reading JSONL files.  See [Using the agent evaluator](using-agent-evaluator.md) for the full UI guide.

### Diagnosing common problems

**The agent loops forever (never returns `final_message`):**

Set log level to `debug` in the evaluator, watch the `runtime.decision_made` events.  The model is likely producing the same decision repeatedly.  Common causes:
- The system prompt does not clearly tell the model when to call `final_message`.
- A tool keeps returning an error and the model keeps retrying without a termination condition.
- The model is in an infinite subagent delegation cycle.

Fix: add explicit termination instructions to the system prompt: "After gathering all required information, always respond with a `final_message` decision."

**The model calls a tool that is not in the `tools:` list:**

The agent's `tools:` list acts as the safety boundary.  If the model hallucinates a tool name not in the list, the framework rejects it with an error message injected into the conversation.  The model then has a chance to self-correct.

If this happens often, check:
- Is the tool listed in `tools:`?  (Typo?)
- Does the tool file exist and load successfully?  (Check `MISSING_TOOL_POLICY=strict` to surface this early.)
- Is the model confusing the tool name with something in the system prompt?

**The model output is not valid JSON / `AgentDecision` parsing fails:**

The bundled system templates instruct the model to output JSON decisions.  If parsing fails, the framework raises `ValueError`.  Common causes:
- The model returned markdown-fenced JSON (```json ... ```) — the framework strips fences, but check the LLM trace to confirm.
- The model returned a `kind` value not in the allowed set — this is a prompt issue; the model was not given the right instructions.
- The model is using a `response_mode` that does not match (check the driver's capabilities).

**A sub-agent never receives the right parameters:**

When a parent calls a child with `call_subagent`, the `parameters` dict in the decision must match the child's declared `parameters:`.  If required parameters are missing, the child will immediately callback asking for them.  Check the parent's system prompt — it should know what parameters to supply when calling each child.

---

## 10. Project structure and building real systems

### A recommended layout

For a project of moderate complexity:

```
my_project/
├── .env                          # Configuration (never commit to git)
├── .env.example                  # Template with placeholder values (commit this)
├── .mcp.json                     # MCP server configuration (if used)
├── agents/
│   ├── root.md                   # Root agent
│   ├── root.json                 # Model tuning for root
│   ├── order_lookup.md
│   ├── order_lookup.json
│   ├── refund_processor.md
│   └── summariser.md
├── behaviors/
│   ├── audit_logger.py           # Shared behavior
│   └── guardrail.py              # Shared behavior
├── tools/
│   ├── send_email.md
│   ├── send_email.py
│   ├── create_order.md
│   └── create_order.py
├── skills/
│   ├── refund_policy/
│   │   ├── SKILL.md
│   │   └── policy_exceptions.md
│   └── shipping_policy/
│       └── SKILL.md
├── world/                        # Sandbox for file operations
├── eval/                         # Evaluation suite
│   ├── initializers/
│   │   ├── customer_support_init.py
│   │   └── order_lookup_init.py
│   └── cases/
│       ├── 01_standard_return.md
│       └── 02_damaged_item.md
├── logs/                         # JSONL traces (gitignore this)
└── tests/
    ├── test_agents.py
    └── test_tools.py
```

### Testing agents

The cleanest way to test agents in isolation is to create a minimal host without real LLM calls:

```python
import pytest
from agent_framework.host import AgentHost
from agent_framework.config import HostConfig
from agent_framework.model import ModelDriver, ModelContext, ModelResponse


class FakeDriver:
    """Fake model driver that returns a pre-set response."""

    def __init__(self, response_payload: dict):
        self._payload = response_payload

    def decide(self, *, agent_id, provider_name, model_names, temperature, context):
        import json
        return ModelResponse(
            raw_text=json.dumps(self._payload),
            payload=self._payload,
        )


def make_test_host(response_payload: dict) -> AgentHost:
    config = HostConfig(
        default_provider="fake",
        default_model=("fake-model",),
        agent_directories=("agents",),
        tool_directories=("tools",),
        world_directory="world",
        root_agent="root",
    )
    return AgentHost.create(
        config=config,
        model_driver=FakeDriver(response_payload),
        builtin_tools=False,   # don't register file/bash tools in tests
    )


def test_root_agent_returns_final_message():
    host = make_test_host({
        "kind": "final_message",
        "message": "Test answer.",
        "parameters": {},
    })
    result = host.run_agent("root", initial_instruction="Test question.")
    assert result.status == "completed"
    assert result.message == "Test answer."
```

For integration tests with a real model but controlled responses, use the evaluator's `evaluate` subcommand:

```bash
python -m agent_framework_evaluator evaluate \
  --env .env \
  --initializer eval/initializers/customer_support_init.py \
  --verbose
```

See [Using the agent evaluator](using-agent-evaluator.md) for the full testing workflow.

### Programmatic orchestration patterns

**Sequential pipeline — process a list of items:**

```python
host = AgentHost.from_env(".env")

tickets = load_tickets_from_database()
for ticket in tickets:
    result = host.run_agent(
        "customer_support",
        parameters={"ticket_text": ticket.text, "customer_tier": ticket.tier},
    )
    save_response(ticket.id, result.message)
```

**Fan-out — process items in parallel:**

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

host = AgentHost.from_env(".env")
executor = ThreadPoolExecutor(max_workers=4)

async def process_ticket(ticket):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        executor,
        lambda: host.run_agent(
            "customer_support",
            parameters={"ticket_text": ticket.text},
        )
    )

async def main():
    tickets = load_tickets_from_database()
    results = await asyncio.gather(*[process_ticket(t) for t in tickets])
    for ticket, result in zip(tickets, results):
        save_response(ticket.id, result.message)
```

Note: `AgentHost` is not thread-safe by default for mutable state.  Running multiple agents concurrently on the same host works when they are read-only (no shared mutable state between calls).  If you have mutable shared state (e.g. a conversation store), ensure it is thread-safe.

**Structured extraction pipeline:**

For extraction tasks where you want structured output from the agent, use terminal tools:

```yaml
# agents/extractor.md
---
id: extractor
role: Data extractor
description: Extracts structured fields from unstructured text.
parameters:
  raw_text:
    description: The unstructured text to extract from.
    required: true
    type: string
tools: []
subagents: []
skills: []
terminal_tools:
  - submit_extraction
---
You are a data extraction specialist.
Extract the requested fields from the provided text.
When you have extracted all fields, call the submit_extraction tool with the structured data.
If a field is not present in the text, use null for its value.
---
Extract name, email, phone, and company from this text:

{{raw_text}}
```

When `submit_extraction` is called as a terminal tool, `result.message` is the JSON of the extracted fields.  Parse it in your pipeline:

```python
import json

result = host.run_agent(
    "extractor",
    parameters={"raw_text": "Contact Alice Chen at alice@example.com, (555) 123-4567, Acme Corp"},
)
extracted = json.loads(result.message)
# {"name": "Alice Chen", "email": "alice@example.com", "phone": "(555) 123-4567", "company": "Acme Corp"}
```

### Commands (slash commands)

Commands are parametrised Markdown prompts in a configured directory.  They are useful for defining reusable instruction patterns that users or scripts can invoke by name:

```
COMMANDS_DIRECTORY=commands
```

**`commands/summarise.md`:**

```markdown
---
description: Summarise a document or text snippet.
argument-hint: <text to summarise>
allowed-tools: Read
---
Please provide a concise summary of the following:

$ARGUMENTS
```

Execute a command programmatically:

```python
result = await host.execute_command("summarise", "The Eiffel Tower was built in 1889...")
print(result)
```

`$ARGUMENTS` is replaced by the raw arguments string.  `$1`–`$9` are positional arguments if the input is space-separated.

---

## Quick reference

### CLI

```bash
# Interactive console
python -m agent_framework --console --env .env

# One-shot
python -m agent_framework --env .env --instruction "Your question here."

# Specific agent
python -m agent_framework --env .env --agent summariser --instruction "..."

# With tracing
python -m agent_framework --env .env \
  --runtime-trace-jsonl ./logs/run.jsonl \
  --llm-trace file \
  --instruction "..."

# Override model
python -m agent_framework --env .env --model gpt-4o --instruction "..."

# Evaluator UI
python -m agent_framework_evaluator web --env .env --open-browser
```

### Agent frontmatter keys

| Key | Required | Type | Default | Meaning |
|-----|----------|------|---------|---------|
| `id` | yes | string | — | Agent identifier |
| `role` | no | string | filename stem | Human-readable role |
| `description` | no | string | `""` | Shown to parent agents |
| `parameters` | no | dict | `{}` | Declared invocation contract |
| `tools` | no | list | `[]` | Allowed tool ids |
| `subagents` | no | list | `[]` | Allowed child agent ids |
| `skills` | no | list | `[]` | Allowed skill names |
| `terminal_tools` | no | list | `[]` | Tools that exit the loop immediately |

### Sidecar JSON keys

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `model` | string | `DEFAULT_MODEL` | Model(s), comma-separated fallbacks |
| `temperature` | float | `0.2` | Sampling temperature |
| `provider` | string | `DEFAULT_PROVIDER` | Provider name |
| `behaviors` | list | `[]` | Behavior ids to attach |
| `can_query_caller` | bool | `true` | Allow callbacks to caller agent |
| `can_use_host_interaction` | bool | `true` | Allow callbacks to host/user |

### Decision kinds

| Kind | Ends loop? | Required fields |
|------|-----------|----------------|
| `final_message` | Yes | `message` |
| `call_tool` | No | `tool_name`, `parameters` |
| `call_subagent` | No | `subagent_id`, `parameters` |
| `callback` | No | `intent`, `message` |
| `invoke_skill` | No | `skill_name` |

### Environment variables

| Variable | Meaning |
|----------|---------|
| `DEFAULT_PROVIDER` | `openai` or `dial` |
| `OPENAI_API_KEY` | API key for OpenAI |
| `DEFAULT_MODEL` | Comma-separated model list |
| `AGENT_MODELS` | Per-agent model overrides |
| `AGENT_DIRECTORY` | Path to agent `.md` files |
| `TOOLS_DIRECTORY` | Path to tool files |
| `WORLD_DIRECTORY` | Sandbox root for file tools |
| `ROOT_AGENT` | Default agent id |
| `SKILLS_DIRECTORY` | Path to skill directories |
| `SKILLS_CATALOG_MAX_TOKENS` | Max tokens for skills catalog |
| `MISSING_TOOL_POLICY` | `graceful` or `strict` |
| `MCP_ENABLED` | `true` or `false` |
| `MCP_CONFIG_PATH` | Explicit path to `.mcp.json` |
| `AGENT_EVAL_MODEL` | Model for the evaluator LLM |

---

## Further reading

- [Architecture overview](../architecture/overview.md)
- [Using Memory](./using-memory.md)
- [Using the agent evaluator](using-agent-evaluator.md)
- [Using DIAL](using-dial.md)
- [Memory System](../architecture/memory-system.md)
- [ADR: Model context and drivers](../architecture/adr-model-context-and-drivers.md)
- [Agent evaluator and web runtime](../architecture/agent-evaluator-web-runtime.md)
- [Tracing and evaluation](../architecture/tracing-evaluation.md)
