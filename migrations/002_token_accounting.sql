-- 002_token_accounting.sql — break token_usage out by call purpose (generation,
-- repair, summary) and record the input/output split the API actually returns,
-- instead of one opaque tokens_used total per org/day.
ALTER TABLE token_usage
    ADD COLUMN IF NOT EXISTS input_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS output_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'generation';

ALTER TABLE token_usage
    ADD CONSTRAINT token_usage_purpose_check CHECK (purpose IN ('generation', 'repair', 'summary'));

-- Grain moves from (org_id, day) to (org_id, day, purpose) so each purpose
-- accumulates independently; callers UPSERT with input_tokens/output_tokens
-- incrementing the existing row for (org_id, today, purpose).
ALTER TABLE token_usage DROP CONSTRAINT token_usage_pkey;
ALTER TABLE token_usage ADD PRIMARY KEY (org_id, day, purpose);

-- Speeds the budget check's SUM(...) WHERE org_id = $1 AND day = $2 across purposes.
CREATE INDEX IF NOT EXISTS token_usage_org_day ON token_usage (org_id, day);
