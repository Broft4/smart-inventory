CREATE TABLE IF NOT EXISTS report_employee_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    user_full_name_snapshot VARCHAR(255) NOT NULL,
    finished_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_report_employee_completion_per_user UNIQUE (report_id, user_id),
    FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_report_employee_completions_report_id ON report_employee_completions(report_id);
CREATE INDEX IF NOT EXISTS ix_report_employee_completions_user_id ON report_employee_completions(user_id);
