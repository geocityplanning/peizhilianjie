CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    channel_name TEXT NOT NULL,
    base_platform TEXT,
    channel_data_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS apps (
    platform_id TEXT PRIMARY KEY,
    app_name TEXT NOT NULL,
    channel_id TEXT,
    channel_name TEXT,
    cloud_app_link TEXT,
    cloud_app_short_link TEXT,
    base TEXT,
    settlement_type TEXT,
    status TEXT,
    row_data_json TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    operation_type TEXT NOT NULL,
    execution_id TEXT,
    success INTEGER NOT NULL,
    previous_values_json TEXT,
    row_data_json TEXT,
    channel_data_json TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    stage TEXT NOT NULL,
    queue_position INTEGER,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT,
    batch_id TEXT,
    execution_id TEXT,
    stage_elapsed_seconds INTEGER,
    elapsed_seconds INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created
ON jobs (status, created_at);
