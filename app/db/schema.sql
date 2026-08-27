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

