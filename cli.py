#!/usr/bin/env python3
"""Thin CLI entrypoint that re-exports the internal runtime helpers."""

from __future__ import annotations

import sys

from zzm_agent.cli_support.runtime import (
    main,
)

if __name__ == "__main__":
    sys.exit(main())
