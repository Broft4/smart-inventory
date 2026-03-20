

BEGIN TRANSACTION;

DELETE FROM verify_attempt_progress
WHERE report_id = 4;

DELETE FROM check_results
WHERE report_id = 4;

DELETE FROM report_target_snapshots
WHERE report_id = 4;

DELETE FROM report_employee_completions
WHERE report_id = 4;

DELETE FROM category_assignments
WHERE location = 'Дубна'
  AND cycle_version = 1;

COMMIT;
