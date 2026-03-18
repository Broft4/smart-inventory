# Smart Inventory

Веб-приложение для проведения ревизий по точкам с ролями **admin** и **employee**.

Проект построен на **FastAPI + Jinja2 + SQLAlchemy Async + SQLite** и умеет работать как с реальными остатками из **МойСклад**, так и с mock-данными, если токен не задан.

## Что умеет приложение

### Для администратора
- вход в админ-панель по сессии
- создание и редактирование пользователей
- создание точек и привязка их к складам МойСклад
- настройка **15-дневного цикла** ревизии
- выбор категорий и подкатегорий для цикла
- изменение даты начала цикла
- просмотр списка ревизий по точке
- просмотр детальной ревизии
- удаление ревизий
- экспорт диагностики проблемной разметки в CSV
- отдельная нумерация ревизий по точкам и циклам в интерфейсе

### Для сотрудника
- вход под своей учётной записью
- получение структуры остатков по назначенной точке
- выбор категорий / подкатегорий / служебных товаров в рамках активного цикла
- выполнение ревизии с сохранением результатов
- вкладки **«Мои»**, **«Свободные»**, **«Занятые»**, **«Завершённые»**
- перенос завершённых подкатегорий во вкладку **«Завершённые»**
- ручной запуск ревизии на текущий день
- завершение текущей дневной ревизии

## Стек
- Python 3.11
- FastAPI
- Uvicorn
- SQLAlchemy 2.x
- aiosqlite
- Jinja2
- httpx
- Apache / Nginx как reverse proxy при деплое

## Структура проекта

```text
app/
  config.py        # настройки приложения и переменные окружения
  database.py      # SQLAlchemy engine / session / Base
  logic.py         # основная бизнес-логика
  main.py          # FastAPI routes и инициализация приложения
  models.py        # ORM-модели
  moysklad.py      # интеграция с API МойСклад
  schemas.py       # Pydantic-схемы

static/
  css/style.css
  js/admin.js
  js/login.js
  js/main.js

templates/
  admin.html
  index.html
  login.html

scripts/
  migrate_sqlite_to_postgres.py

inventory.db       # локальная SQLite база
requirements.txt
README.md
```

## Переменные окружения

Приложение читает настройки из файла `.env`.

Поддерживаемые переменные:

```env
DATABASE_URL=sqlite+aiosqlite:///./inventory.db
SESSION_SECRET_KEY=change-me

DEFAULT_ADMIN_FULL_NAME=Главный администратор
DEFAULT_ADMIN_BIRTH_DATE=1990-01-01
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=admin123

MOYSKLAD_TOKEN=
STORE_DMITROV=Дмитров
STORE_DUBNA=Дубна
STORE_DMITROV_ID=
STORE_DUBNA_ID=
MS_API_BASE_URL=https://api.moysklad.ru/api/remap/1.2
MS_INVENTORY_CACHE_TTL_SECONDS=120
MS_REQUEST_TIMEOUT_SECONDS=30
MS_RETRY_ATTEMPTS=4
```

> Если `MOYSKLAD_TOKEN` не задан, приложение использует fallback на mock-данные.

## Быстрый старт локально

### 1. Создать виртуальное окружение

```bash
python -m venv .venv
source .venv/bin/activate
```

Для Windows:

```powershell
.venv\Scripts\activate
```

### 2. Установить зависимости

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Создать `.env`

Если файла `.env` нет, создай его вручную по списку переменных выше.

### 4. Запустить приложение

```bash
uvicorn app.main:app --reload
```

После запуска:
- страница входа: `http://127.0.0.1:8000/login`
- сотрудник: `http://127.0.0.1:8000/`
- админка: `http://127.0.0.1:8000/admin`

## Первый вход

По умолчанию приложение может создать администратора из переменных:
- `DEFAULT_ADMIN_USERNAME`
- `DEFAULT_ADMIN_PASSWORD`

Если переменные не менялись, стандартные значения в коде такие:
- логин: `admin`
- пароль: `admin123`

Рекомендуется сразу поменять пароль после первого входа.

## Как работает цикл ревизии

- Для каждой точки ведётся отдельный **15-дневный цикл**.
- Администратор выбирает категории / подкатегории, доступные в рамках активного цикла.
- Сотрудники берут доступные участки в работу.
- Дневная ревизия создаётся автоматически при открытии структуры сотрудником.
- Завершённые участки уходят во вкладку **«Завершённые»**.
- История ревизий хранится в базе.
- В интерфейсе админа нумерация ревизий считается **отдельно по точке и по циклу**.

## API и основные маршруты

### Публичные / сессионные страницы
- `GET /login` — страница входа
- `GET /` — страница сотрудника
- `GET /admin` — страница администратора

### Авторизация
- `POST /api/login`
- `POST /api/logout`
- `GET /api/me`

### Точки и цикл
- `GET /api/locations`
- `POST /api/locations`
- `POST /api/locations/stores`
- `GET /api/cycle-targets`
- `POST /api/cycle-targets`

### Пользователи
- `GET /api/users`
- `POST /api/users`
- `PUT /api/users/{user_id}`
- `DELETE /api/users/{user_id}`

### Ревизии и структура
- `GET /get-structure`
- `POST /assign-selection`
- `POST /verify`
- `POST /finish-report`
- `GET /api/reports`
- `GET /api/report`
- `DELETE /api/report/{report_id}`
- `GET /api/inventory-diagnostics/export`

## Деплой на VPS

Проект уже запускался на Linux-сервере через:
- `systemd` для фонового сервиса
- Apache как reverse proxy на `127.0.0.1:8000`

### Пример systemd unit

```ini
[Unit]
Description=Smart Inventory FastAPI
After=network.target

[Service]
User=root
WorkingDirectory=/opt/smart-inventory
ExecStart=/opt/smart-inventory/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Пример Apache-конфига

```apache
<VirtualHost *:80>
    ServerName your-domain.example
    ProxyRequests Off
    ProxyPreserveHost On

    ProxyPass / http://127.0.0.1:8000/
    ProxyPassReverse / http://127.0.0.1:8000/
</VirtualHost>
```

После изменений:

```bash
systemctl daemon-reload
systemctl restart smart_inventory
systemctl restart httpd
```

## Обновление проекта на сервере

Если код обновляется через `git pull`, не стоит хранить рабочую `inventory.db` в Git.

Рекомендуется:
- убрать `inventory.db` из отслеживания Git
- добавить её в `.gitignore`
- хранить боевую базу только на сервере

## Что лучше не коммитить

Добавь в `.gitignore`, если этого ещё нет:

```gitignore
.venv/
__pycache__/
*.pyc
.env
inventory.db
*.db-journal
```

## Полезные замечания

- После обновления фронтенда браузер может держать старые `main.js` или `admin.js` в кэше.
- Если в одном браузере всё работает, а в другом нет, сначала попробуй:
  - hard reload
  - очистку site data
  - режим инкогнито
- При деплое на старые yum-серверы может понадобиться отдельная установка Python 3.11.
