-- Посмотреть точки
SELECT id, name FROM location_points ORDER BY name;

-- Посмотреть активных сотрудников по точке
-- ЗАМЕНИ 'Дмитров' на свою точку
SELECT id, full_name, username, location, is_active
FROM users
WHERE role = 'employee' AND location = 'Дмитров'
ORDER BY full_name;

-- Посмотреть последние отчёты по точке
-- ЗАМЕНИ 'Дмитров' на свою точку
SELECT id, location, report_date, cycle_version, status, date_created
FROM reports
WHERE location = 'Дмитров'
ORDER BY report_date DESC, id DESC
LIMIT 20;

-- Посмотреть текущие закрепления сотрудника
-- ЗАМЕНИ location и user_id
SELECT id, location, cycle_version, target_type, category_name, subcategory_name, target_name, user_id, user_full_name_snapshot, assigned_at
FROM category_assignments
WHERE location = 'Дмитров' AND user_id = 1
ORDER BY target_type, category_name, subcategory_name, target_name;

-- Посмотреть сегодняшние snapshots по сотруднику
-- ЗАМЕНИ report_id и user_id
SELECT id, report_id, target_type, category_name, subcategory_name, target_name, assigned_user_id_snapshot, assigned_user_name_snapshot, created_at
FROM report_target_snapshots
WHERE report_id = 1 AND assigned_user_id_snapshot = 1
ORDER BY target_type, category_name, subcategory_name, target_name;

-- Посмотреть сегодняшние результаты сотрудника
-- ЗАМЕНИ report_id и user_id
SELECT id, report_id, target_type, category_name, subcategory_name, target_name, status, checked_by_user_id, checked_by_name_snapshot, created_at
FROM check_results
WHERE report_id = 1 AND checked_by_user_id = 1
ORDER BY target_type, category_name, subcategory_name, target_name;
