CREATE TABLE server_tokens (
    name       text PRIMARY KEY,
    hash       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
