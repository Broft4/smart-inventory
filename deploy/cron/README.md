# Автообновление кешей в 04:00 по Москве

## Что обновлять ночью

- **Кеш бухгалтерии / зарплаты** — только **вчерашний день**, но с обязательным live-обновлением. Это закрывает вчерашние зависшие смены и не оставляет старый кеш.
- **Кеш себестоимости и розницы товаров для админ-ревизий** — отдельным запуском, потому что это не дневные продажи, а карточки товаров.
- **Старые периоды** за последние 40 дней добирайте вручную отдельной командой, когда нужно пересчитать историю.

## Вариант 1: через `crontab -e`

```cron
CRON_TZ=Europe/Moscow
0 4 * * * flock -n /tmp/smart_inventory_payroll_cache.lock sh -c 'cd /opt/smart_inventory && /opt/smart_inventory/.venv/bin/python -m scripts.refresh_payroll_metrics_cache --yesterday-only --auto-close-open-shifts --force-refresh --rebuild-closed-shifts --skip-product-financials >> /var/log/smart_inventory_payroll_cache.log 2>&1'
20 4 * * * flock -n /tmp/smart_inventory_product_financial_cache.lock sh -c 'cd /opt/smart_inventory && /opt/smart_inventory/.venv/bin/python -m scripts.refresh_product_financial_cache >> /var/log/smart_inventory_product_financial_cache.log 2>&1'
```

## Вариант 2: через `/etc/cron.d/smart_inventory_maintenance`

```cron
CRON_TZ=Europe/Moscow
0 4 * * * root flock -n /tmp/smart_inventory_payroll_cache.lock sh -c 'cd /opt/smart_inventory && /opt/smart_inventory/.venv/bin/python -m scripts.refresh_payroll_metrics_cache --yesterday-only --auto-close-open-shifts --force-refresh --rebuild-closed-shifts --skip-product-financials >> /var/log/smart_inventory_payroll_cache.log 2>&1'
20 4 * * * root flock -n /tmp/smart_inventory_product_financial_cache.lock sh -c 'cd /opt/smart_inventory && /opt/smart_inventory/.venv/bin/python -m scripts.refresh_product_financial_cache >> /var/log/smart_inventory_product_financial_cache.log 2>&1'
```

## Разовый ручной добор истории за последние 40 дней

```bash
cd /opt/smart_inventory
/opt/smart_inventory/.venv/bin/python -m scripts.refresh_payroll_metrics_cache --days 40 --force-refresh --rebuild-closed-shifts
```

## Разовый прогрев локального кеша себестоимости/цен товаров

```bash
cd /opt/smart_inventory
/opt/smart_inventory/.venv/bin/python -m scripts.refresh_product_financial_cache --force-refresh
```

## Диагностический дамп raw-ответов МоегоСклада

```bash
cd /opt/smart_inventory
/opt/smart_inventory/.venv/bin/python -m scripts.dump_moysklad_retail_metrics --location "Дмитров" --date-from 2026-04-01
```
