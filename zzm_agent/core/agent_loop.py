import json
from typing import TYPE_CHECKING, Any, Callable

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
    ):
        """
        Initialize the AgentLoop.
        
        Args:
            client: An OpenAI client instance.
            model: The name of the model to use (e.g., 'gpt-4').
            system_prompt: The initial system instructions for the agent.
            registry: The tool registry containing available functions.
            store: The memory store for persisting history.
        """
        self.client = client
        self.model = model
        self.system_prompt = system_prompt
        self.registry = registry
        self.store = store

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
        # 1. Load context: System prompt + History + New input
        history = self.store.load_history()
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        # Persist only the current turn once it is safe to do so; this avoids
        # duplicating prior history that was already loaded from disk.
        turn_messages = [{"role": "user", "content": user_input}]
        
        # Get available tool schemas
        tools = self.registry.get_schemas()

        # 2. Start the thinking-action loop
        while True:
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
            # Record the assistant's intent to call tools
            assistant_intent_msg = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls_raw,
            }
            messages.append(assistant_intent_msg)
            turn_messages.append(assistant_intent_msg)

            # Tool results stay inside the same turn so the model can immediately
            # consume them on the next loop iteration.
            for tc in tool_calls_raw:
                try:
                    # Parse arguments and call the tool through the registry
                    args = json.loads(tc["function"]["arguments"])
                    result = self.registry.call(tc["function"]["name"], args)
                    result_str = str(result)
                except Exception as e:
                    # Capture tool execution errors and feed them back to the model
                    result_str = f"Error executing tool: {e}"

                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                }
                messages.append(tool_result_msg)
                turn_messages.append(tool_result_msg)
            
            # Continue the loop to let the model process tool results
