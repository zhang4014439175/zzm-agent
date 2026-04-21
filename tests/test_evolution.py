import json
from unittest.mock import MagicMock

from zzm_agent.evolution.optimizer import EvolutionOptimizer

def test_evaluate_generates_and_saves_record(tmp_path):
    # Mock OpenAI client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "relevance_score": 9,
        "tool_usage_score": 8,
        "conciseness_score": 7,
        "reasoning": "Good performance.",
        "conclusion": "Excellent."
    })
    mock_client.chat.completions.create.return_value = mock_response

    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent:\n  system_prompt: 'test'\n", encoding="utf-8")
    
    optimizer = EvolutionOptimizer(client=mock_client, model="gpt-4", config_path=config_path)
    
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    result = optimizer.evaluate(history)
    
    assert result["relevance_score"] == 9
    assert result["conciseness_score"] == 7
    assert "timestamp" in result
    
    # Verify file persistence
    assert optimizer.eval_path.exists()
    latest = optimizer.get_latest_evaluation()
    assert latest["relevance_score"] == 9

def test_get_latest_evaluation_returns_none_when_no_file(tmp_path):
    mock_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    optimizer = EvolutionOptimizer(client=mock_client, model="gpt-4", config_path=config_path)
    
    assert optimizer.get_latest_evaluation() is None


def test_run_generates_pending_candidate_without_applying(tmp_path):
    mock_client = MagicMock()
    eval_response = MagicMock()
    eval_response.choices[0].message.content = json.dumps({
        "relevance_score": 6,
        "tool_usage_score": None,
        "conciseness_score": 5,
        "reasoning": "Needs clearer boundaries.",
        "conclusion": "Improve the prompt."
    })
    candidate_response = MagicMock()
    candidate_response.choices[0].message.content = json.dumps({
        "candidate_prompt": "You are zzm-agent. Be concise and state tool limits.",
        "rationale": "Adds explicit operating guidance."
    })
    mock_client.chat.completions.create.side_effect = [eval_response, candidate_response]

    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent:\n  system_prompt: old prompt\n", encoding="utf-8")

    optimizer = EvolutionOptimizer(client=mock_client, model="gpt-4", config_path=config_path)
    candidate = optimizer.run([{"role": "user", "content": "please help"}])

    assert candidate is not None
    assert candidate["status"] == "pending"
    assert candidate["candidate_prompt"] == "You are zzm-agent. Be concise and state tool limits."
    assert optimizer.get_current_prompt() == "old prompt"
    assert optimizer.get_candidate()["id"] == candidate["id"]


def test_diff_apply_and_rollback_prompt_candidate(tmp_path):
    mock_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent:\n  system_prompt: old prompt\n", encoding="utf-8")
    optimizer = EvolutionOptimizer(
        client=mock_client,
        model="gpt-4",
        config_path=config_path,
        history_versions=2,
    )
    optimizer._save_candidate({
        "id": "candidate-1",
        "status": "pending",
        "created_at": "now",
        "current_prompt": "old prompt",
        "candidate_prompt": "new prompt",
        "rationale": "test",
        "evaluation": None,
    })

    diff = optimizer.diff()
    assert "--- current system_prompt" in diff
    assert "+++ candidate candidate-1" in diff
    assert "-old prompt" in diff
    assert "+new prompt" in diff

    applied = optimizer.apply_candidate()
    assert applied["status"] == "applied"
    assert optimizer.get_current_prompt() == "new prompt"
    assert optimizer.get_config_prompt() == "old prompt"
    assert "new prompt" not in config_path.read_text(encoding="utf-8")
    assert optimizer.get_prompt_history()[0]["prompt"] == "old prompt"
    assert optimizer.get_prompt_history()[0]["source"] == "config"

    restored = optimizer.rollback()
    assert restored["prompt"] == "old prompt"
    assert optimizer.get_current_prompt() == "old prompt"
    assert optimizer.get_config_prompt() == "old prompt"
    assert not optimizer.active_prompt_path.exists()
    assert optimizer.rollback() is None


def test_apply_writes_active_prompt_state_not_config(tmp_path):
    mock_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    original_config = "agent:\n  system_prompt: baseline prompt\n"
    config_path.write_text(original_config, encoding="utf-8")
    optimizer = EvolutionOptimizer(client=mock_client, model="gpt-4", config_path=config_path)

    optimizer.apply("runtime prompt")

    assert config_path.read_text(encoding="utf-8") == original_config
    assert optimizer.get_current_prompt() == "runtime prompt"
    assert optimizer.active_prompt_path.exists()


def test_second_apply_records_previous_active_prompt(tmp_path):
    mock_client = MagicMock()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent:\n  system_prompt: baseline prompt\n", encoding="utf-8")
    optimizer = EvolutionOptimizer(client=mock_client, model="gpt-4", config_path=config_path)
    optimizer._save_candidate({
        "id": "candidate-1",
        "status": "pending",
        "created_at": "now",
        "current_prompt": "baseline prompt",
        "candidate_prompt": "first active prompt",
        "rationale": "test",
        "evaluation": None,
    })
    optimizer.apply_candidate()
    optimizer._save_candidate({
        "id": "candidate-2",
        "status": "pending",
        "created_at": "later",
        "current_prompt": "first active prompt",
        "candidate_prompt": "second active prompt",
        "rationale": "test",
        "evaluation": None,
    })

    optimizer.apply_candidate()

    history = optimizer.get_prompt_history()
    assert [entry["prompt"] for entry in history] == [
        "baseline prompt",
        "first active prompt",
    ]
    assert [entry["source"] for entry in history] == ["config", "active"]
