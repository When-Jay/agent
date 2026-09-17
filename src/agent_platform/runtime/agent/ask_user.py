"""AskUserMiddleware: interactive question-answering during agent execution.

Adapted from the upstream LangChain ecosystem implementation
(``deepagents_code.ask_user.AskUserMiddleware``, langchain-ai/deepagents):
that module ships inside the deepagents-code CLI distribution, which is not
usable as a server-side dependency, so the minimal interrupt/answer protocol
is vendored here with the same public interface and payload contract:

* The middleware contributes an ``ask_user`` tool; calling it raises a
  LangGraph ``interrupt`` carrying an ``AskUserRequest`` payload.
* The platform run parks in WAITING_FOR_HUMAN (adapter `_finish_or_pause`)
  and the user answers through POST /runs/{id}/respond; the response is
  folded back via ``Command(resume=...)`` (budget-steering-spec.md section 28).
* A resume payload is ``{"status": "answered"|"cancelled"|"error",
  "answers": [str, ...]}``; answers align with the asked questions and are
  rendered back to the model as a ToolMessage (Q/A pairs).

CLI-specific authorization receipts and turn-id plumbing are intentionally
not ported (platform Run/Session identity covers that role).
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain.tools import InjectedToolCallId, ToolRuntime
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt
from pydantic import Field
from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


ASK_USER_TOOL_DESCRIPTION = """Ask the user one or more questions when you need clarification or input before proceeding.
Each question can be either:
- "text": Free-form text response from the user
- "multiple_choice": User selects from predefined options (an "Other" option is always available)
For multiple choice questions, provide a list of choices. The user can pick one or type a custom answer via the "Other" option.
By default all questions are required. Set "required" to false for optional questions that the user can skip. Do not include "(required)", "(optional)", "- optional", or similar annotations in the question text — the UI renders that separately based on the "required" field.
Use this tool when:
- You need clarification on ambiguous requirements
- You want the user to choose between multiple valid approaches
- You need specific information only the user can provide
- You want to confirm a plan before executing it
Do NOT use this tool for:
- Simple yes/no confirmations (just proceed with your best judgment)
- Questions you can answer yourself from context
- Trivial decisions that don't meaningfully affect the outcome"""  # noqa: E501

ASK_USER_SYSTEM_PROMPT = """## `ask_user`
You have access to the `ask_user` tool to ask the user questions when you need clarification or input.
Use this tool sparingly - only when you genuinely need information from the user that you cannot determine from context.
When using `ask_user`:
- Be concise and specific with your questions
- Use multiple choice when there are clear options to choose from
- Use text input when you need free-form responses
- Group related questions into a single ask_user call rather than making multiple calls
- Never ask questions you can answer yourself from the available context"""  # noqa: E501


class Question(TypedDict, total=False):
    """One question presented to the user (upstream payload contract)."""

    question: str
    type: str  # "text" | "multiple_choice"
    choices: list[str]
    required: bool


class AskUserRequest(TypedDict):
    """Interrupt payload surfaced through the APPROVAL_REQUIRED event."""

    type: str  # always "ask_user"
    questions: list[Question]
    tool_call_id: str


def _validate_questions(questions: list[Question]) -> None:
    """Validate ask_user question structure before interrupting."""
    if not questions:
        msg = "ask_user requires at least one question"
        raise ValueError(msg)
    for q in questions:
        question_text = q.get("question")
        if not isinstance(question_text, str) or not question_text.strip():
            msg = "ask_user questions must have non-empty 'question' text"
            raise ValueError(msg)
        question_type = q.get("type")
        if question_type not in {"text", "multiple_choice"}:
            msg = f"unsupported ask_user question type: {question_type!r}"
            raise ValueError(msg)
        if question_type == "multiple_choice" and not q.get("choices"):
            msg = (
                f"multiple_choice question {q.get('question')!r} requires a "
                f"non-empty 'choices' list"
            )
            raise ValueError(msg)
        if question_type == "text" and q.get("choices"):
            msg = f"text question {q.get('question')!r} must not define 'choices'"
            raise ValueError(msg)


