---
name: history
description: Event recording, milestone tagging, and history compression techniques.
---

# History & Event Compression Guide

## Guidelines

1. **Importance Scale**:
   - Level 1-3: Low-level mundane state shifts (birth/decay of minor cells).
   - Level 4-7: Significant structural formations, phase transitions, collective behaviors.
   - Level 8-10: Epochal milestones (first self-replicating cluster, extinction event, cosmic symmetry breaking).

2. **Compression Cycle**:
   - Store detailed event rows in `events` table with timestamps.
   - When event count exceeds thresholds (e.g. > 10,000 entries), compress minor events (levels 1-3) into statistical summaries:
     `"Epoch 42: 1,420 micro-collisions occurred, entropy stabilized at 0.84."`
   - Never compress or delete Level 8+ epochal milestones.
