from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zzm_agent.constants import EVALUATIONS_PATH

if TYPE_CHECKING:
    from openai import OpenAI

EVALUATION_PROMPT = """
You are an expert AI evaluator. Your task is to evaluate the following conversation trajectory between a user and an AI agent.
Analyze how well the agent performed based on the provided history.

Provide your evaluation in the following JSON format:
{{
  "relevance_score": (1-10),
  "tool_usage_score": (1-10, or null if no tools used),
  "conciseness_score": (1-10),
  "reasoning": "A short explanation of your scoring",
  "conclusion": "A summary of the agent's performance"
}}

Conversation History:
{history}
"""


class EvolutionOptimizer:
    """
    Framework for self-evolution of the agent's system prompt.
    
    This module is responsible for analyzing conversation trajectories,
    generating improved system prompts, and applying them back to the configuration.
    """

    def __init__(self, client: "OpenAI", model: str, config_path: str | Path, sample_size: int = 20):
        """
        Initialize the evolution optimizer.
        
        Args:
            client: An OpenAI client instance used for self-reflection.
            model: The model name to use for evaluations and optimizations.
            config_path: Path to the 'config.yaml' file to be updated.
            sample_size: Number of recent messages to analyze during optimization.
        """
        self.client = client
        self.model = model
        self.config_path = Path(config_path).expanduser().resolve()
        self.sample_size = sample_size
        self.eval_path = self.config_path.parent / EVALUATIONS_PATH

    def evaluate(self, history: list[dict[str, Any]]) -> dict[str, Any] | None:
        """
        Perform a self-evaluation of a conversation trajectory.
        
        Args:
            history: The conversation history to evaluate.
            
        Returns:
            A dictionary containing the evaluation results, or None if evaluation fails.
        """
        if not history:
            return None

        # Truncate history to avoid token limits if necessary
        sample = history[-self.sample_size :]
        history_text = json.dumps(sample, ensure_ascii=False, indent=2)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": EVALUATION_PROMPT.format(history=history_text)},
                ],
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            if not content:
                return None
                
            eval_result = json.loads(content)
            eval_result["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            self._save_evaluation(eval_result)
            return eval_result
        except Exception as e:
            print(f"Error during evaluation: {e}")
            return None

    def _save_evaluation(self, eval_result: dict[str, Any]) -> None:
        """
        Append the evaluation result to the local storage.
        
        Args:
            eval_result: The evaluation record to save.
        """
        try:
            self.eval_path.parent.mkdir(parents=True, exist_ok=True)
            
            evals = []
            if self.eval_path.exists():
                with open(self.eval_path, "r", encoding="utf-8") as f:
                    try:
                        evals = json.load(f)
                    except json.JSONDecodeError:
                        evals = []
            
            evals.append(eval_result)
            
            # Keep only the last 100 evaluations to prevent file bloat
            evals = evals[-100:]
            
            with open(self.eval_path, "w", encoding="utf-8") as f:
                json.dump(evals, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving evaluation: {e}")

    def get_latest_evaluation(self) -> dict[str, Any] | None:
        """
        Retrieve the most recent evaluation record.
        
        Returns:
            The latest evaluation record, or None if none exist.
        """
        if not self.eval_path.exists():
            return None
            
        try:
            with open(self.eval_path, "r", encoding="utf-8") as f:
                evals = json.load(f)
                if evals:
                    return evals[-1]
        except Exception:
            pass
        return None

    def optimize(self, history: list[dict]) -> str:
        """
        Analyze conversation history and generate a potentially better system prompt.
        
        Currently a stub implementation that returns an empty string.
        
        Args:
            history: The full conversation history.
            
        Returns:
            A new suggested system prompt, or an empty string if no optimization is made.
        """
        # TODO: Implement trajectory analysis -> model self-reflection -> prompt generation
        return ""

    def apply(self, new_prompt: str) -> None:
        """
        Write the new system prompt back to the config.yaml file.
        
        Args:
            new_prompt: The optimized system prompt to apply.
        """
        if not new_prompt:
            return

        import yaml

        try:
            # Evolution only mutates the prompt field; keeping the rest of the
            # config intact avoids surprising model or memory setting changes.
            if not self.config_path.exists():
                return

            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)

            if "agent" not in cfg:
                cfg["agent"] = {}
            cfg["agent"]["system_prompt"] = new_prompt

            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)

        except Exception as e:
            # For this MVP, we log errors to stdout (should be replaced with proper logging)
            print(f"Error applying new prompt to config: {e}")
