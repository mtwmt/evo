---
name: migration
description: Safe SQLite schema evolution, data preservation, and backward compatibility.
---

# SQLite Schema Migration Guide

## Rules of Evolution

1. **Never Drop Historical Tables Directly**:
   - Prefer `ALTER TABLE ... ADD COLUMN ...` with default values.
   - If restructuring is necessary, create `new_table`, copy data, and rename:
     ```sql
     CREATE TABLE new_entities (...);
     INSERT INTO new_entities SELECT ... FROM entities;
     DROP TABLE entities;
     ALTER TABLE new_entities RENAME TO entities;
     ```

2. **Idempotent Migration Scripts**:
   - Check if columns or tables exist (`CREATE TABLE IF NOT EXISTS`, `PRAGMA table_info(...)`) before altering.
   - Evolution should run seamlessly even if restarted midway.

3. **Data Loss Prevention**:
   - The Evo outer governor takes automatic snapshots of `habitat.db` before candidate deployment.
   - However, within the universe, preserve the continuity of historical records wherever possible.
