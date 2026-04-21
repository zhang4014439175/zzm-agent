from __future__ import annotations

import difflib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from zzm_agent.constants import (
    ACTIVE_PROMPT_PATH,
    EVALUATIONS_PATH,
    EVOLUTION_CANDIDATES_PATH,
    PROMPT_HISTORY_PATH,
)

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

OPTIMIZATION_PROMPT = """
You are improving the system prompt for zzm-agent.

Current system prompt:
{current_prompt}

Recent conversation history:
{history}

Latest evaluation, if available:
{evaluation}

Return JSON only in this shape:
{{
  "candidate_prompt": "the full replacement system prompt, or an empty string if no change is justified",
  "rationale": "short reason for the candidate or why no candidate is needed"
}}

Only propose a candidate when it is specific, actionable, and preserves the agent's existing responsibilities.
"""


class EvolutionOptimizer:
    """
    Framework for self-evolution of the agent's system prompt.
    
    This module is responsible for analyzing conversation trajectories,
    generating improved system prompts, and applying them as runtime state.
    """

    def __init__(
        self,
        client: "OpenAI",
        model: str,
        config_path: str | Path,
        sample_size: int = 20,
        history_versions: int = 5,
    ):
        """
        Initialize the evolution optimizer.
        
        Args:
            client: An OpenAI client instance used for self-reflection.
            model: The model name to use for evaluations and optimizations.
            config_path: Path to the baseline 'config.yaml' file.
            sample_size: Number of recent messages to analyze during optimization.
            history_versions: Number of prior prompts to keep for rollback.
        """
        self.client = client
        self.model = model
        self.config_path = Path(config_path).expanduser().resolve()
        self.sample_size = sample_size
        self.history_versions = history_versions
        self.eval_path = self.config_path.parent / EVALUATIONS_PATH
        self.candidates_path = self.config_path.parent / EVOLUTION_CANDIDATES_PATH
        self.prompt_history_path = self.config_path.parent / PROMPT_HISTORY_PATH
        self.active_prompt_path = self.config_path.parent / ACTIVE_PROMPT_PATH

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

    def run(self, history: list[dict[str, Any]]) -> dict[str, Any] | None:
        """
        Generate and persist a prompt candidate without applying it.

        Args:
            history: The full conversation history used as optimization evidence.

        Returns:
            A persisted candidate record, or ``None`` when no candidate is justified.
        """
        current_prompt = self.get_current_prompt()
        if not current_prompt:
            return None

        evaluation = self.evaluate(history) if history else self.get_latest_evaluation()
        sample = history[-self.sample_size :] if history else []
        history_text = json.dumps(sample, ensure_ascii=False, indent=2)
        evaluation_text = json.dumps(evaluation or {}, ensure_ascii=False, indent=2)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You propose careful system prompt improvements.",
                    },
                    {
                        "role": "user",
                        "content": OPTIMIZATION_PROMPT.format(
                            current_prompt=current_prompt,
                            history=history_text,
                            evaluation=evaluation_text,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                return None

            payload = json.loads(content)
        except Exception as e:
            print(f"Error during optimization: {e}")
            return None

        candidate_prompt = str(payload.get("candidate_prompt", "")).strip()
        if not candidate_prompt or candidate_prompt == current_prompt.strip():
            return None

        candidate = {
            "id": self._make_record_id("candidate"),
            "status": "pending",
            "created_at": self._utc_now(),
            "current_prompt": current_prompt,
            "candidate_prompt": candidate_prompt,
            "rationale": str(payload.get("rationale", "")).strip(),
            "evaluation": evaluation,
        }
        self._save_candidate(candidate)
        return candidate

    def optimize(self, history: list[dict]) -> str:
        """
        Analyze conversation history and generate a potentially better system prompt.

        This compatibility wrapper now creates a pending candidate instead of
        mutating the config; callers must invoke ``apply_candidate`` explicitly.
        """
        candidate = self.run(history)
        if not candidate:
            return ""
        return str(candidate["candidate_prompt"])

    def diff(self, candidate_id: str | None = None) -> str:
        """
        Return a unified diff between the current prompt and a candidate prompt.

        Args:
            candidate_id: Optional candidate id. Defaults to the newest pending
                candidate.

        Returns:
            Unified diff text, or an empty string when no candidate exists.
        """
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            return ""

        current_prompt = self.get_current_prompt()
        candidate_prompt = str(candidate.get("candidate_prompt", ""))
        return "".join(
            difflib.unified_diff(
                current_prompt.splitlines(keepends=True),
                candidate_prompt.splitlines(keepends=True),
                fromfile="current system_prompt",
                tofile=f"candidate {candidate['id']}",
            )
        )

    def apply_candidate(self, candidate_id: str | None = None) -> dict[str, Any] | None:
        """
        Apply a stored prompt candidate and record rollback history.

        Args:
            candidate_id: Optional candidate id. Defaults to the newest pending
                candidate.

        Returns:
            The applied candidate record, or ``None`` if no candidate exists.
        """
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            return None

        current_prompt, current_source = self._get_current_prompt_record()
        candidate_prompt = str(candidate.get("candidate_prompt", "")).strip()
        if not candidate_prompt:
            return None

        self._append_prompt_history(
            {
                "id": self._make_record_id("prompt"),
                "saved_at": self._utc_now(),
                "prompt": current_prompt,
                "source": current_source,
                "source_candidate_id": candidate["id"],
                "rolled_back_at": None,
            }
        )
        self.apply(candidate_prompt)

        candidates = self._load_records(self.candidates_path)
        for item in candidates:
            if item.get("id") == candidate["id"]:
                item["status"] = "applied"
                item["applied_at"] = self._utc_now()
                candidate = item
                break
        self._write_records(self.candidates_path, candidates)
        return candidate

    def rollback(self) -> dict[str, Any] | None:
        """
        Restore the most recent unapplied prompt history entry.

        Returns:
            The history entry used for rollback, or ``None`` when no rollback
            point exists.
        """
        history = self.get_prompt_history()
        for entry in reversed(history):
            if entry.get("rolled_back_at"):
                continue
            prompt = str(entry.get("prompt", ""))
            if not prompt:
                continue
            self.apply(prompt)
            entry["rolled_back_at"] = self._utc_now()
            self._write_records(self.prompt_history_path, history)
            return entry
        return None

    def apply(self, new_prompt: str) -> None:
        """
        Apply the system prompt as runtime evolution state.

        The baseline config remains immutable during interactive evolution.
        Runtime startup reads this active prompt state first, then falls back to
        `config.yaml` when no evolved prompt has been applied.
        
        Args:
            new_prompt: The optimized system prompt to apply.
        """
        if not new_prompt:
            return

        try:
            if new_prompt.strip() == self.get_config_prompt().strip():
                self._clear_active_prompt()
            else:
                self._write_active_prompt(new_prompt)

        except Exception as e:
            # For this MVP, we log errors to stdout (should be replaced with proper logging).
            print(f"Error applying active prompt: {e}")

    def get_current_prompt(self) -> str:
        """Return the active prompt, falling back to the configured baseline."""
        prompt, _source = self._get_current_prompt_record()
        return prompt

    def _get_current_prompt_record(self) -> tuple[str, str]:
        """Return the current prompt and whether it came from active state or config."""
        active_prompt = self._read_active_prompt()
        if active_prompt:
            return active_prompt, "active"
        return self.get_config_prompt(), "config"

    def get_config_prompt(self) -> str:
        """Return the baseline system prompt from config.yaml."""
        try:
            cfg = self._read_config()
        except Exception:
            return ""
        return str(cfg.get("agent", {}).get("system_prompt", ""))

    def get_candidate(self, candidate_id: str | None = None) -> dict[str, Any] | None:
        """Return a candidate by id, or the newest pending candidate by default."""
        candidates = self._load_records(self.candidates_path)
        if candidate_id:
            for candidate in candidates:
                if candidate.get("id") == candidate_id:
                    return candidate
            return None

        for candidate in reversed(candidates):
            if candidate.get("status") == "pending":
                return candidate
        return None

    def get_prompt_history(self) -> list[dict[str, Any]]:
        """Return stored prompt history records in chronological order."""
        return self._load_records(self.prompt_history_path)

    def _read_active_prompt(self) -> str:
        """Read the runtime prompt override created by `/evolve apply`."""
        if not self.active_prompt_path.exists():
            return ""
        try:
            with open(self.active_prompt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return ""
        return str(data.get("prompt", "")).strip() if isinstance(data, dict) else ""

    def _write_active_prompt(self, prompt: str) -> None:
        """Persist the active prompt without mutating project configuration."""
        self.active_prompt_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "prompt": prompt,
            "updated_at": self._utc_now(),
        }
        with open(self.active_prompt_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _clear_active_prompt(self) -> None:
        """Remove active prompt state when rollback returns to the config baseline."""
        try:
            self.active_prompt_path.unlink()
        except FileNotFoundError:
            return

    def _read_config(self) -> dict[str, Any]:
        """Load config.yaml while preserving all unrelated fields."""
        import yaml

        if not self.config_path.exists():
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _save_candidate(self, candidate: dict[str, Any]) -> None:
        """Persist a candidate and supersede older pending candidates."""
        candidates = self._load_records(self.candidates_path)
        for item in candidates:
            if item.get("status") == "pending":
                item["status"] = "superseded"
                item["superseded_at"] = self._utc_now()
        candidates.append(candidate)
        self._write_records(self.candidates_path, candidates[-100:])

    def _append_prompt_history(self, entry: dict[str, Any]) -> None:
        """Save a rollback point while enforcing the configured retention limit."""
        history = self._load_records(self.prompt_history_path)
        if history and history[-1].get("prompt") == entry.get("prompt"):
            history[-1]["saved_at"] = entry["saved_at"]
            history[-1]["source_candidate_id"] = entry["source_candidate_id"]
            history[-1]["rolled_back_at"] = None
            history[-1]["source"] = entry.get("source", history[-1].get("source", "unknown"))
            self._write_records(self.prompt_history_path, history)
            return
        history.append(entry)
        limit = max(1, self.history_versions)
        self._write_records(self.prompt_history_path, history[-limit:])

    def _load_records(self, path: Path) -> list[dict[str, Any]]:
        """Read a JSON list file, treating missing or corrupt files as empty."""
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _write_records(self, path: Path, records: list[dict[str, Any]]) -> None:
        """Write a JSON list file under the evolution directory."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    def _make_record_id(self, prefix: str) -> str:
        """Create a readable timestamp-based record id for CLI display."""
        return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    def _utc_now(self) -> str:
        """Return an ISO-8601 UTC timestamp for evolution metadata."""
        return datetime.now(timezone.utc).isoformat()
