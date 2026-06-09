# Переход Smart Inventory / UCHETKA с SQLite на PostgreSQL

Цель перехода — оставить всю бизнес-логику такой же, как сейчас, но заменить файл `inventory.db` на промышленную PostgreSQL-БД. После перехода смены, выручка, ревизии, премии, штрафы, расходы, настройки зарплаты, токены МойСклад и реферальные заявки сохраняются в одной PostgreSQL-БД.

Важно: изоляция клиентов делается не отдельными файлами БД, а ролями и доступами к точкам. Роль `platform_admin` видит всё, главные управляющие (`superadmin`) и управляющие (`admin`) работают только со своими точками.

## 1. Что меняется в проекте

В `.env` вместо SQLite нужно будет указать PostgreSQL:

```env
DATABASE_URL=postgresql+asyncpg://uchetka:STRONG_PASSWORD@127.0.0.1:5432/uchetka
```

SQLite-адрес для старой БД больше не используется в работе приложения, но файл `inventory.db` нужно сохранить как бэкап.

## 2. Обязательный бэкап перед миграцией

На сервере:

```bash
cd /opt/smart-inventory
mkdir -p backups
systemctl stop smart_inventory
cp inventory.db backups/inventory_$(date +%F_%H%M).db
cp .env backups/env_$(date +%F_%H%M).txt
```

## 3. Создать PostgreSQL-БД

Если PostgreSQL уже установлен на сервере:

```bash
sudo -u postgres psql
```

Внутри `psql`:

```sql
CREATE USER uchetka WITH PASSWORD 'STRONG_PASSWORD';
CREATE DATABASE uchetka OWNER uchetka;
\q
```

Если на Jino есть отдельная управляемая PostgreSQL-БД, можно использовать её. Тогда в `DATABASE_URL` нужно указать хост, порт, имя БД, пользователя и пароль из панели Jino.

## 4. Установить зависимости проекта

`asyncpg` уже есть в `requirements.txt`, но после обновления файлов лучше выполнить:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. Перенести данные SQLite → PostgreSQL

Пока `.env` ещё может оставаться на SQLite. Запускаем миграцию явно с PostgreSQL URL:

```bash
cd /opt/smart-inventory
source .venv/bin/activate
PYTHONPATH=/opt/smart-inventory \
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite /opt/smart-inventory/inventory.db \
  --postgres 'postgresql+asyncpg://uchetka:STRONG_PASSWORD@127.0.0.1:5432/uchetka'
```

Скрипт рассчитан на пустую PostgreSQL-БД. Если ты тестируешь на временной PostgreSQL-БД и хочешь полностью пересоздать таблицы:

```bash
PYTHONPATH=/opt/smart-inventory \
python scripts/migrate_sqlite_to_postgres.py \
  --sqlite /opt/smart-inventory/inventory.db \
  --postgres 'postgresql+asyncpg://uchetka:STRONG_PASSWORD@127.0.0.1:5432/uchetka' \
  --drop-existing --yes
```

На боевой БД `--drop-existing --yes` используй только если это новая пустая PostgreSQL-БД или тестовая база.

## 6. Переключить приложение на PostgreSQL

Открой `.env`:

```bash
nano /opt/smart-inventory/.env
```

Поставь:

```env
DATABASE_URL=postgresql+asyncpg://uchetka:STRONG_PASSWORD@127.0.0.1:5432/uchetka
PUBLIC_APP_URL=https://uchetka-retail.ru
```

Потом запусти сервис:

```bash
systemctl start smart_inventory
systemctl status smart_inventory -l --no-pager
```

Если сервис уже был запущен:

```bash
systemctl restart smart_inventory
```

## 7. Проверки после запуска

```bash
curl -I http://127.0.0.1:8000/login
journalctl -u smart_inventory -n 100 -l --no-pager
```

В интерфейсе нужно проверить:

- вход админа;
- список точек;
- список сотрудников;
- заявки и рефералы;
- ревизии;
- смены;
- бухгалтерию;
- премии, штрафы и расходы;
- токен МойСклад для точки;
- загрузку остатков/товаров.

## 8. Если нужно срочно откатиться на SQLite

```bash
systemctl stop smart_inventory
```

В `.env` вернуть:

```env
DATABASE_URL=sqlite+aiosqlite:///./inventory.db
```

Потом:

```bash
systemctl start smart_inventory
```

PostgreSQL-БД при этом останется нетронутой, а приложение снова будет работать со старым `inventory.db`.

## 9. Почему не отдельная SQLite-БД на каждого главного управляющего

Для релиза отдельные SQLite-файлы на каждого клиента усложнят:

- миграции;
- бэкапы;
- фоновые задачи;
- отчёты;
- рефералы;
- админский доступ ко всем клиентам;
- обновление схемы при новых функциях;
- отладку проблем с МойСклад.

PostgreSQL решает это лучше: одна БД, но данные разделены доступами к точкам и ролями. Для твоего сервиса это правильный следующий шаг перед нормальным релизом.
