# Memory Overview

This document summarizes how memory currently works in `zzm-agent`.

## Memory Types

### History

`history` is the raw conversation transcript for the current session.

- Stored per session in `sessions/<session-id>/history.json`
- Written after each completed turn
- Read back as the active session's working memory
- Limited by `max_history` when loading into the model context

Use it for:

- Current-session continuity
- Tool call history
- Recent user and assistant messages

### Episodic

`episodic` is a lightweight summary of one session.

- Stored per session in `sessions/<session-id>/episodic.json`
- Generated from that session's `history.json`
- Used as cross-session recall
- Excludes the active session when injecting memory into a new turn

Use it for:

- "What was decided in a previous session?"
- Carrying key conclusions across sessions

### Semantic

`semantic` is cross-session long-term fact memory.

- Stored globally in `semantic.json`
- Added explicitly by the user through `/remember`
- Removed by `/forget`
- Shared by all sessions

Use it for:

- Stable user preferences
- Project facts
- Long-lived constraints

## Persistence Timing

### When `history.json` is written

`history.json` is updated after a turn completes and messages are appended to the active session.

### When `episodic.json` is written

`episodic.json` is written in two cases:

1. After new messages are appended to the current session
2. Before switching away from the current session or creating a new active session

This means episodic memory is refreshed both during normal conversation and at session boundaries.

### When `semantic.json` is written

`semantic.json` is written only when the user explicitly changes long-term memory:

- `/remember <fact>`
- `/forget <keyword>`

## Model Injection Order

Before calling the model, memory is assembled in this order:

1. System prompt
2. Semantic memory
3. Episodic memory
4. Current session history
5. Current user input

This keeps stable facts and previous-session summaries visible without mixing them into the current session transcript.

## Retrieval Limits

Long-term memory injection is bounded by `memory.retrieval_top_k`.

- Semantic memory is truncated to that limit
- Episodic memory is truncated to that limit
- Current session history is still controlled separately by `max_history`

## `/remember` and `/forget`

### Current `/remember` format

`/remember` currently accepts only one argument: the fact text.

```text
/remember <fact>
```

Examples:

```text
/remember User prefers concise answers.
/remember Project language is Python.
```

Current behavior:

- No extra flags or metadata
- Duplicate facts are normalized and refreshed instead of duplicated
- Facts become available to future sessions through semantic memory injection

### Current `/forget` format

```text
/forget <keyword>
```

Examples:

```text
/forget concise
/forget Python
```

Current behavior:

- Removes semantic memory entries whose fact text contains the keyword
- Matches by normalized text, not by explicit id

## File Layout

```text
<memory-root>/
├── semantic.json
└── sessions/
    ├── index.json
    ├── last_session.txt
    └── <session-id>/
        ├── meta.json
        ├── history.json
        └── episodic.json
```

## Practical Summary

- `history` stores the raw transcript of the active session
- `episodic` stores short summaries of finished or ongoing sessions
- `semantic` stores explicit long-term facts across all sessions
- `episodic` is auto-generated
- `semantic` is user-managed
