"""Supervisor: the Deep Agents agent that receives every doctor message (docs/agent-impl-plan.md task 3; agent-plan
§3.2, T90, T93). It plans (write_todos), calls the capabilities and writes the answer; every answer passes the guard.
"""

from pathlib import Path

from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, create_deep_agent, register_harness_profile
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRequest, TodoListMiddleware, dynamic_prompt
from langchain_core.language_models import BaseChatModel

from agent.analysis import analyze_findings
from agent.guard import AnswerGuardMiddleware
from agent.state import CONFIG, CaseState, render_case
from agent.tools import CODE_TOOLS
from core.llm import chat

PROMPTS = Path(__file__).with_name("prompts")
SUPERVISOR_PROMPT = (PROMPTS / "supervisor.md").read_text(encoding="utf-8")
TODOS_PROMPT = (PROMPTS / "todos.md").read_text(encoding="utf-8")
TOOLS = [analyze_findings, *CODE_TOOLS]
SUBAGENTS: list = []                      # none: explanations come from POST /cases/{id}/explanations (T81)

# No file tools (every tool result is bounded by list_limit) and no general-purpose subagent, hence no `task`.
# A pre-built ChatOpenAI resolves its profile by "<ls_provider>:<model name>" (T98 (4)).
register_harness_profile(f"openai:{CONFIG['supervisor_model']}", HarnessProfile(
    excluded_tools=frozenset({"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}),
    general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
))


@dynamic_prompt
def case_prompt(request: ModelRequest) -> str:
    """The case as it is now, appended to the system prompt on every model call (agent-plan §5)."""
    return request.system_message.text + "\n\n" + render_case(request.state)


def build_supervisor(model: BaseChatModel | None = None):
    # Thinking off for tool turns (T22); stream_usage so streamed calls still report tokens to Langfuse (T99).
    model = model or chat(CONFIG["supervisor_model"], thinking=False).model_copy(
        update={"stream_usage": True, "name": "supervisor"})
    return create_deep_agent(
        model=model, tools=TOOLS, subagents=SUBAGENTS, system_prompt=SUPERVISOR_PROMPT,
        middleware=[TodoListMiddleware(system_prompt=TODOS_PROMPT), case_prompt, AnswerGuardMiddleware(),
                    ModelCallLimitMiddleware(run_limit=CONFIG["max_model_calls"], exit_behavior="error")],
        state_schema=CaseState, name="supervisor")
