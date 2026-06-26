import yaml
from pathlib import Path

from zzm_agent.eval.runner import _run_replay


BENCHMARK_DIR = Path("zzm_agent/eval/benchmarks")


def _load_case(filename: str) -> dict:
    return yaml.safe_load((BENCHMARK_DIR / filename).read_text(encoding="utf-8"))


def test_replay_benchmark_checks_reflection_expectations():
    case = _load_case("07_reflection_repeated_observation.yaml")

    assert _run_replay(case, Path(".")) is True


def test_replay_benchmark_checks_tool_error_category_expectations():
    case = _load_case("08_error_category_recovery.yaml")

    assert _run_replay(case, Path(".")) is True


def test_replay_benchmark_checks_retry_after_expectations():
    case = _load_case("09_retry_after_external_service.yaml")

    assert _run_replay(case, Path(".")) is True


def test_replay_benchmark_fails_when_expected_reflection_is_missing():
    case = _load_case("08_error_category_recovery.yaml")
    case["expected"]["reflection_count"] = 1

    assert _run_replay(case, Path(".")) is False
