const payrollState = {
    user: window.currentUser || {},
    locations: [],
    employees: [],
    admins: [],
    settings: null,
    categoryCatalog: [],
    summary: null,
    managerSummary: null,
    templates: [],
    expenses: [],
    audit: [],
    shiftDays: [],
    categoryFilters: {
        search: '',
        view: 'all',
        sort: 'earning_desc',
    },
};

function qs(id) {
    return document.getElementById(id);
}

function isAdminRole() {
    return ['admin', 'superadmin'].includes(payrollState.user.role);
}

function formatMoney(value) {
    const num = Number(value || 0);
    return `${num.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatDateRu(iso) {
    if (!iso) return '';
    const date = new Date(`${iso}T00:00:00`);
    return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(date);
}

function formatTimeRu(dateTimeValue) {
    if (!dateTimeValue) return '';
    const normalized = String(dateTimeValue).replace(' ', 'T');
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return String(dateTimeValue);
    return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(date);
}

function normalizeSearch(value) {
    return String(value ?? '').trim().toLowerCase();
}

function compactCurrency(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return '0';
    return num.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
}

function isMobileCompactMode() {
    return window.matchMedia('(max-width: 640px)').matches;
}

function monthLabel(monthValue) {
    const [year, month] = String(monthValue || monthIso()).split('-').map(Number);
    if (!year || !month) return '';
    return new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(new Date(year, month - 1, 1));
}

function setButtonLoading(button, isLoading, loadingLabel = 'Сохраняем...') {
    if (!button) return;
    const loader = button.querySelector('.btn-loader');
    const label = button.querySelector('.btn-label');
    if (isLoading) {
        button.disabled = true;
        button.dataset.originalLabel = label ? label.textContent : button.textContent;
        button.classList.add('is-loading');
        if (loader) loader.classList.remove('hidden');
        if (label) label.textContent = loadingLabel;
    } else {
        button.disabled = false;
        button.classList.remove('is-loading');
        if (loader) loader.classList.add('hidden');
        if (label) label.textContent = button.dataset.originalLabel || label.textContent;
    }
}

function showStatus(message, tone = 'loading') {
    const box = qs('payroll-status');
    box.textContent = message;
    box.className = `inventory-status ${tone}`;
    box.classList.remove('hidden');
}

function hideStatus() {
    const box = qs('payroll-status');
    box.classList.add('hidden');
    box.textContent = '';
    box.className = 'inventory-status hidden';
}

async function api(url, options = {}) {
    const response = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        ...options,
    });
    let payload = null;
    try {
        payload = await response.json();
    } catch {
        payload = null;
    }
    if (!response.ok) {
        throw new Error(payload?.detail || payload?.message || 'Ошибка запроса');
    }
    return payload;
}

function todayIso() {
    return new Date().toISOString().slice(0, 10);
}

function monthIso() {
    return todayIso().slice(0, 7);
}

function setDefaultDates() {
    if (qs('payroll-date-from')) qs('payroll-date-from').value = todayIso();
    if (qs('payroll-date-to')) qs('payroll-date-to').value = todayIso();
    if (qs('settings-effective-from')) qs('settings-effective-from').value = todayIso();
    if (qs('shift-month-input')) qs('shift-month-input').value = monthIso();
    if (qs('expenses-month-input')) qs('expenses-month-input').value = monthIso();
    if (qs('shift-date-input')) qs('shift-date-input').value = todayIso();
}

function selectedLocation() {
    return qs('payroll-location-select').value;
}

function selectedEmployeeId() {
    const raw = qs('payroll-employee-select')?.value || '';
    return raw ? Number(raw) : null;
}

function selectedMonthStart(inputId) {
    const raw = qs(inputId).value || monthIso();
    return `${raw}-01`;
}

function renderLocations() {
    const select = qs('payroll-location-select');
    select.innerHTML = payrollState.locations.map(location => `<option value="${location}">${location}</option>`).join('');
    const defaultLocation = payrollState.user.default_location || payrollState.user.location || payrollState.locations[0] || '';
    if (defaultLocation && payrollState.locations.includes(defaultLocation)) {
        select.value = defaultLocation;
    }
}

function renderUsersForLocation() {
    const employeeLabel = qs('payroll-employee-label');
    const employeeSelect = qs('payroll-employee-select');
    const shiftEmployeeSelect = qs('shift-employee-select');
    const shiftModalEmployeeSelect = qs('shift-modal-employee-select');
    const auditEmployeeSelect = qs('audit-employee-filter');
    const employeeOptions = payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`).join('');
    if (!isAdminRole()) {
        employeeLabel.classList.add('hidden');
    } else {
        employeeLabel.classList.remove('hidden');
        employeeSelect.innerHTML = ['<option value="">Все / я</option>', ...payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`)].join('');
    }
    if (shiftEmployeeSelect) shiftEmployeeSelect.innerHTML = employeeOptions;
    if (shiftModalEmployeeSelect) shiftModalEmployeeSelect.innerHTML = employeeOptions;
    if (auditEmployeeSelect) {
        const people = new Map();
        [...(payrollState.employees || []), ...(payrollState.admins || [])].forEach(item => {
            if (item?.id != null) people.set(String(item.id), item.full_name);
        });
        auditEmployeeSelect.innerHTML = ['<option value="">Все сотрудники</option>', ...[...people.entries()].map(([id, name]) => `<option value="${id}">${escapeHtml(name)}</option>`)].join('');
    }
}

function renderSummary(summary) {
    payrollState.summary = summary;
    qs('kpi-shifts').textContent = String((summary.days || []).length);
    qs('kpi-exit').textContent = formatMoney(summary.totals?.exit_amount || 0);
    qs('kpi-bonus').textContent = formatMoney(summary.totals?.bonus_amount || 0);
    qs('kpi-category').textContent = formatMoney(summary.totals?.category_earnings_total || 0);
    qs('kpi-employee-expenses').textContent = formatMoney(summary.employee_expenses_total || 0);
    qs('kpi-payout').textContent = formatMoney(summary.net_payout_amount || 0);

    const shiftsCard = qs('payroll-shifts-card');
    const daysContainer = qs('payroll-days-container');
    if (shiftsCard) {
        shiftsCard.classList.toggle('hidden', isAdminRole());
    }
    if (daysContainer && !isAdminRole()) {
        if (!summary.days?.length) {
            daysContainer.innerHTML = '<div class="muted-text">За выбранный период смен не найдено.</div>';
        } else {
            daysContainer.innerHTML = summary.days.map(day => `
                <article class="payroll-day-card">
                    <div class="payroll-day-header">
                        <strong>${day.shift_date}</strong>
                        <span class="payroll-chip ${day.is_closed ? 'green' : 'orange'}">${day.is_closed ? 'Закрыта' : 'Открыта'}</span>
                        ${!day.is_closed && payrollState.user.role === 'employee' && Number(day.employee_user_id) === Number(payrollState.user.id)
                            ? `<button class="btn secondary btn-inline" type="button" onclick="closeOwnShift(${day.id})">Закрыть смену</button>`
                            : ''}
                    </div>
                    <div class="payroll-day-grid">
                        <div><span class="summary-label">Выручка</span><strong>${formatMoney(day.gross_sales_amount)}</strong></div>
                        <div><span class="summary-label">Возвраты</span><strong>${formatMoney(day.return_amount)}</strong></div>
                        <div><span class="summary-label">Выручка после возвратов</span><strong>${formatMoney(day.net_sales_amount)}</strong></div>
                        <div><span class="summary-label">Выход</span><strong>${formatMoney(day.exit_amount)}</strong></div>
                        <div><span class="summary-label">Бонус</span><strong>${formatMoney(day.bonus_amount)}</strong></div>
                        <div><span class="summary-label">Итого</span><strong>${formatMoney(day.gross_salary_amount)}</strong></div>
                    </div>
                </article>
            `).join('');
        }
    }

    renderEmployeeShiftCalendar(summary);

    if (!isAdminRole()) {
        renderPayrollCategoryTable(summary.categories || []);
    } else if (payrollState.managerSummary?.categories) {
        renderPayrollCategoryTable(payrollState.managerSummary.categories || []);
    } else {
        renderPayrollCategoryTable(summary.categories || []);
    }
}

function getFilteredPayrollCategories(categories = []) {
    const search = normalizeSearch(payrollState.categoryFilters.search);
    const view = payrollState.categoryFilters.view || 'all';
    const sort = payrollState.categoryFilters.sort || 'earning_desc';

    const filtered = (categories || []).filter(category => {
        const categoryName = String(category?.category_name || '');
        if (search && !normalizeSearch(categoryName).includes(search)) {
            return false;
        }
        const earningAmount = Number(category?.earning_amount || 0);
        const salesAmount = Number(category?.sales_amount || 0);
        if (view === 'earned' && Math.abs(earningAmount) <= 1e-9) return false;
        if (view === 'sales' && Math.abs(salesAmount) <= 1e-9) return false;
        return true;
    });

    filtered.sort((left, right) => {
        if (sort === 'name_asc') {
            return String(left?.category_name || '').localeCompare(String(right?.category_name || ''), 'ru');
        }
        if (sort === 'sales_desc') {
            return Number(right?.sales_amount || 0) - Number(left?.sales_amount || 0);
        }
        return Number(right?.earning_amount || 0) - Number(left?.earning_amount || 0);
    });

    return filtered;
}

function renderPayrollCategoryTable(categories = payrollState.summary?.categories || []) {
    const categoryTbody = qs('payroll-category-tbody');
    const filterMeta = qs('payroll-category-filter-meta');
    if (!categoryTbody) return;

    const filtered = getFilteredPayrollCategories(categories);
    if (filterMeta) {
        filterMeta.textContent = `Показано категорий: ${filtered.length} из ${Array.isArray(categories) ? categories.length : 0}`;
    }

    categoryTbody.innerHTML = filtered.length
        ? filtered.map(category => `
            <tr>
                <td data-label="Категория">${escapeHtml(category.category_name)}</td>
                <td data-label="%">${Number(category.rate_percent || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%</td>
                <td data-label="Продажи">${formatMoney(category.sales_amount)}</td>
                <td data-label="Возвраты">${formatMoney(category.return_amount)}</td>
                <td data-label="Чистая сумма">${formatMoney(category.net_sales_amount)}</td>
                <td data-label="Начислено"><strong>${formatMoney(category.earning_amount)}</strong></td>
            </tr>
        `).join('')
        : '<tr><td colspan="6" class="muted-text">По текущим фильтрам категории не найдены.</td></tr>';
}

function applyPayrollCategoryFiltersFromUi() {
    payrollState.categoryFilters.search = qs('payroll-category-search')?.value || '';
    payrollState.categoryFilters.view = qs('payroll-category-view')?.value || 'all';
    payrollState.categoryFilters.sort = qs('payroll-category-sort')?.value || 'earning_desc';
    renderPayrollCategoryTable(payrollState.summary?.categories || []);
}

function syncCollapseToggleText(details) {
    if (!details) return;
    const text = details.querySelector('.payroll-collapse-btn');
    if (!text) return;
    text.textContent = details.open ? 'Свернуть' : 'Развернуть';
}

function initializeCollapseSections() {
    document.querySelectorAll('.payroll-collapse').forEach((details) => {
        syncCollapseToggleText(details);
    });
}

function renderEmployeeShiftCalendar(summary) {
    const card = qs('employee-calendar-card');
    const grid = qs('employee-shift-calendar-grid');
    const details = qs('employee-calendar-details');
    if (!card || !grid || !details) return;

    if (isAdminRole()) {
        card.classList.add('hidden');
        grid.innerHTML = '';
        return;
    }

    card.classList.remove('hidden');
    syncCollapseToggleText(details);

    const days = Array.isArray(summary?.days) ? summary.days : [];
    const month = qs('payroll-date-from')?.value?.slice(0, 7) || monthIso();
    if (!/^\d{4}-\d{2}$/.test(month)) {
        grid.innerHTML = '<div class="shift-calendar-empty muted-text">Нет данных по сменам.</div>';
        return;
    }

    const [year, mon] = month.split('-').map(Number);
    const totalDays = new Date(year, mon, 0).getDate();
    const firstWeekday = (new Date(year, mon - 1, 1).getDay() + 6) % 7;
    const mapByDate = new Map(days.map(day => [String(day.shift_date), day]));
    const today = todayIso();
    const cells = [];

    for (let i = 0; i < firstWeekday; i += 1) {
        cells.push('<div class="shift-calendar-cell shift-calendar-cell--empty" aria-hidden="true"></div>');
    }

    const compactMode = isMobileCompactMode();

    for (let d = 1; d <= totalDays; d += 1) {
        const date = `${year}-${String(mon).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const day = mapByDate.get(date);
        const isToday = date === today;
        let stateClass = '';
        let body = compactMode
            ? '<div class="shift-calendar-empty-day">—</div>'
            : '<div class="shift-calendar-empty-day">Смены нет</div>';
        if (day) {
            if (day.is_closed) stateClass = ' employee-shift-day--closed';
            else if (isToday) stateClass = ' employee-shift-day--today-open';
            else if (date > today) stateClass = ' employee-shift-day--future';

            const categoryAmount = Number(day.category_earnings_total || 0);
            const statusClass = day.is_closed ? 'closed' : (isToday ? 'open' : 'planned');
            const statusLabel = day.is_closed ? 'Завершена' : (isToday ? 'Текущая' : 'Назначена');
            if (compactMode) {
                body = `
                    <div class="employee-shift-status employee-shift-status--compact ${statusClass}">${statusLabel}</div>
                    ${isToday || day.is_closed ? `<div class="employee-shift-compact-total">${compactCurrency(day.gross_salary_amount || 0)} ₽</div>` : ''}
                `;
            } else {
                const lines = [
                    `<div class="employee-shift-line"><span>Выход</span><strong>${formatMoney(day.exit_amount || 0)}</strong></div>`,
                    `<div class="employee-shift-line"><span>Бонус</span><strong>${formatMoney(day.bonus_amount || 0)}</strong></div>`,
                    `<div class="employee-shift-line"><span>Категории</span><strong>${formatMoney(categoryAmount)}</strong></div>`,
                ];
                if (isToday || day.is_closed) {
                    lines.push(`<div class="employee-shift-line total"><span>${day.is_closed ? 'Итог' : 'Промежуточно'}</span><strong>${formatMoney(day.gross_salary_amount || 0)}</strong></div>`);
                }
                body = `
                    <div class="employee-shift-status ${statusClass}">${statusLabel}</div>
                    <div class="employee-shift-lines">${lines.join('')}</div>
                `;
            }
        }

        cells.push(`
            <article class="shift-calendar-cell employee-shift-calendar-cell${isToday ? ' shift-calendar-cell--today' : ''}${stateClass}${compactMode ? ' employee-shift-calendar-cell--compact' : ''}">
                <div class="shift-calendar-cell-head">
                    <div class="shift-calendar-day-meta">
                        <strong>${d}</strong>
                        <span>${compactMode ? '' : formatDateRu(date)}</span>
                    </div>
                </div>
                <div class="shift-calendar-cell-body">${body}</div>
            </article>
        `);
    }

    grid.innerHTML = cells.join('');
}

function renderManagerSummary(summary) {
    payrollState.managerSummary = summary;
    const card = qs('manager-summary-card');
    if (!isAdminRole()) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    qs('manager-net-sales').textContent = formatMoney(summary.net_sales_amount || 0);
    if (qs('manager-returns')) qs('manager-returns').textContent = formatMoney(summary.return_amount || 0);
    qs('manager-cost').textContent = formatMoney(summary.cost_amount || 0);
    qs('manager-employee-salary').textContent = formatMoney(summary.employee_salary_total || 0);
    qs('manager-expenses').textContent = formatMoney(summary.expenses_total || 0);
    qs('manager-profit').textContent = formatMoney(summary.operating_profit_before_manager_salary || 0);
    qs('manager-salary').textContent = `${formatMoney(summary.manager_salary_amount || 0)} (${Number(summary.manager_rate_percent || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%)`;
    if (qs('manager-profit-after-manager')) qs('manager-profit-after-manager').textContent = formatMoney(summary.net_profit_after_manager_salary || 0);
    qs('manager-responsible-line').textContent = summary.responsible_admin_name
        ? `Ответственный администратор точки: ${summary.responsible_admin_name}`
        : 'Ответственный администратор для точки пока не назначен.';
}

function renderSettings() {
    const card = qs('admin-settings-card');
    if (!isAdminRole()) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    const settings = payrollState.settings || {};
    qs('settings-effective-from').value = todayIso();
    qs('settings-exit').value = settings.exit_amount ?? 2000;
    qs('settings-threshold').value = settings.bonus_threshold ?? 40000;
    qs('settings-bonus').value = settings.bonus_amount ?? 500;
    qs('settings-other-rate').value = settings.other_rate_percent ?? 3;
    qs('settings-admin-select').innerHTML = ['<option value="">—</option>', ...payrollState.admins.map(admin => `<option value="${admin.id}">${admin.full_name}</option>`)].join('');
    if (settings.responsible_admin_user_id) qs('settings-admin-select').value = String(settings.responsible_admin_user_id);
    const existing = new Map((settings.category_rates || []).map(item => [item.category_id, item.rate_percent]));
    qs('settings-category-rates').innerHTML = payrollState.categoryCatalog.map(category => `
        <label class="settings-rate-card">
            <span class="settings-rate-name">${escapeHtml(category.name)}</span>
            <input
                type="number"
                min="0"
                step="0.01"
                data-category-rate-id="${escapeHtml(category.id)}"
                data-category-rate-name="${escapeHtml(category.name)}"
                value="${existing.has(category.id) ? existing.get(category.id) : ''}"
                placeholder="%"
            >
        </label>
    `).join('');
}

function renderShiftCalendar() {
    const card = qs('admin-shifts-card');
    const grid = qs('shift-calendar-grid');
    const monthInput = qs('shift-month-input');
    if (!card || !grid || !monthInput) return;
    if (!isAdminRole()) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    const days = payrollState.shiftDays || [];
    const month = monthInput.value || monthIso();
    if (!days.length) {
        grid.innerHTML = `<div class="shift-calendar-empty muted-text">В ${monthLabel(month)} смен пока нет.</div>`;
        return;
    }

    const [year, mon] = month.split('-').map(Number);
    const firstWeekday = (new Date(year, mon - 1, 1).getDay() + 6) % 7;
    const cells = [];
    for (let i = 0; i < firstWeekday; i += 1) {
        cells.push('<div class="shift-calendar-cell shift-calendar-cell--empty" aria-hidden="true"></div>');
    }

    const today = todayIso();
    days.forEach(day => {
        const dayNumber = Number(String(day.date).slice(8, 10));
        const shiftCount = (day.shifts || []).length;
        const shiftCards = shiftCount
            ? day.shifts.map(shift => `
                <div class="shift-calendar-entry ${shift.is_closed ? 'closed' : 'open'}">
                    <div class="shift-calendar-entry-head">
                        <strong>${escapeHtml(shift.employee_name)}</strong>
                        <span class="payroll-chip ${shift.is_closed ? 'green' : 'orange'}">${shift.is_closed ? 'Закрыта' : 'Открыта'}</span>
                    </div>
                    <div class="shift-calendar-entry-meta">${formatMoney(shift.gross_salary_amount)}</div>
                    <div class="shift-calendar-entry-actions">
                        ${!shift.is_closed ? `<button type="button" class="btn secondary btn-inline" onclick="closeAdminShift(${shift.id})">Закрыть</button>` : ''}
                        <button type="button" class="btn danger btn-inline" onclick="deleteShift(${shift.id})">Убрать</button>
                    </div>
                </div>
            `).join('')
            : '<div class="shift-calendar-empty-day">Смен нет</div>';

        cells.push(`
            <article class="shift-calendar-cell ${day.date === today ? 'shift-calendar-cell--today' : ''}">
                <div class="shift-calendar-cell-head">
                    <div class="shift-calendar-day-meta">
                        <strong>${dayNumber}</strong>
                        <span>${formatDateRu(day.date)}</span>
                    </div>
                    <div class="shift-calendar-head-actions">
                        <span class="shift-calendar-count">${shiftCount}</span>
                        <button type="button" class="shift-calendar-add-btn" onclick="openShiftModal('${day.date}')" aria-label="Назначить смену на ${formatDateRu(day.date)}">+</button>
                    </div>
                </div>
                <div class="shift-calendar-cell-body">${shiftCards}</div>
            </article>
        `);
    });

    grid.innerHTML = cells.join('');
}

function renderTemplates() {
    const card = qs('expenses-card');
    if (!isAdminRole()) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    qs('expense-template-tbody').innerHTML = (payrollState.templates || []).map(template => `
        <article class="expense-template-card ${template.is_active ? '' : 'inactive'}">
            <div class="expense-template-card-head">
                <div>
                    <h4>${escapeHtml(template.name)}</h4>
                    <p class="muted-text">${template.amount_type === 'static' ? 'Статический шаблон' : 'Динамический шаблон'}</p>
                </div>
                <span class="status-chip ${template.is_active ? 'green' : 'grey'}">${template.is_active ? 'Активен' : 'Отключен'}</span>
            </div>
            <div class="expense-template-meta">
                <span>По умолчанию сотруднику: <strong>${template.assign_to_employee_by_default ? 'Да' : 'Нет'}</strong></span>
                <span>Сумма по умолчанию: <strong>${template.default_amount != null ? formatMoney(template.default_amount) : '—'}</strong></span>
            </div>
            <div class="expense-template-actions">
                <button type="button" class="btn danger btn-inline" onclick="deleteExpenseTemplate(${template.id})">Удалить</button>
            </div>
        </article>
    `).join('') || '<div class="empty-text">Шаблонов расходов пока нет.</div>';
}


function renderExpenses() {
    const employeeOptions = ['<option value="">Без привязки</option>', ...payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`)].join('');
    qs('expense-entry-tbody').innerHTML = (payrollState.expenses || []).map(entry => `
        <article class="expense-entry-card ${entry.is_manual ? 'manual' : ''}">
            <div class="expense-entry-head">
                <div>
                    <h4>${escapeHtml(entry.name || entry.template_name || 'Расход')}</h4>
                    <p class="muted-text">${entry.is_manual ? 'Свободный расход без шаблона' : `${entry.amount_type === 'static' ? 'Статический расход' : 'Динамический расход'} · месяц ${escapeHtml(entry.month_start || '')}`}</p>
                </div>
                <span class="status-chip ${entry.is_paid ? 'green' : 'orange'}">${entry.is_paid ? 'Оплачен' : 'Не оплачен'}</span>
            </div>
            <div class="expense-entry-form">
                <label>
                    Сумма
                    <input type="number" min="0" step="0.01" data-expense-amount="${entry.id}" value="${entry.amount}">
                </label>
                <label>
                    Сотрудник
                    <select data-expense-employee="${entry.id}">${employeeOptions}</select>
                </label>
                <label class="checkbox-like expense-checkbox-card expense-checkbox-card--inline">
                    <input type="checkbox" data-expense-paid="${entry.id}" ${entry.is_paid ? 'checked' : ''}>
                    Уже оплачен
                </label>
                <label class="checkbox-like expense-checkbox-card expense-checkbox-card--inline">
                    <input type="checkbox" data-expense-apply="${entry.id}" ${entry.apply_to_employee_salary ? 'checked' : ''}>
                    Вычитать из зарплаты сотрудника
                </label>
                <label class="expense-comment-field expense-entry-comment">
                    Комментарий
                    <textarea rows="3" data-expense-comment="${entry.id}" placeholder="Комментарий к расходу">${escapeHtml(entry.comment || '')}</textarea>
                </label>
            </div>
            <div class="expense-entry-actions">
                <button type="button" class="btn secondary btn-inline" onclick="saveExpenseEntry(${entry.id})">Сохранить расход</button>
                ${entry.is_manual ? `<button type="button" class="btn danger btn-inline" onclick="deleteExpenseEntry(${entry.id})">Удалить</button>` : ''}
            </div>
        </article>
    `).join('') || '<div class="empty-text">Нет расходов за выбранный месяц.</div>';
    (payrollState.expenses || []).forEach(entry => {
        const select = document.querySelector(`[data-expense-employee="${entry.id}"]`);
        if (select && entry.assigned_employee_user_id) select.value = String(entry.assigned_employee_user_id);
    });
}


function describeAuditLog(log) {
    const actor = log.actor_name || 'Система';
    const when = formatTimeRu(log.created_at);
    const details = log.details || {};
    const shiftDate = details.shift_date ? formatDateRu(details.shift_date) : '';

    if (log.entity_type === 'work_shift') {
        const employeeName = payrollState.employees.find(item => Number(item.id) === Number(details.employee_user_id))?.full_name || 'сотруднику';
        if (log.action_type === 'create' || log.action_type === 'restore') {
            return `${when} · ${actor} назначил смену ${employeeName} на ${shiftDate}.`;
        }
        if (log.action_type === 'close') {
            return `${when} · ${actor} закрыл смену ${employeeName} за ${shiftDate}.`;
        }
        if (log.action_type === 'auto_close') {
            return `${when} · система автоматически закрыла смену ${employeeName} за ${shiftDate}.`;
        }
        if (log.action_type === 'delete') {
            return `${when} · ${actor} убрал смену ${employeeName} из активного календаря за ${shiftDate}.`;
        }
    }

    if (log.entity_type === 'payroll_settings' && log.action_type === 'create_version') {
        return `${when} · ${actor} сохранил новую версию правил зарплаты с датой вступления ${formatDateRu(details.effective_from)}.`;
    }

    if (log.entity_type === 'expense_template') {
        if (log.action_type === 'create') {
            return `${when} · ${actor} создал шаблон расхода «${details.name || 'Без названия'}».`;
        }
        if (log.action_type === 'update') {
            const name = details.after?.name || details.before?.name || 'Без названия';
            return `${when} · ${actor} обновил шаблон расхода «${name}».`;
        }
        if (log.action_type === 'deactivate') {
            return `${when} · ${actor} отключил шаблон расхода «${details.name || 'Без названия'}».`;
        }
    }

    if (log.entity_type === 'monthly_expense' && log.action_type === 'update') {
        return `${when} · ${actor} изменил ежемесячный расход по точке.`;
    }

    return `${when} · ${actor} выполнил действие ${log.action_type}.`;
}

function groupAuditByDate(logs) {
    const groups = new Map();
    [...logs]
        .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
        .forEach(log => {
            const dayKey = String(log.created_at || '').slice(0, 10) || 'Без даты';
            if (!groups.has(dayKey)) groups.set(dayKey, []);
            groups.get(dayKey).push(log);
        });
    return [...groups.entries()];
}

function auditLogMatchesFilters(log) {
    const dateFilter = qs('audit-date-filter')?.value || '';
    const employeeFilter = qs('audit-employee-filter')?.value || '';
    if (dateFilter && String(log.created_at || '').slice(0, 10) !== dateFilter) {
        return false;
    }
    if (!employeeFilter) return true;
    const employeeId = String(employeeFilter);
    const people = [...(payrollState.employees || []), ...(payrollState.admins || [])];
    const selectedPerson = people.find(item => String(item.id) === employeeId);
    const selectedName = selectedPerson?.full_name || '';
    const affectedEmployeeId = log.details?.employee_user_id != null ? String(log.details.employee_user_id) : '';
    const actorId = log.actor_user_id != null ? String(log.actor_user_id) : '';
    const actorName = log.actor_name || '';
    const message = describeAuditLog(log);
    return actorId === employeeId
        || affectedEmployeeId === employeeId
        || (selectedName && actorName === selectedName)
        || (selectedName && message.includes(selectedName));
}

function renderAudit() {
    const card = qs('audit-card');
    if (!isAdminRole()) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    const filtered = (payrollState.audit || []).filter(auditLogMatchesFilters);
    const grouped = groupAuditByDate(filtered);
    qs('audit-log-list').innerHTML = grouped.length ? grouped.map(([day, logs]) => `
        <details class="audit-day-card">
            <summary>
                <span>${formatDateRu(day)}</span>
                <span class="audit-day-count">${logs.length}</span>
            </summary>
            <div class="audit-day-body">
                ${logs.map(log => `<article class="audit-log-row"><span class="audit-log-message">${escapeHtml(describeAuditLog(log))}</span></article>`).join('')}
            </div>
        </details>
    `).join('') : '<div class="empty-text">По выбранным фильтрам журнал пуст.</div>';
}

async function loadSetupForLocation() {
    const location = selectedLocation();
    if (!location) return;
    const setup = await api(`/api/payroll/settings?location=${encodeURIComponent(location)}`);
    payrollState.settings = setup.settings;
    payrollState.employees = setup.employees || [];
    payrollState.admins = setup.admins || [];
    const categories = await api(`/api/payroll/categories?location=${encodeURIComponent(location)}`);
    payrollState.categoryCatalog = categories.categories || [];
    renderUsersForLocation();
    renderSettings();
    await Promise.all([
        loadShiftCalendar(),
        loadExpenseTemplatesAndEntries(),
        loadAudit(),
    ]);
}

async function loadSummary() {
    const location = selectedLocation();
    const dateFrom = qs('payroll-date-from').value;
    const dateTo = qs('payroll-date-to').value;
    if (!location || !dateFrom || !dateTo) return;
    showStatus('Загружаем расчёт зарплаты...', 'loading');
    try {
        const employeeId = selectedEmployeeId();
        const employeeQuery = employeeId ? `&employee_user_id=${employeeId}` : '';
        const summary = await api(`/api/payroll/employee-summary?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}${employeeQuery}`);
        renderSummary(summary);
        if (isAdminRole()) {
            const managerSummary = await api(`/api/payroll/manager-summary?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}`);
            renderManagerSummary(managerSummary);
            renderPayrollCategoryTable(managerSummary.categories || []);
        }
        showStatus('Данные обновлены.', 'success');
        setTimeout(hideStatus, 1500);
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось загрузить зарплату.', 'error');
    }
}

async function loadShiftCalendar() {
    if (!isAdminRole() || !qs('shift-month-input') || !qs('shift-calendar-grid')) return;
    const location = selectedLocation();
    const month = selectedMonthStart('shift-month-input');
    const [year, mon] = month.split('-').map(Number);
    const dateFrom = `${year}-${String(mon).padStart(2, '0')}-01`;
    const dateTo = new Date(year, mon, 0).toISOString().slice(0, 10);
    const payload = await api(`/api/payroll/shifts?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}`);
    const daysByDate = new Map((payload.days || []).map(day => [day.date, day]));
    const rendered = [];
    for (let d = 1; d <= Number(dateTo.slice(8, 10)); d += 1) {
        const iso = `${year}-${String(mon).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        rendered.push(daysByDate.get(iso) || { date: iso, shifts: [] });
    }
    payrollState.shiftDays = rendered;
    renderShiftCalendar();
}

async function loadExpenseTemplatesAndEntries() {
    if (!isAdminRole()) return;
    const location = selectedLocation();
    const month = selectedMonthStart('expenses-month-input');
    const templates = await api(`/api/payroll/expense-templates?location=${encodeURIComponent(location)}`);
    payrollState.templates = templates.templates || [];
    renderTemplates();
    const expenses = await api(`/api/payroll/expenses?location=${encodeURIComponent(location)}&month=${month}`);
    payrollState.expenses = expenses.entries || [];
    renderExpenses();
    const manualExpenseEmployee = qs('manual-expense-employee');
    if (manualExpenseEmployee) {
        manualExpenseEmployee.innerHTML = ['<option value="">Без привязки</option>', ...payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`)].join('');
    }
}

async function loadAudit() {
    if (!isAdminRole()) return;
    const location = selectedLocation();
    const payload = await api(`/api/payroll/audit?location=${encodeURIComponent(location)}&limit=100`);
    payrollState.audit = payload.logs || [];
    renderAudit();
}

async function saveSettings() {
    const categoryRates = [...document.querySelectorAll('[data-category-rate-id]')]
        .map(input => ({
            category_id: input.dataset.categoryRateId,
            category_name: input.dataset.categoryRateName,
            rate_percent: input.value === '' ? null : Number(input.value),
        }))
        .filter(item => item.rate_percent !== null && Number.isFinite(item.rate_percent));
    const payload = {
        location: selectedLocation(),
        effective_from: qs('settings-effective-from').value,
        exit_amount: Number(qs('settings-exit').value || 0),
        bonus_threshold: Number(qs('settings-threshold').value || 0),
        bonus_amount: Number(qs('settings-bonus').value || 0),
        other_rate_percent: Number(qs('settings-other-rate').value || 0),
        responsible_admin_user_id: qs('settings-admin-select').value ? Number(qs('settings-admin-select').value) : null,
        category_rates: categoryRates,
    };
    showStatus('Сохраняем новую версию правил...', 'loading');
    try {
        await api('/api/payroll/settings', { method: 'PUT', body: JSON.stringify(payload) });
        await loadSetupForLocation();
        await loadSummary();
        showStatus('Версия правил сохранена.', 'success');
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось сохранить правила.', 'error');
    }
}

function openShiftModal(dateValue = '') {
    if (!isAdminRole()) return;
    const modal = qs('shift-modal');
    qs('shift-modal-date-input').value = dateValue || qs('shift-date-input').value || todayIso();
    const sourceEmployee = qs('shift-employee-select')?.value || '';
    if (sourceEmployee) qs('shift-modal-employee-select').value = sourceEmployee;
    modal.classList.remove('hidden');
    document.body.classList.add('modal-open');
}

function closeShiftModal() {
    qs('shift-modal').classList.add('hidden');
    document.body.classList.remove('modal-open');
}

window.openShiftModal = openShiftModal;

async function saveShiftFromModal() {
    const button = qs('shift-modal-save-btn');
    const payload = {
        location: selectedLocation(),
        shift_date: qs('shift-modal-date-input').value,
        employee_user_id: Number(qs('shift-modal-employee-select').value),
    };
    setButtonLoading(button, true, 'Назначаем...');
    showStatus('Сохраняем смену...', 'loading');
    try {
        await api('/api/payroll/shifts', { method: 'POST', body: JSON.stringify(payload) });
        qs('shift-date-input').value = payload.shift_date;
        qs('shift-employee-select').value = String(payload.employee_user_id);
        await loadShiftCalendar();
        await loadSummary();
        await loadAudit();
        closeShiftModal();
        showStatus('Смена сохранена.', 'success');
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось сохранить смену.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}

async function addShift() {
    const button = qs('add-shift-btn');
    const payload = {
        location: selectedLocation(),
        shift_date: qs('shift-date-input').value,
        employee_user_id: Number(qs('shift-employee-select').value),
    };
    setButtonLoading(button, true, 'Назначаем...');
    showStatus('Сохраняем смену...', 'loading');
    try {
        await api('/api/payroll/shifts', { method: 'POST', body: JSON.stringify(payload) });
        await loadShiftCalendar();
        await loadSummary();
        await loadAudit();
        showStatus('Смена сохранена.', 'success');
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось сохранить смену.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}

window.deleteShift = async function deleteShift(id) {
    if (!confirm('Убрать эту смену из активного календаря?')) return;
    try {
        await api(`/api/payroll/shifts/${id}`, { method: 'DELETE' });
        await loadShiftCalendar();
        await loadSummary();
        await loadAudit();
        showStatus('Смена убрана.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось убрать смену.', 'error');
    }
};

window.closeOwnShift = async function closeOwnShift(id) {
    try {
        await api(`/api/payroll/shifts/${id}/close`, { method: 'POST' });
        await loadSummary();
        if (isAdminRole()) await loadShiftCalendar();
        showStatus('Смена закрыта.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось закрыть смену.', 'error');
    }
};

window.closeAdminShift = window.closeOwnShift;

async function createExpenseTemplate() {
    const payload = {
        location: selectedLocation(),
        name: qs('expense-template-name').value,
        amount_type: qs('expense-template-type').value,
        default_amount: qs('expense-template-default').value ? Number(qs('expense-template-default').value) : null,
        assign_to_employee_by_default: qs('expense-template-employee').checked,
    };
    try {
        await api('/api/payroll/expense-templates', { method: 'POST', body: JSON.stringify(payload) });
        qs('expense-template-name').value = '';
        qs('expense-template-default').value = '';
        qs('expense-template-employee').checked = false;
        await loadExpenseTemplatesAndEntries();
        await loadAudit();
        showStatus('Шаблон расхода добавлен.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось добавить шаблон.', 'error');
    }
}

window.deleteExpenseTemplate = async function deleteExpenseTemplate(id) {
    if (!confirm('Удалить шаблон? История уже созданных расходов сохранится.')) return;
    try {
        await api(`/api/payroll/expense-templates/${id}`, { method: 'DELETE' });
        await loadExpenseTemplatesAndEntries();
        await loadAudit();
        showStatus('Шаблон удалён.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось удалить шаблон.', 'error');
    }
};

window.saveExpenseEntry = async function saveExpenseEntry(id) {
    const amount = Number(document.querySelector(`[data-expense-amount="${id}"]`).value || 0);
    const isPaid = document.querySelector(`[data-expense-paid="${id}"]`).checked;
    const employeeValue = document.querySelector(`[data-expense-employee="${id}"]`).value;
    const applyToEmployeeSalary = document.querySelector(`[data-expense-apply="${id}"]`).checked;
    const comment = document.querySelector(`[data-expense-comment="${id}"]`)?.value || '';
    const payload = {
        amount,
        is_paid: isPaid,
        assigned_employee_user_id: employeeValue ? Number(employeeValue) : null,
        apply_to_employee_salary: applyToEmployeeSalary,
        comment,
    };
    try {
        await api(`/api/payroll/expenses/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        await loadExpenseTemplatesAndEntries();
        await loadSummary();
        await loadAudit();
        showStatus('Расход сохранён.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось сохранить расход.', 'error');
    }
};

async function createManualExpense() {
    const payload = {
        location: selectedLocation(),
        month_start: selectedMonthStart('expenses-month-input'),
        name: qs('manual-expense-name').value.trim(),
        amount: Number(qs('manual-expense-amount').value || 0),
        assigned_employee_user_id: qs('manual-expense-employee').value ? Number(qs('manual-expense-employee').value) : null,
        is_paid: qs('manual-expense-paid').checked,
        apply_to_employee_salary: qs('manual-expense-apply').checked,
        comment: qs('manual-expense-comment').value.trim(),
    };
    if (!payload.name) {
        showStatus('Укажи название свободного расхода.', 'error');
        return;
    }
    try {
        await api('/api/payroll/expenses/manual', { method: 'POST', body: JSON.stringify(payload) });
        qs('manual-expense-name').value = '';
        qs('manual-expense-amount').value = '';
        qs('manual-expense-employee').value = '';
        qs('manual-expense-paid').checked = false;
        qs('manual-expense-apply').checked = false;
        qs('manual-expense-comment').value = '';
        await loadExpenseTemplatesAndEntries();
        await loadSummary();
        await loadAudit();
        showStatus('Свободный расход добавлен.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось добавить свободный расход.', 'error');
    }
}

window.deleteExpenseEntry = async function deleteExpenseEntry(id) {
    if (!confirm('Удалить этот свободный расход?')) return;
    try {
        await api(`/api/payroll/expenses/${id}`, { method: 'DELETE' });
        await loadExpenseTemplatesAndEntries();
        await loadSummary();
        await loadAudit();
        showStatus('Свободный расход удалён.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось удалить свободный расход.', 'error');
    }
};

function toggleExpenseTemplates() {
    const list = qs('expense-template-tbody');
    const button = qs('expense-templates-toggle-btn');
    const hidden = list.classList.toggle('hidden');
    button.textContent = hidden ? 'Развернуть шаблоны' : 'Свернуть шаблоны';
}

function clearAuditFilters() {
    qs('audit-date-filter').value = '';
    qs('audit-employee-filter').value = '';
    renderAudit();
}

async function exportPayroll() {
    const location = selectedLocation();
    const dateFrom = qs('payroll-date-from').value;
    const dateTo = qs('payroll-date-to').value;
    const employeeId = selectedEmployeeId();
    const employeeQuery = employeeId ? `&employee_user_id=${employeeId}` : '';
    window.location.href = `/api/payroll/export-xlsx?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}${employeeQuery}`;
}

async function logout() {
    await api('/api/logout', { method: 'POST' });
    window.location.href = '/login';
}

async function bootstrap() {
    setDefaultDates();
    initializeCollapseSections();
    try {
        const access = await api('/api/payroll/access');
        payrollState.locations = access.locations || [];
        renderLocations();
        await loadSetupForLocation();
        await loadSummary();
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось загрузить страницу зарплаты.', 'error');
    }
}

qs('payroll-load-btn').addEventListener('click', loadSummary);
qs('payroll-export-btn').addEventListener('click', exportPayroll);
qs('payroll-location-select').addEventListener('change', async () => {
    await loadSetupForLocation();
    await loadSummary();
});
qs('payroll-employee-select')?.addEventListener('change', loadSummary);
qs('save-settings-btn')?.addEventListener('click', saveSettings);
qs('add-shift-btn')?.addEventListener('click', addShift);
qs('shift-month-input')?.addEventListener('change', loadShiftCalendar);
qs('expenses-month-input')?.addEventListener('change', loadExpenseTemplatesAndEntries);
qs('create-expense-template-btn')?.addEventListener('click', createExpenseTemplate);
qs('create-manual-expense-btn')?.addEventListener('click', createManualExpense);
qs('logout-btn').addEventListener('click', logout);
qs('payroll-category-search')?.addEventListener('input', applyPayrollCategoryFiltersFromUi);
qs('payroll-category-view')?.addEventListener('change', applyPayrollCategoryFiltersFromUi);
qs('payroll-category-sort')?.addEventListener('change', applyPayrollCategoryFiltersFromUi);
qs('shift-modal-save-btn')?.addEventListener('click', saveShiftFromModal);
qs('shift-modal-close-btn')?.addEventListener('click', closeShiftModal);
qs('shift-modal-cancel-btn')?.addEventListener('click', closeShiftModal);
qs('shift-modal')?.addEventListener('click', (event) => {
    if (event.target === qs('shift-modal')) closeShiftModal();
});
qs('expense-templates-toggle-btn')?.addEventListener('click', toggleExpenseTemplates);
qs('audit-toggle-btn')?.addEventListener('click', () => {
    const list = qs('audit-log-list');
    const button = qs('audit-toggle-btn');
    const hidden = list.classList.toggle('hidden');
    button.textContent = hidden ? 'Развернуть журнал' : 'Свернуть журнал';
});
qs('audit-date-filter')?.addEventListener('change', renderAudit);
qs('audit-employee-filter')?.addEventListener('change', renderAudit);
qs('audit-clear-filters-btn')?.addEventListener('click', clearAuditFilters);
document.querySelectorAll('.payroll-collapse').forEach((details) => {
    details.addEventListener('toggle', () => syncCollapseToggleText(details));
});

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !qs('shift-modal')?.classList.contains('hidden')) {
        closeShiftModal();
    }
});

window.addEventListener('resize', () => {
    if (payrollState.summary) {
        renderSummary(payrollState.summary);
    }
});

bootstrap();