def _parse_answers(
    response: object,
    questions: list[Question],
    tool_call_id: str,
) -> Command[Any]:
    """Parse an interrupt resume payload into a Command with a ToolMessage.

    Malformed payloads become explicit error answers instead of silently
    defaulting to "(no answer)" (same contract as upstream).
    """
    status: str = "answered"
    error_text: str | None = None
    answers: list[str]
    if not isinstance(response, dict):
        logger.error(
            "ask_user received malformed resume payload (expected dict, got %s)",
            type(response).__name__,
        )
        answers = []
        status = "error"
        error_text = "invalid ask_user response payload"
    else:
        response_status = response.get("status")
        if isinstance(response_status, str):
            status = response_status
        if "answers" not in response:
            if status == "answered":
                logger.error("ask_user resume payload without 'answers'")
                answers = []
                status = "error"
                error_text = "missing ask_user answers payload"
            else:
                answers = []
        else:
            raw_answers = response["answers"]
            if isinstance(raw_answers, list):
                answers = [str(answer) for answer in raw_answers]
            else:
                logger.error(
                    "ask_user received non-list 'answers' payload (%s)",
                    type(raw_answers).__name__,
                )
                answers = []
                status = "error"
                error_text = "invalid ask_user answers payload"
        if status == "error":
            response_error = response.get("error")
            if isinstance(response_error, str) and response_error:
                error_text = response_error
        elif status == "cancelled":
            answers = ["(cancelled)" for _ in questions]
        elif status == "answered":
            if len(answers) != len(questions):
                logger.warning(
                    "ask_user answer count mismatch: expected %d, got %d",
                    len(questions),
                    len(answers),
                )
        else:
            logger.error("ask_user received unknown status %r", status)
            answers = []
            status = "error"
            error_text = "invalid ask_user response status"

    if status == "error":
        detail = error_text or "ask_user interaction failed"
        answers = [f"(error: {detail})" for _ in questions]

    formatted = []
    for i, question in enumerate(questions):
        answer = answers[i] if i < len(answers) else "(no answer)"
        formatted.append(f"Q: {question['question']}\nA: {answer}")
    return Command(
        update={
            "messages": [
                ToolMessage(
                    "\n\n".join(formatted),
                    name="ask_user",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


class AskUserMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Provides an ``ask_user`` tool that pauses the run on a LangGraph
    interrupt until the user answers (budget-steering-spec.md sections 27/28).
    """

    def __init__(
        self,
        *,
        system_prompt: str = ASK_USER_SYSTEM_PROMPT,
        tool_description: str = ASK_USER_TOOL_DESCRIPTION,
    ) -> None:
        super().__init__()
        self.system_prompt = system_prompt
        self.tool_description = tool_description

        @tool(description=self.tool_description)
        def _ask_user(
            questions: Annotated[
                list[Question],
                Field(description="Questions to present to the user."),
            ],
            tool_call_id: Annotated[str, InjectedToolCallId],
            runtime: ToolRuntime[Any, Any],
        ) -> Command[Any]:
            """Ask the user one or more questions."""
            _validate_questions(questions)
            ask_request = AskUserRequest(
                type="ask_user",
                questions=questions,
                tool_call_id=tool_call_id,
            )
            # interrupt() raises GraphBubbleUp from inside tool execution;
            # wrap_tool_call middleware must never swallow it (upstream note:
            # broad `except Exception` handlers break ask_user).
            response = interrupt(ask_request)
            return _parse_answers(response, questions, tool_call_id)

        _ask_user.name = "ask_user"
        self.tools = [_ask_user]

    def _with_system_prompt(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        if request.system_message is not None:
            blocks = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
            system_message = SystemMessage(content=blocks)  # type: ignore[arg-type]
        else:
            system_message = SystemMessage(content=self.system_prompt)
        return request.override(system_message=system_message)

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT] | AIMessage:
        return handler(self._with_system_prompt(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT] | AIMessage:
        return await handler(self._with_system_prompt(request))
