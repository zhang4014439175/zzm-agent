from zzm_agent.core.progress_monitor import ProgressMonitor, ToolObservation


def observation(
    name: str,
    arguments: str,
    content: str,
    *,
    success: bool = True,
    retryable: bool = False,
) -> ToolObservation:
    return ToolObservation(
        tool_name=name,
        arguments=arguments,
        content=content,
        success=success,
        retryable=retryable,
    )


def test_detects_repeated_observation_when_arguments_change():
    monitor = ProgressMonitor(repeated_observation_limit=2)

    assert monitor.observe_round(
        [observation("search", '{"query":"alpha"}', "no matches")]
    ) is None
    signal = monitor.observe_round(
        [observation("search", '{"query":"beta"}', "no matches")]
    )

    assert signal is not None
    assert signal.reason == "repeated_observation"
    assert signal.round_count == 2


def test_detects_consecutive_non_retryable_failures():
    monitor = ProgressMonitor(non_retryable_failure_limit=2)

    assert monitor.observe_round(
        [
            observation(
                "fetch",
                '{"url":"a"}',
                '{"error_type":"PermissionError"}',
                success=False,
                retryable=False,
            )
        ]
    ) is None
    signal = monitor.observe_round(
        [
            observation(
                "fetch",
                '{"url":"b"}',
                '{"error_type":"PermissionError"}',
                success=False,
                retryable=False,
            )
        ]
    )

    assert signal is not None
    assert signal.reason == "consecutive_non_retryable_failures"


def test_detects_repeating_two_round_cycle():
    monitor = ProgressMonitor(cycle_repetition_limit=2)

    rounds = [
        observation("search", '{"query":"a"}', "result-a"),
        observation("read", '{"path":"b"}', "result-b"),
        observation("search", '{"query":"a"}', "result-a"),
        observation("read", '{"path":"b"}', "result-b"),
    ]

    signals = [monitor.observe_round([item]) for item in rounds]

    assert signals[-1] is not None
    assert signals[-1].reason == "repeating_tool_cycle"


def test_new_observation_resets_repeated_result_streak():
    monitor = ProgressMonitor(repeated_observation_limit=2)

    assert monitor.observe_round(
        [observation("search", '{"query":"a"}', "no matches")]
    ) is None
    assert monitor.observe_round(
        [observation("search", '{"query":"b"}', "one match")]
    ) is None
    assert monitor.observe_round(
        [observation("search", '{"query":"c"}', "no matches")]
    ) is None


def test_retryable_failure_does_not_count_as_non_retryable_stall():
    monitor = ProgressMonitor(
        non_retryable_failure_limit=2,
        repeated_observation_limit=3,
    )

    signal = None
    for _ in range(2):
        signal = monitor.observe_round(
            [
                observation(
                    "fetch",
                    "{}",
                    '{"error_type":"CommandTimeoutError"}',
                    success=False,
                    retryable=True,
                )
            ]
        )

    assert signal is None
