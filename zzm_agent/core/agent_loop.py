import json
from typing import TYPE_CHECKING, Any, Callable

from zzm_agent.core.errors import ToolError, tool_error_from_exception

if TYPE_CHECKING:
    from openai import OpenAI
    from zzm_agent.core.tool_registry import ToolRegistry
    from zzm_agent.memory.store import MemoryStore


class AgentLoop:
    """
    The main execution loop for the agent.
    
    It manages conversation history, handles tool calling loops, and
    interacts with the OpenAI-compatible API.
    """

    def __init__(
        self,
        client: "OpenAI",
        model: str,
        system_prompt: str,
        registry: "ToolRegistry",
        store: "MemoryStore",
        memory_injection_limit: int = 3,
        temperature: float | None = None,
        max_tokens: int | None = None,
        auto_approve: bool = False,
        safe_mode: bool = False,
        confirm_tool: Callable[[str, dict[str, Any], str], bool] | None = None,
        max_tool_iterations: int = 20,
        duplicate_tool_call_limit: int = 3,
        max_tool_retries: int = 1,
    ):
        """
        Initialize the AgentLoop.
        
        Args:
            client: An OpenAI client instance.
            model: The name of the model to use (e.g., 'gpt-4').
            system_prompt: The initial system instructions for the agent.
            registry: The tool registry containing available functions.
            store: The memory store for persisting history.
            max_tool_iterations: Maximum model tool-call rounds before stopping.
            duplicate_tool_call_limit: Consecutive identical calls before stopping.
            max_tool_retries: Automatic retries for retryable tool execution errors.
        """
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.registry = registry
        self.store = store
        self.memory_injection_limit = memory_injection_limit
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.auto_approve = auto_approve
        self.safe_mode = safe_mode
        self.confirm_tool = confirm_tool
        self.max_tool_iterations = max(1, max_tool_iterations)
        self.duplicate_tool_call_limit = max(1, duplicate_tool_call_limit)
        self.max_tool_retries = max(0, max_tool_retries)

    def _build_tool_call_record(
        self,
        tool_call_id: str,
        name: str,
        arguments: str,
    ) -> dict[str, Any]:
        return {
            "id": tool_call_id,
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            },
        }

    def _extract_tool_calls(self, tool_calls: list[Any]) -> list[dict[str, Any]]:
        # Non-stream responses already carry complete tool calls, so this path
        # just normalizes SDK objects into the persisted assistant message shape.
        records = []
        for tc in tool_calls:
            records.append(
                self._build_tool_call_record(
                    tool_call_id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
            )
        return records

    def _complete_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], bool]:
        kwargs = {
            "model": self.model,
            "messages": messages,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        return msg.content or "", self._extract_tool_calls(msg.tool_calls or []), False

    def _stream_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text_chunk: Callable[[str], None] | None,
    ) -> tuple[str, list[dict[str, Any]], bool]:
        kwargs = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        text_parts: list[str] = []
        # Streamed tool calls are incremental: the model can emit the same call
        # over multiple chunks, so we rebuild each call by its stable index.
        tool_call_map: dict[int, dict[str, Any]] = {}

        try:
            response = self.client.chat.completions.create(**kwargs)
            for chunk in response:
                # Ignore keep-alive/empty chunks that carry no choice payload.
                if not getattr(chunk, "choices", None):
                    continue

                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                # Only delta chunks contain incremental streamed content.
                if delta is None:
                    continue

                content = getattr(delta, "content", None)
                if content:
                    # Preserve the full assistant reply locally and optionally
                    # push each fragment to the caller for real-time rendering.
                    text_parts.append(content)
                    if on_text_chunk is not None:
                        on_text_chunk(content)

                for tc_delta in getattr(delta, "tool_calls", None) or []:
                    index = getattr(tc_delta, "index", 0)
                    # Create the accumulator the first time this tool-call slot appears.
                    record = tool_call_map.setdefault(
                        index,
                        self._build_tool_call_record("", "", ""),
                    )

                    if getattr(tc_delta, "id", None):
                        # The id may show up after earlier fragments, so keep refreshing it.
                        record["id"] = tc_delta.id

                    function = getattr(tc_delta, "function", None)
                    if function is None:
                        continue

                    # Function metadata can arrive piece by piece; concatenate the
                    # fragments until we have the full callable name and JSON args.
                    if getattr(function, "name", None):
                        record["function"]["name"] += function.name
                    if getattr(function, "arguments", None):
                        record["function"]["arguments"] += function.arguments
        except (KeyboardInterrupt, GeneratorExit):
            # Let the caller keep any text already shown to the user, but signal
            # that this turn was interrupted so partial tool state is discarded.
            return "".join(text_parts), [], True
        except Exception:
            if text_parts:
                # If the transport dies mid-stream after visible output, treat it
                # like an interrupted response instead of raising after partial render.
                return "".join(text_parts), [], True
            raise

        # Tool calls must be replayed in their original order for downstream execution.
        tool_calls = [tool_call_map[i] for i in sorted(tool_call_map)]
        return "".join(text_parts), tool_calls, False

    def _requires_tool_confirmation(self, risk_level: str) -> bool:
        """Return whether a tool call needs interactive approval."""
        if self.auto_approve:
            return False
        if risk_level == "high":
            return True
        if risk_level == "medium":
            return self.safe_mode
        return False

    def _is_tool_execution_approved(self, name: str, arguments: dict[str, Any]) -> bool:
        """Check whether a requested tool call is allowed to execute."""
        risk_level = self.registry.get_tool_meta(name).get("risk_level", "low")
        if not self._requires_tool_confirmation(risk_level):
            return True
        if self.confirm_tool is None:
            return False
        return bool(self.confirm_tool(name, arguments, risk_level))

    def _tool_call_signature(self, tool_call: dict[str, Any]) -> tuple[str, str]:
        """Return a stable signature used to detect repeated tool calls."""
        function = tool_call.get("function", {})
        name = str(function.get("name", ""))
        arguments = str(function.get("arguments", ""))
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            normalized_args = arguments
        else:
            normalized_args = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
        return name, normalized_args

    def _repetition_stop_message(self, signature: tuple[str, str]) -> str:
        """Build the final response when the model repeats the same tool blindly."""
        name, arguments = signature
        return (
            "Stopped tool execution because the model repeatedly requested the "
            f"same tool call: {name}({arguments}). Please adjust the approach "
            "or provide more specific input."
        )

    def _iteration_stop_message(self) -> str:
        """Build the final response when the tool loop reaches its safety limit."""
        return (
            "Stopped tool execution because the maximum tool iteration limit "
            f"({self.max_tool_iterations}) was reached. Please narrow the task "
            "or continue with a more specific instruction."
        )

    def _format_retried_error(self, error: ToolError, attempts: int) -> str:
        """Annotate the final retryable error with retry context."""
        if attempts <= 1:
            return error.to_json()
        retries = attempts - 1
        error.recovery_hint = (
            f"{error.recovery_hint} Automatic retry exhausted after "
            f"{retries} retry attempt(s)."
        )
        return error.to_json()

    def _execute_tool_with_retries(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool with bounded retries for structured retryable errors."""
        attempts = 0
        last_error: ToolError | None = None
        max_attempts = self.max_tool_retries + 1

        while attempts < max_attempts:
            attempts += 1
            try:
                return str(self.registry.call(name, args))
            except Exception as exc:
                last_error = tool_error_from_exception(exc)
                if not last_error.retryable or attempts >= max_attempts:
                    return self._format_retried_error(last_error, attempts)

        # This is defensive; the loop always returns on success or final error.
        if last_error is None:
            return ToolError(
                error_type="ToolExecutionError",
                message="Tool execution failed without an exception payload.",
                recovery_hint="Inspect tool execution logs before retrying.",
                retryable=False,
            ).to_json()
        return self._format_retried_error(last_error, attempts)

    def run(
        self,
        user_input: str,
        stream: bool = True,
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """
        Run a single turn of the conversation.
        
        This method will load history, call the LLM, execute any requested
        tools in a loop, and finally return the model's text response.
        
        Args:
            user_input: The message from the user.
            stream: Whether to request a streaming response from the model.
            on_text_chunk: Optional callback invoked for every streamed text chunk.
            
        Returns:
            The final text response from the assistant.
        """
        # 1. Load runtime context: base instructions + long-term memory +
        # compressed session history + current user input.
        messages, _compression = self.store.build_turn_messages(
            system_prompt=self.system_prompt,
            user_input=user_input,
            memory_limit=self.memory_injection_limit,
        )

        # Persist only the current turn once it is safe to do so; this avoids
        # duplicating prior history that was already loaded from disk.
        turn_messages = [{"role": "user", "content": user_input}]
        
        # Get available tool schemas
        tools = self.registry.get_schemas()

        # 2. Start the thinking-action loop
        # print(messages)
        tool_iterations = 0
        consecutive_signature: tuple[str, str] | None = None
        consecutive_count = 0
        while True:
            if tool_iterations >= self.max_tool_iterations:
                final_reply = self._iteration_stop_message()
                assistant_msg = {"role": "assistant", "content": final_reply}
                messages.append(assistant_msg)
                turn_messages.append(assistant_msg)
                self.store.append(turn_messages)
                return final_reply

            if stream:
                assistant_content, tool_calls_raw, interrupted = self._stream_once(
                    messages=messages,
                    tools=tools,
                    on_text_chunk=on_text_chunk,
                )
            else:
                assistant_content, tool_calls_raw, interrupted = self._complete_once(
                    messages=messages,
                    tools=tools,
                )

            if interrupted:
                # Interrupted turns intentionally do not write partial assistant
                # or tool state, preserving a resumable conversation history.
                return assistant_content

            # If it's a simple text reply, we are done
            if not tool_calls_raw:
                final_reply = assistant_content
                assistant_msg = {"role": "assistant", "content": final_reply}
                messages.append(assistant_msg)
                turn_messages.append(assistant_msg)

                # Only the final assistant reply marks the turn as complete.
                self.store.append(turn_messages)
                return final_reply

            # If the model wants to call tools
            current_signatures = [self._tool_call_signature(tc) for tc in tool_calls_raw]
            if len(current_signatures) == 1 and current_signatures[0] == consecutive_signature:
                consecutive_count += 1
            elif len(current_signatures) == 1:
                consecutive_signature = current_signatures[0]
                consecutive_count = 1
            else:
                consecutive_signature = None
                consecutive_count = 0

            if (
                consecutive_signature is not None
                and consecutive_count >= self.duplicate_tool_call_limit
            ):
                final_reply = self._repetition_stop_message(consecutive_signature)
                assistant_msg = {"role": "assistant", "content": final_reply}
                messages.append(assistant_msg)
                turn_messages.append(assistant_msg)
                self.store.append(turn_messages)
                return final_reply

            tool_iterations += 1

            # Record the assistant's intent to call tools
            assistant_intent_msg = {
                "role": "assistant",
                "content": assistant_content or None,  # Use None if empty for tool calls
                "tool_calls": tool_calls_raw,
            }
            messages.append(assistant_intent_msg)
            turn_messages.append(assistant_intent_msg)

            # Tool results stay inside the same turn so the model can immediately
            # consume them on the next loop iteration.
            for tc in tool_calls_raw:

                try:
                    # Parse arguments and call the tool through the registry
                    name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    if not self._is_tool_execution_approved(name, args):
                        result_str = "User denied tool execution."
                    else:
                        result_str = self._execute_tool_with_retries(name, args)
                except Exception as e:
                    # Capture tool execution errors and feed them back to the model
                    result_str = tool_error_from_exception(e).to_json()

                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                }
                messages.append(tool_result_msg)
                turn_messages.append(tool_result_msg)
            
            # Continue the loop to let the model process tool results
