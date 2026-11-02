CREATE TABLE walls (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_needed  text NOT NULL,
    system         text NOT NULL,
    blocker_type   text NOT NULL CHECK (blocker_type IN (
        'permission', 'credential', 'no_tool', 'physical_or_ui', 'human_judgment', 'network'
    )),
    embedding      vector(1536) NOT NULL,
    status         text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolution     text,
    resolved_at    timestamptz,
    reopened_count integer NOT NULL DEFAULT 0,
    merged_into_id bigint REFERENCES walls (id),
    remedy         jsonb,
    remedy_count   integer,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE occurrences (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    wall_id       bigint REFERENCES walls (id),
    action_needed text NOT NULL,
    system        text NOT NULL,
    blocker_type  text NOT NULL,
    why_blocked   text NOT NULL,
    suggested_fix text,
    repo          text,
    client        text,
    machine       text,
    session_id    text,
    match_score   real,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX occurrences_wall ON occurrences (wall_id, created_at);
CREATE INDEX occurrences_unplaced ON occurrences (id) WHERE wall_id IS NULL;

CREATE TABLE merge_candidates (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    wall_id    bigint NOT NULL REFERENCES walls (id),
    similar_id bigint NOT NULL REFERENCES walls (id),
    score      real NOT NULL,
    status     text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'merged', 'kept_separate')),
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (wall_id, similar_id)
);

CREATE TABLE digests (
    week    text PRIMARY KEY,
    sent_at timestamptz NOT NULL DEFAULT now(),
    mail_id text
);
