-- 1. Новая таблица доступа обычных админов к точкам
CREATE TABLE IF NOT EXISTS admin_location_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_user_id INTEGER NOT NULL,
    location_point_id INTEGER NOT NULL,
    granted_by_user_id INTEGER,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(admin_user_id, location_point_id),
    FOREIGN KEY(admin_user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(location_point_id) REFERENCES location_points(id) ON DELETE CASCADE,
    FOREIGN KEY(granted_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_admin_location_access_admin_user_id ON admin_location_access(admin_user_id);
CREATE INDEX IF NOT EXISTS ix_admin_location_access_location_point_id ON admin_location_access(location_point_id);
CREATE INDEX IF NOT EXISTS ix_admin_location_access_granted_by_user_id ON admin_location_access(granted_by_user_id);

-- 2. Перевести основной аккаунт в главного администратора.
-- Подставьте нужный логин вместо admin, если у вас другой основной аккаунт.
UPDATE users
SET role = 'superadmin', location = NULL
WHERE username = 'admin';

-- 3. Если у вас уже были обычные админы с одной точкой в users.location,
-- перенесите их доступы в новую таблицу.
INSERT OR IGNORE INTO admin_location_access (admin_user_id, location_point_id, granted_by_user_id, created_at)
SELECT u.id, lp.id, NULL, CURRENT_TIMESTAMP
FROM users u
JOIN location_points lp ON lp.name = u.location
WHERE u.role = 'admin' AND u.location IS NOT NULL;
