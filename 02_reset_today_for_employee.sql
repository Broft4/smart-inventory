-- ==============================================================
-- СБРОСИТЬ ХВОСТЫ ТОЛЬКО ДЛЯ ОДНОГО СОТРУДНИКА НА СЕГОДНЯ
-- ==============================================================
-- Перед запуском:
-- 1) сделай backup inventory.db
-- 2) замени значения ниже на реальные
--
-- REPLACE_ME_REPORT_ID      -> id сегодняшнего отчёта по точке
-- REPLACE_ME_USER_ID        -> id сотрудника
-- REPLACE_ME_LOCATION       -> название точки
-- REPLACE_ME_CYCLE_VERSION  -> cycle_version сегодняшнего отчёта
--
-- Этот скрипт:
-- - снимает текущие закрепления сотрудника по точке/циклу
-- - удаляет его snapshots за сегодняшний отчёт
-- - удаляет его сегодняшние результаты и прогресс попыток
-- - снимает отметку о завершении сегодняшней ревизии
--
-- Используй, если сотрудник должен начать СЕГОДНЯ заново.

BEGIN TRANSACTION;

DELETE FROM verify_attempt_progress
WHERE report_id = REPLACE_ME_REPORT_ID
  AND checked_by_user_id = REPLACE_ME_USER_ID;

DELETE FROM check_results
WHERE report_id = REPLACE_ME_REPORT_ID
  AND checked_by_user_id = REPLACE_ME_USER_ID;

DELETE FROM report_target_snapshots
WHERE report_id = REPLACE_ME_REPORT_ID
  AND assigned_user_id_snapshot = REPLACE_ME_USER_ID;

DELETE FROM report_employee_completions
WHERE report_id = REPLACE_ME_REPORT_ID
  AND user_id = REPLACE_ME_USER_ID;

DELETE FROM category_assignments
WHERE location = 'REPLACE_ME_LOCATION'
  AND cycle_version = REPLACE_ME_CYCLE_VERSION
  AND user_id = REPLACE_ME_USER_ID;

COMMIT;
