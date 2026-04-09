import json
from typing import TYPE_CHECKING, Any

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

    def run(self, user_input: str) -> str:
        """
        Run a single turn of the conversation.
        
        This method will load history, call the LLM, execute any requested
        tools in a loop, and finally return the model's text response.
        
        Args:
            user_input: The message from the user.
            
        Returns:
            The final text response from the assistant.
        """
        # 1. Load context: System prompt + History + New input
        history = self.store.load_history()
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})

        # Track only the messages from this specific turn for persistence
        turn_messages = [{"role": "user", "content": user_input}]
        
        # Get available tool schemas
        tools = self.registry.get_schemas()

        # 2. Start the thinking-action loop
        while True:
            kwargs = {
                "model": self.model,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            # Call the model
            response = self.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            # If it's a simple text reply, we are done
            if not msg.tool_calls:
                final_reply = msg.content or ""
                assistant_msg = {"role": "assistant", "content": final_reply}
                messages.append(assistant_msg)
                turn_messages.append(assistant_msg)
                
                # Save the new messages from this turn to persistent memory
                self.store.append(turn_messages)
                return final_reply

            # If the model wants to call tools
            tool_calls_raw = []
            for tc in msg.tool_calls:
                tool_calls_raw.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })
            
            # Record the assistant's intent to call tools
            assistant_intent_msg = {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": tool_calls_raw,
            }
            messages.append(assistant_intent_msg)
            turn_messages.append(assistant_intent_msg)

            # Execute each tool call requested
            for tc in msg.tool_calls:
                try:
                    # Parse arguments and call the tool through the registry
                    args = json.loads(tc.function.arguments)
                    result = self.registry.call(tc.function.name, args)
                    result_str = str(result)
                except Exception as e:
                    # Capture tool execution errors and feed them back to the model
                    result_str = f"Error executing tool: {e}"

                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                }
                messages.append(tool_result_msg)
                turn_messages.append(tool_result_msg)
            
            # Continue the loop to let the model process tool results
