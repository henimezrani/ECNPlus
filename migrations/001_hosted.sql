CREATE TABLE ecn_profiles (
    slug text PRIMARY KEY CHECK (slug IN ('yasmine', 'houssein', 'autre')),
    settings jsonb NOT NULL,
    credential text,
    account_id text,
    latest_complete jsonb,
    latest_attempt jsonb
);
CREATE TABLE ecn_runs (
    profile text NOT NULL REFERENCES ecn_profiles(slug),
    id text NOT NULL,
    started_at text NOT NULL,
    finished_at text,
    status text NOT NULL,
    PRIMARY KEY (profile, id)
);
CREATE INDEX ecn_runs_latest ON ecn_runs(profile, started_at DESC);
CREATE TABLE ecn_tasks (
    profile text NOT NULL,
    run_id text NOT NULL,
    path text NOT NULL,
    kind text NOT NULL,
    context jsonb NOT NULL,
    PRIMARY KEY (profile, run_id, path),
    FOREIGN KEY (profile, run_id) REFERENCES ecn_runs(profile, id) ON DELETE CASCADE
);
CREATE TABLE ecn_responses (
    profile text NOT NULL,
    run_id text NOT NULL,
    path text NOT NULL,
    kind text NOT NULL,
    context jsonb NOT NULL,
    fetched_at text NOT NULL,
    body jsonb,
    error text,
    status_code integer,
    PRIMARY KEY (profile, run_id, path),
    FOREIGN KEY (profile, run_id) REFERENCES ecn_runs(profile, id) ON DELETE CASCADE
);
CREATE TABLE ecn_jobs (
    profile text PRIMARY KEY REFERENCES ecn_profiles(slug),
    id text NOT NULL,
    kind text NOT NULL,
    status text NOT NULL,
    message text NOT NULL,
    processed integer NOT NULL DEFAULT 0,
    total integer NOT NULL DEFAULT 0,
    started_at text NOT NULL,
    finished_at text,
    lease_until timestamptz,
    payload jsonb NOT NULL DEFAULT '{}'
);
