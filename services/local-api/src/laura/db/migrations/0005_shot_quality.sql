-- Smart rough-cut: per-shot quality metrics + keep decision (see analysis/quality.py).
-- Deterministic CPU image-math scores let `from-shots` drop weak shots (black, frozen,
-- near-duplicate, blurry). Frame-state invariants unchanged; these are derived metrics.
-- Portable ADD COLUMN with constant defaults (SQLite + PostgreSQL).
ALTER TABLE shots ADD COLUMN black_ratio REAL;
ALTER TABLE shots ADD COLUMN static_score REAL;
ALTER TABLE shots ADD COLUMN phash TEXT;
ALTER TABLE shots ADD COLUMN blur_score REAL;
ALTER TABLE shots ADD COLUMN keep INTEGER NOT NULL DEFAULT 1;
ALTER TABLE shots ADD COLUMN drop_reason TEXT;
