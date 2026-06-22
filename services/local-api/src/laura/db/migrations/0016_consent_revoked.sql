-- Consent can be withdrawn: revoked_at (NULL = active). The ai.reenact gate
-- refuses any consent record whose revoked_at is set.
ALTER TABLE consent_records ADD COLUMN revoked_at TEXT;
