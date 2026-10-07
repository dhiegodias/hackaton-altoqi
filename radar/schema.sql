CREATE TABLE IF NOT EXISTS leads (
    id uuid PRIMARY KEY,
    name text NOT NULL DEFAULT '',
    email text NOT NULL DEFAULT '',
    company text NOT NULL DEFAULT '',
    hubspot_id text UNIQUE,
    data jsonb NOT NULL DEFAULT '{}',
    baseline jsonb NOT NULL DEFAULT '{}',
    crm_baseline jsonb NOT NULL DEFAULT '{}',
    qualification jsonb NOT NULL DEFAULT '{}',
    origin text NOT NULL,
    demo boolean NOT NULL DEFAULT false,
    suppressed boolean NOT NULL DEFAULT false,
    last_scanned_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS leads_email_unique ON leads(lower(email)) WHERE email <> '';
CREATE TABLE IF NOT EXISTS suggestions (
    id uuid PRIMARY KEY,
    lead_id uuid NOT NULL REFERENCES leads(id),
    field text NOT NULL,
    value jsonb NOT NULL,
    previous_value jsonb,
    source_kind text NOT NULL,
    source_url text NOT NULL DEFAULT '',
    evidence text NOT NULL,
    confidence integer NOT NULL CHECK(confidence BETWEEN 0 AND 100),
    observed_at timestamptz NOT NULL DEFAULT now(),
    fingerprint text NOT NULL,
    basis_fingerprint text,
    status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','superseded')),
    reviewer text,
    reviewed_at timestamptz,
    UNIQUE(lead_id, field, fingerprint)
);
CREATE INDEX IF NOT EXISTS suggestions_queue ON suggestions(status, lead_id);
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS assessment jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS events (
    id bigserial PRIMARY KEY,
    lead_id uuid REFERENCES leads(id),
    action text NOT NULL,
    actor text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
    id uuid PRIMARY KEY,
    kind text NOT NULL DEFAULT 'enrich',
    lead_id uuid NOT NULL REFERENCES leads(id),
    status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed','cancelled')),
    attempts integer NOT NULL DEFAULT 0,
    result jsonb NOT NULL DEFAULT '{}',
    available_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active ON jobs(lead_id, kind) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS requests (
    key text PRIMARY KEY,
    digest text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS syncs (
    id uuid PRIMARY KEY,
    lead_id uuid NOT NULL REFERENCES leads(id),
    fingerprint text NOT NULL UNIQUE,
    mode text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    result jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL
);
INSERT INTO settings(key, value) VALUES ('sources', '{"registry":false,"website":false,"ai":false}') ON CONFLICT DO NOTHING;
INSERT INTO settings(key, value) VALUES ('backfill', '{"threshold":80,"field_thresholds":{},"auto_fill_empty":false,"max_pages":6,"web_search":false}') ON CONFLICT DO NOTHING;

-- Recovery of old proposals must not scan all audit events once per suggestion.
CREATE INDEX IF NOT EXISTS events_original_proposal ON events((detail->>'suggestion_id'),id)
WHERE action IN ('approved','rejected');
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS proposed_value jsonb;
UPDATE suggestions s SET proposed_value=COALESCE(
    (SELECT e.detail->'proposed' FROM events e WHERE e.action IN ('approved','rejected')
      AND e.detail->>'suggestion_id'=s.id::text ORDER BY e.id LIMIT 1), s.value)
WHERE s.proposed_value IS NULL;

CREATE TABLE IF NOT EXISTS scoring_versions (
    revision bigserial PRIMARY KEY,
    weights jsonb NOT NULL,
    reviewer text NOT NULL,
    note text NOT NULL,
    evaluation jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS calibration_reviews (
    id bigserial PRIMARY KEY,
    suggestion_id uuid NOT NULL REFERENCES suggestions(id),
    judgment text NOT NULL CHECK(judgment IN ('correct','incorrect','unknown')),
    reviewer text NOT NULL,
    note text NOT NULL DEFAULT '',
    sample_fingerprint text NOT NULL,
    sample jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calibration_reviews_latest ON calibration_reviews(suggestion_id,id DESC);

CREATE TABLE IF NOT EXISTS outbound_searches (
    id uuid PRIMARY KEY,
    request_key text NOT NULL UNIQUE,
    fingerprint text NOT NULL,
    query jsonb NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed')),
    attempts integer NOT NULL DEFAULT 0,
    result jsonb NOT NULL DEFAULT '{}',
    error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);
CREATE TABLE IF NOT EXISTS outbound_promotions (
    search_id uuid NOT NULL REFERENCES outbound_searches(id),
    candidate_id text NOT NULL,
    lead_id uuid NOT NULL REFERENCES leads(id),
    reviewer text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(search_id,candidate_id)
);

ALTER TABLE leads ADD COLUMN IF NOT EXISTS intake_data jsonb NOT NULL DEFAULT '{}';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS identity_checks jsonb NOT NULL DEFAULT '{}';
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS standard_proof jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS erasure_blocks (
    kind text NOT NULL,
    fingerprint text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(kind,fingerprint)
);
CREATE TABLE IF NOT EXISTS erasure_receipts (
    id uuid PRIMARY KEY,
    counts jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Durable bulk work: metadata, bounded file chunks and independently committed rows.
CREATE TABLE IF NOT EXISTS transfer_jobs (
    id uuid PRIMARY KEY,
    request_key uuid NOT NULL UNIQUE,
    kind text NOT NULL CHECK(kind IN ('import','hubspot_import','export')),
    status text NOT NULL DEFAULT 'queued',
    phase text NOT NULL,
    filename text NOT NULL DEFAULT '',
    digest text NOT NULL DEFAULT '',
    byte_count bigint NOT NULL DEFAULT 0,
    encoding text NOT NULL DEFAULT 'utf-8-sig',
    headers jsonb NOT NULL DEFAULT '[]',
    mapping jsonb,
    total integer,
    staged integer NOT NULL DEFAULT 0,
    processed integer NOT NULL DEFAULT 0,
    created_count integer NOT NULL DEFAULT 0,
    duplicate_count integer NOT NULL DEFAULT 0,
    error_count integer NOT NULL DEFAULT 0,
    exported_count integer NOT NULL DEFAULT 0,
    cursor_value text NOT NULL DEFAULT '',
    source_after text,
    source_done boolean NOT NULL DEFAULT false,
    snapshot_at timestamptz,
    snapshot_stamp jsonb,
    error text NOT NULL DEFAULT '',
    attempts integer NOT NULL DEFAULT 0,
    lease uuid,
    lease_until timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours'
);
CREATE INDEX IF NOT EXISTS transfer_jobs_claim ON transfer_jobs(available_at,created_at) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS transfer_blobs (
    job_id uuid NOT NULL REFERENCES transfer_jobs(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN ('input','output')),
    part integer NOT NULL,
    content bytea NOT NULL,
    PRIMARY KEY(job_id,kind,part)
);
CREATE TABLE IF NOT EXISTS transfer_rows (
    job_id uuid NOT NULL REFERENCES transfer_jobs(id) ON DELETE CASCADE,
    line integer NOT NULL,
    row_data jsonb,
    status text NOT NULL DEFAULT 'pending',
    lead_id uuid REFERENCES leads(id) ON DELETE SET NULL,
    error text NOT NULL DEFAULT '',
    PRIMARY KEY(job_id,line)
);
CREATE INDEX IF NOT EXISTS transfer_rows_pending ON transfer_rows(job_id,line) WHERE status='pending';
CREATE INDEX IF NOT EXISTS transfer_rows_lead ON transfer_rows(lead_id) WHERE lead_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS requests_lead_result ON requests((result->>'id'));
CREATE INDEX IF NOT EXISTS events_lead ON events(lead_id);
CREATE INDEX IF NOT EXISTS leads_page ON leads(created_at,id) WHERE NOT suppressed;
