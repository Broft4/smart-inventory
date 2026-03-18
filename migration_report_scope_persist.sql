CREATE TABLE IF NOT EXISTS report_target_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    category_id VARCHAR(100) NOT NULL,
    category_name VARCHAR(255) NOT NULL,
    subcategory_id VARCHAR(100),
    subcategory_name VARCHAR(255),
    target_type VARCHAR(20) NOT NULL,
    target_id VARCHAR(100) NOT NULL,
    target_name VARCHAR(255) NOT NULL,
    assigned_user_id_snapshot INTEGER,
    assigned_user_name_snapshot VARCHAR(255),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_report_target_snapshot_per_user
    ON report_target_snapshots(report_id, target_type, target_id, assigned_user_id_snapshot);

CREATE INDEX IF NOT EXISTS ix_report_target_snapshots_report_id
    ON report_target_snapshots(report_id);

CREATE INDEX IF NOT EXISTS ix_report_target_snapshots_category_id
    ON report_target_snapshots(category_id);

CREATE INDEX IF NOT EXISTS ix_report_target_snapshots_subcategory_id
    ON report_target_snapshots(subcategory_id);

CREATE INDEX IF NOT EXISTS ix_report_target_snapshots_target_id
    ON report_target_snapshots(target_id);

CREATE INDEX IF NOT EXISTS ix_report_target_snapshots_assigned_user_id_snapshot
    ON report_target_snapshots(assigned_user_id_snapshot);
