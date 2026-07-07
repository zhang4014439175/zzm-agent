#!/usr/bin/env python3
"""Thin CLI entrypoint that re-exports the internal runtime helpers."""

from __future__ import annotations

import sys

# Configure stdout and stderr to UTF-8 to prevent UnicodeEncodeError on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from zzm_agent.cli_support.runtime import (
    main,
)

if __name__ == "__main__":
    sys.exit(main())

