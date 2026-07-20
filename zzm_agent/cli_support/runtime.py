"""Backward-compatible facade for the split CLI runtime modules."""

from __future__ import annotations

import time
from types import SimpleNamespace
from zzm_agent.cli_support import bootstrap as _bootstrap
from zzm_agent.cli_support import execution as _execution
from zzm_agent.cli_support import repl as _repl
from zzm_agent.cli_support.bootstrap import *
from zzm_agent.cli_support.execution import *
from zzm_agent.cli_support.repl import *

build_prompt_session = _repl.build_prompt_session
read_repl_input = _repl.read_repl_input
prompt_for_model_config = _bootstrap.prompt_for_model_config
_build_working_footer = _repl._build_working_footer
_format_repl_exception = _repl._format_repl_exception
_format_repl_exception_with_runtime = _repl.format_runtime_exception
_build_exec_prompt = _execution._build_exec_prompt
_ask_tool_approval_choice = _bootstrap._ask_tool_approval_choice
_config_bool = _bootstrap._config_bool
_resolve_plugin_dirs = _bootstrap._resolve_plugin_dirs


def _start_working_status(console, *, runtime=None, reset_elapsed=True):
    return _repl._start_working_status(
        console,
        runtime=runtime,
        reset_elapsed=reset_elapsed,
        monotonic_fn=time.monotonic,
    )


def _stop_working_status(console, *, clear_status=False):
    return _repl._stop_working_status(console, clear_status=clear_status)


def ensure_model_credentials(cfg, args, *, stdin=None):
    """Preserve the legacy prompt monkeypatch point during migration."""
    original = _bootstrap.prompt_for_model_config
    _bootstrap.prompt_for_model_config = prompt_for_model_config
    try:
        return _bootstrap.ensure_model_credentials(cfg, args, stdin=stdin)
    finally:
        _bootstrap.prompt_for_model_config = original

def run_repl(runtime):
    """Forward legacy patched dependencies to the REPL implementation."""
    compatible_runtime = runtime
    if runtime.get("query_engine") is None and runtime.get("loop") is not None:
        loop = runtime["loop"]

        class _LegacyQueryEngine:
            def submit_message(self, prompt, *, stream=False, on_stream_event=None, **_):
                on_text_chunk = None
                if on_stream_event is not None:
                    on_text_chunk = lambda chunk: on_stream_event(
                        _repl.ModelStreamEvent.content_delta(chunk)
                    )
                reply = loop.run(prompt, stream=stream, on_text_chunk=on_text_chunk)
                return SimpleNamespace(reply=reply)

        compatible_runtime = dict(runtime)
        compatible_runtime["query_engine"] = _LegacyQueryEngine()
    return _repl.run_repl(
        compatible_runtime,
        build_prompt_session_fn=build_prompt_session,
        read_repl_input_fn=read_repl_input,
        monotonic_fn=time.monotonic,
    )

def main(argv=None):
    """Run the split CLI bootstrap entry point."""
    return _bootstrap.main(argv)
