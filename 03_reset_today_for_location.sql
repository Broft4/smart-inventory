

BEGIN TRANSACTION;

DELETE FROM verify_attempt_progress
WHERE report_id = REPLACE_ME_REPORT_ID;

DELETE FROM check_results
WHERE report_id = REPLACE_ME_REPORT_ID;

DELETE FROM report_target_snapshots
WHERE report_id = REPLACE_ME_REPORT_ID;

DELETE FROM report_employee_completions
WHERE report_id = REPLACE_ME_REPORT_ID;

DELETE FROM category_assignments
WHERE location = 'REPLACE_ME_LOCATION'
  AND cycle_version = REPLACE_ME_CYCLE_VERSION;

COMMIT;
