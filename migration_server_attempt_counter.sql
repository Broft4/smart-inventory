CREATE TABLE IF NOT EXISTS verify_attempt_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    target_type VARCHAR(20) NOT NULL,
    target_id VARCHAR(100) NOT NULL,
    checked_by_user_id INTEGER NOT NULL,
    attempts_used INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(report_id, target_type, target_id, checked_by_user_id),
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY(checked_by_user_id) REFERENCES users(id) ON DELETE CASCADE
);
