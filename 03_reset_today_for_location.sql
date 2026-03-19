-- ==============================================================
-- ПОЛНОСТЬЮ ПЕРЕЗАПУСТИТЬ СЕГОДНЯШНЮЮ РЕВИЗИЮ ПО ТОЧКЕ
-- ==============================================================
-- Перед запуском:
-- 1) сделай backup inventory.db
-- 2) замени значения ниже на реальные
--
-- REPLACE_ME_REPORT_ID      -> id сегодняшнего отчёта по точке
-- REPLACE_ME_LOCATION       -> название точки
-- REPLACE_ME_CYCLE_VERSION  -> cycle_version сегодняшнего отчёта
--
-- Этот скрипт:
-- - очищает сегодняшний прогресс по точке
-- - удаляет все snapshots текущего дня
-- - снимает отметки завершения по сотрудникам
-- - удаляет все текущие закрепления сотрудников по точке/циклу
--
-- Это лучший вариант, если после фикса ты хочешь,
-- чтобы ВСЕ сотрудники на этой точке заново выбрали, что берут сегодня.

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
