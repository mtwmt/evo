---
name: review
description: Standards and practices for code self-review, refactoring, and boundary adherence.
---

# Code Review & Refactoring Guide

## Critical Checklist

1. **Non-Blocking Execution**:
   - Ensure the universe tick loop in `main.py` yields control (e.g. `time.sleep(interval)` or `asyncio.sleep()`).
   - Do not create tight infinite busy-wait loops that peg CPU to 100%.

2. **Resource Boundary**:
   - Limit memory footprints by purging transient state and capping in-memory arrays.
   - Do not spawn child processes; the runtime sandbox forbids external execution.

3. **Database Integrity**:
   - Always commit transactions or use context managers (`with sqlite3.connect(...) as conn:`).
   - Enable WAL mode for SQLite (`PRAGMA journal_mode=WAL;`) to allow concurrent reads from the Observer bridge.

4. **Exception Handling**:
   - Wrap dynamic calculations in defensive try-except blocks so individual entity glitches do not crash the entire cosmos.
