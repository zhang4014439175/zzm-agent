#!/usr/bin/env python3
"""Thin CLI entrypoint that re-exports the internal runtime helpers."""

from __future__ import annotations

import sys
from zzm_agent.cli_support.commands import handle_slash
from zzm_agent.cli_support.runtime import (
    build_registry,
    load_config,
    main,
    parse_args,
)
from zzm_agent.cli_support.rendering import build_console, render_reply, stream_reply_chunk


if __name__ == "__main__":
    sys.exit(main())
