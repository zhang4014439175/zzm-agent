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
