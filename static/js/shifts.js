const shiftsState = {
    user: window.currentUser || {},
    locations: [],
    employees: [],
    shiftDays: [],
    payrollDays: [],
    filterMode: 'month',
    selectedDate: '',
    payrollDaysLoaded: false,
    payrollDaysLoading: false,
    payrollDaysContextKey: '',
};

const MONTH_OPTIONS = [
    { value: '01', label: 'Январь' },
    { value: '02', label: 'Февраль' },
    { value: '03', label: 'Март' },
    { value: '04', label: 'Апрель' },
    { value: '05', label: 'Май' },
    { value: '06', label: 'Июнь' },
    { value: '07', label: 'Июль' },
    { value: '08', label: 'Август' },
    { value: '09', label: 'Сентябрь' },
    { value: '10', label: 'Октябрь' },
    { value: '11', label: 'Ноябрь' },
    { value: '12', label: 'Декабрь' },
];

function qs(id) {
    return document.getElementById(id);
}

function isAdminRole() {
    return ['admin', 'superadmin'].includes(shiftsState.user.role);
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

function todayIso() {
    return new Date().toISOString().slice(0, 10);
}

function monthIso() {
    return todayIso().slice(0, 7);
}

function currentYear() {
    return Number(todayIso().slice(0, 4));
}

function selectedYear() {
    const raw = String(qs('shift-year-input')?.value || currentYear()).trim();
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed >= 2020 ? parsed : currentYear();
}

function selectedMonthNumber() {
    const raw = qs('shift-month-select')?.value || monthIso().slice(5, 7);
    return /^[0-1]\d$/.test(raw) ? Number(raw) : Number(monthIso().slice(5, 7));
}

function selectedMonthValue() {
    return `${selectedYear()}-${String(selectedMonthNumber()).padStart(2, '0')}`;
}

function monthLabel(monthValue) {
    const [year, month] = String(monthValue || monthIso()).split('-').map(Number);
    if (!year || !month) return '';
    return new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(new Date(year, month - 1, 1));
}

function isMobileCompactMode() {
    return window.matchMedia('(max-width: 640px)').matches;
}

function initialsFromName(value) {
    const parts = String(value || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '—';
    return parts.slice(0, 2).map(part => part[0]?.toUpperCase() || '').join('');
}

function formatDateRu(iso) {
    if (!iso) return '';
    const date = new Date(`${iso}T00:00:00`);
    return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }).format(date);
}

function showStatus(message, tone = 'loading') {
    const box = qs('shift-status');
    if (!box) return;
    box.textContent = message;
    box.className = `inventory-status ${tone}`;
    box.classList.remove('hidden');
}

function hideStatus() {
    const box = qs('shift-status');
    if (!box) return;
    box.classList.add('hidden');
    box.textContent = '';
    box.className = 'inventory-status hidden';
}

function showScopedStatus(id, message, tone = 'loading') {
    const box = qs(id);
    if (!box) return;
    if (box._hideTimer) {
        clearTimeout(box._hideTimer);
        box._hideTimer = null;
    }
    box.textContent = message;
    box.className = `inventory-status ${tone}`;
    box.classList.remove('hidden');
}

function hideScopedStatus(id) {
    const box = qs(id);
    if (!box) return;
    if (box._hideTimer) {
        clearTimeout(box._hideTimer);
        box._hideTimer = null;
    }
    box.classList.add('hidden');
    box.textContent = '';
    box.className = 'inventory-status hidden';
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

function selectedLocation() {
    return qs('shift-location-select')?.value || '';
}

function normalizeLocationList(locations = []) {
    const result = [];
    const seen = new Set();
    (locations || []).forEach((location) => {
        const value = String(location ?? '').trim();
        if (!value || seen.has(value)) return;
        seen.add(value);
        result.push(value);
    });
    return result;
}

function currentLocationOptionsFromDom() {
    return normalizeLocationList(
        [...(qs('shift-location-select')?.options || [])].map((option) => option.value || option.textContent || '')
    );
}

function fallbackLocations() {
    return normalizeLocationList([
        ...currentLocationOptionsFromDom(),
        ...(Array.isArray(shiftsState.user.accessible_locations) ? shiftsState.user.accessible_locations : []),
        shiftsState.user.default_location,
        shiftsState.user.location,
    ]);
}

function setDefaults() {
    if (qs('shift-year-input')) qs('shift-year-input').value = String(currentYear());
    if (qs('shift-month-select')) qs('shift-month-select').value = monthIso().slice(5, 7);
}

function renderMonthOptions() {
    const select = qs('shift-month-select');
    if (!select) return;
    select.innerHTML = MONTH_OPTIONS.map(item => `<option value="${item.value}">${item.label}</option>`).join('');
}

function renderLocations() {
    const select = qs('shift-location-select');
    if (!select) return;
    const locations = normalizeLocationList(shiftsState.locations.length ? shiftsState.locations : fallbackLocations());
    shiftsState.locations = locations;
    select.innerHTML = locations.map(location => `<option value="${location}">${location}</option>`).join('');
    const defaultLocation = shiftsState.user.default_location || shiftsState.user.location || locations[0] || '';
    if (defaultLocation && locations.includes(defaultLocation)) {
        select.value = defaultLocation;
    } else if (locations[0]) {
        select.value = locations[0];
    }
}

function renderEmployees() {
    const options = shiftsState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`).join('');
    if (qs('shift-modal-employee-select')) qs('shift-modal-employee-select').innerHTML = options;
}

function syncCollapseToggleText(details) {
    if (!details) return;
    const text = details.querySelector('.payroll-collapse-btn');
    if (!text) return;
    text.textContent = details.open ? 'Свернуть' : 'Развернуть';
}

function monthDateRange() {
    const year = selectedYear();
    const mon = selectedMonthNumber();
    const month = `${year}-${String(mon).padStart(2, '0')}`;
    const dateFrom = `${month}-01`;
    const dateTo = new Date(year, mon, 0).toISOString().slice(0, 10);
    return { month, dateFrom, dateTo };
}

function isShiftDaySelectable(day) {
    const shifts = Array.isArray(day?.shifts) ? day.shifts : [];
    if (!shifts.length) return false;
    return day.date <= todayIso() || shifts.some(shift => shift.is_closed);
}

function renderShiftFilterControls() {
    const isDateMode = shiftsState.filterMode === 'date';
    qs('shift-view-all-btn')?.classList.toggle('active', !isDateMode);
    qs('shift-view-date-btn')?.classList.toggle('active', isDateMode);
    qs('shift-date-picker-wrap')?.classList.toggle('hidden', !isDateMode);

    const caption = qs('shift-filter-caption');
    if (!caption) return;
    if (!isDateMode) {
        caption.textContent = 'Показаны все даты за выбранный месяц.';
        return;
    }
    caption.textContent = shiftsState.selectedDate
        ? `Показана дата ${formatDateRu(shiftsState.selectedDate)}.`
        : 'Выбери конкретную дату в маленьком календаре ниже.';
}

function setPayrollDaysPlaceholder(message = 'Открой раздел «Смены», чтобы загрузить детализацию по дням.') {
    const container = qs('shift-payroll-days-container');
    if (!container) return;
    container.innerHTML = `<div class="muted-text">${escapeHtml(message)}</div>`;
}

function isPayrollSectionOpen() {
    return !!qs('shift-payroll-section')?.open;
}

function currentPayrollDaysContextKey() {
    const location = selectedLocation();
    const { dateFrom, dateTo } = monthDateRange();
    return [location, dateFrom, dateTo].join('|');
}

async function ensurePayrollDaysLoaded(force = false, options = {}) {
    const { showLoading = false } = options;
    const contextKey = currentPayrollDaysContextKey();
    if (!force && shiftsState.payrollDaysLoaded && shiftsState.payrollDaysContextKey === contextKey) return;
    if (shiftsState.payrollDaysLoading) return;
    shiftsState.payrollDaysLoading = true;
    if (showLoading) {
        setPayrollDaysPlaceholder('Загружаем детализацию смен...');
    }
    try {
        await loadPayrollDays();
        shiftsState.payrollDaysLoaded = true;
        shiftsState.payrollDaysContextKey = contextKey;
    } finally {
        shiftsState.payrollDaysLoading = false;
    }
}

function renderPayrollDays() {
    const container = qs('shift-payroll-days-container');
    if (!container) return;
    let days = [...(shiftsState.payrollDays || [])];
    if (shiftsState.filterMode === 'date') {
        days = shiftsState.selectedDate ? days.filter(day => day.shift_date === shiftsState.selectedDate) : [];
    }
    if (!days.length) {
        container.innerHTML = shiftsState.filterMode === 'date'
            ? '<div class="muted-text">За выбранную дату смен не найдено.</div>'
            : '<div class="muted-text">За выбранный месяц смен не найдено.</div>';
        return;
    }
    container.innerHTML = days.map(day => `
        <article class="payroll-day-card">
            <div class="payroll-day-header">
                <div>
                    <strong>${escapeHtml(day.shift_date || '')}</strong>
                    ${day.employee_name ? `<div class="muted-text">${escapeHtml(day.employee_name)}</div>` : ''}
                </div>
                <span class="payroll-chip ${day.is_closed ? 'green' : 'orange'}">${day.is_closed ? 'Закрыта' : 'Открыта'}</span>
            </div>
            <div class="payroll-day-grid payroll-day-grid--emphasis">
                <div><span class="summary-label">Выручка</span><strong>${formatMoney(day.gross_sales_amount)}</strong></div>
                <div><span class="summary-label">Возвраты</span><strong>${formatMoney(day.return_amount)}</strong></div>
                <div><span class="summary-label">Выручка после возвратов</span><strong>${formatMoney(day.net_sales_amount)}</strong></div>
                <div><span class="summary-label">Выход</span><strong>${formatMoney(day.exit_amount)}</strong></div>
                <div><span class="summary-label">Бонус к выходу</span><strong>${formatMoney(day.bonus_amount)}</strong></div>
                <div><span class="summary-label">Бонус по категориям</span><strong>${formatMoney(day.category_earnings_total)}</strong></div>
                <div><span class="summary-label">Итого</span><strong>${formatMoney(day.gross_salary_amount)}</strong></div>
            </div>
            ${(day.id && !day.is_closed) ? `
                <div class="shift-detail-actions">
                    <button type="button" class="btn secondary btn-inline" onclick="closeAdminShift(${day.id})">Закрыть</button>
                    <button type="button" class="btn danger btn-inline" onclick="deleteShift(${day.id})">Убрать</button>
                </div>
            ` : ''}
        </article>
    `).join('');
}

function renderShiftDatePicker() {
    const grid = qs('shift-date-picker-grid');
    const title = qs('shift-date-picker-title');
    if (!grid || !title) return;

    const { month } = monthDateRange();
    const [year, mon] = month.split('-').map(Number);
    title.textContent = monthLabel(month);
    const totalDays = new Date(year, mon, 0).getDate();
    const firstWeekday = (new Date(year, mon - 1, 1).getDay() + 6) % 7;
    const daysByDate = new Map((shiftsState.shiftDays || []).map(day => [day.date, day]));
    const cells = [];

    for (let i = 0; i < firstWeekday; i += 1) {
        cells.push('<span class="shift-date-picker-cell shift-date-picker-cell--empty" aria-hidden="true"></span>');
    }

    for (let d = 1; d <= totalDays; d += 1) {
        const iso = `${year}-${String(mon).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const day = daysByDate.get(iso) || { date: iso, shifts: [] };
        const selectable = isShiftDaySelectable(day);
        const active = shiftsState.selectedDate === iso;
        cells.push(`
            <button
                type="button"
                class="shift-date-picker-cell${selectable ? '' : ' is-disabled'}${active ? ' is-active' : ''}"
                data-shift-picker-date="${iso}"
                ${selectable ? '' : 'disabled'}
                title="${selectable ? formatDateRu(iso) : 'Недоступно для выбора'}"
            >
                ${d}
            </button>
        `);
    }

    grid.innerHTML = cells.join('');
}

function toggleShiftDatePicker(forceOpen = null) {
    const popover = qs('shift-date-picker-popover');
    if (!popover) return;
    const shouldOpen = forceOpen == null ? popover.classList.contains('hidden') : Boolean(forceOpen);
    popover.classList.toggle('hidden', !shouldOpen);
    if (shouldOpen) renderShiftDatePicker();
}

async function setShiftFilterMode(mode) {
    shiftsState.filterMode = mode === 'date' ? 'date' : 'month';
    if (shiftsState.filterMode === 'month') {
        shiftsState.selectedDate = '';
        toggleShiftDatePicker(false);
    } else if (!shiftsState.payrollDaysLoaded) {
        const payrollSection = qs('shift-payroll-section');
        if (payrollSection && !payrollSection.open) {
            payrollSection.open = true;
            syncCollapseToggleText(payrollSection);
        }
        try {
            await ensurePayrollDaysLoaded(false, { showLoading: true });
        } catch (error) {
            console.error(error);
            showStatus(error.message || 'Не удалось загрузить детализацию смен.', 'error');
        }
    }
    renderShiftFilterControls();
    renderPayrollDays();
}

async function selectShiftDate(dateValue) {
    shiftsState.selectedDate = dateValue;
    if (!shiftsState.payrollDaysLoaded) {
        try {
            await ensurePayrollDaysLoaded(false, { showLoading: true });
        } catch (error) {
            console.error(error);
            showStatus(error.message || 'Не удалось загрузить детализацию смен.', 'error');
        }
    }
    renderShiftFilterControls();
    renderShiftDatePicker();
    renderPayrollDays();
    toggleShiftDatePicker(false);
}

function renderShiftCalendar() {
    const grid = qs('shift-calendar-grid');
    if (!grid) return;

    const days = shiftsState.shiftDays || [];
    const { month } = monthDateRange();
    const [year, mon] = month.split('-').map(Number);
    const firstWeekday = (new Date(year, mon - 1, 1).getDay() + 6) % 7;
    const cells = [];
    for (let i = 0; i < firstWeekday; i += 1) {
        cells.push('<div class="shift-calendar-cell shift-calendar-cell--empty" aria-hidden="true"></div>');
    }

    if (!days.length) {
        grid.innerHTML = `<div class="shift-calendar-empty muted-text">В ${monthLabel(month)} смен пока нет.</div>`;
        return;
    }

    const today = todayIso();
    const adminMode = isAdminRole();

    days.forEach(day => {
        const dayNumber = Number(String(day.date).slice(8, 10));
        const shiftCount = (day.shifts || []).length;

        if (adminMode) {
            const mobileActionButtons = `<div class="shift-calendar-mobile-actions">
                <button
                    type="button"
                    class="shift-calendar-mobile-add-btn"
                    onclick="openShiftModal('${day.date}')"
                    aria-label="Назначить смену на ${formatDateRu(day.date)}"
                    title="Назначить смену на ${formatDateRu(day.date)}"
                >+</button>${day.shifts.map(shift => `
                    <button
                        type="button"
                        class="shift-calendar-mobile-remove-btn"
                        onclick="deleteShift(${shift.id})"
                        aria-label="Убрать смену ${escapeHtml(shift.employee_name)}"
                        title="Убрать смену ${escapeHtml(shift.employee_name)}"
                    >−</button>
                `).join('')}</div>`;
            const miniChips = shiftCount
                ? `<div class="shift-calendar-mini-list">${day.shifts.slice(0, 4).map(shift => `
                    <span class="shift-calendar-mini-chip ${shift.is_closed ? 'closed' : 'open'}" title="${escapeHtml(shift.employee_name)}">${escapeHtml(initialsFromName(shift.employee_name))}</span>
                `).join('')}${shiftCount > 4 ? `<span class="shift-calendar-mini-more">+${shiftCount - 4}</span>` : ''}</div>`
                : '<div class="shift-calendar-empty-day">Смен нет</div>';
            const shiftCards = shiftCount
                ? `${miniChips}${day.shifts.map(shift => `
                    <div class="shift-calendar-entry ${shift.is_closed ? 'closed' : 'open'}">
                        <div class="shift-calendar-entry-head">
                            <strong>${escapeHtml(shift.employee_name)}</strong>
                            <div class="shift-calendar-entry-head-actions">
                                <span class="payroll-chip ${shift.is_closed ? 'green' : 'orange'}">${shift.is_closed ? 'Закрыта' : 'Открыта'}</span>
                                <button
                                    type="button"
                                    class="shift-calendar-mobile-remove-btn shift-calendar-mobile-remove-btn--inline"
                                    onclick="deleteShift(${shift.id})"
                                    aria-label="Убрать смену ${escapeHtml(shift.employee_name)}"
                                    title="Убрать смену ${escapeHtml(shift.employee_name)}"
                                >−</button>
                            </div>
                        </div>
                        <div class="shift-calendar-entry-meta">${formatMoney(shift.gross_salary_amount)}</div>
                        <div class="shift-calendar-entry-actions">
                            ${!shift.is_closed ? `<button type="button" class="btn secondary btn-inline" onclick="closeAdminShift(${shift.id})">Закрыть</button>` : ''}
                            <button type="button" class="btn danger btn-inline" onclick="deleteShift(${shift.id})">Убрать</button>
                        </div>
                    </div>
                `).join('')}`
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
                    <div class="shift-calendar-cell-body">${shiftCards}${mobileActionButtons}</div>
                </article>
            `);
            return;
        }

        const shift = shiftCount ? day.shifts[0] : null;
        const selectable = isShiftDaySelectable(day);
        let body = '<div class="shift-calendar-empty-day">Смены нет</div>';
        if (shift) {
            const compactMode = isMobileCompactMode();
            const statusLabel = shift.is_closed ? 'Завершена' : (day.date > today ? 'Назначена' : (day.date === today ? 'Сегодня' : 'Открыта'));
            const statusClass = shift.is_closed ? 'closed' : (day.date > today ? 'planned' : 'open');
            const lines = [
                `<div class="employee-shift-line"><span>Выход</span><strong>${formatMoney(shift.exit_amount || 0)}</strong></div>`,
                `<div class="employee-shift-line"><span>Бонус</span><strong>${formatMoney(shift.bonus_amount || 0)}</strong></div>`,
                `<div class="employee-shift-line"><span>Категории</span><strong>${formatMoney(shift.category_earnings_total || 0)}</strong></div>`,
            ];
            if (shift.is_closed || day.date <= today) {
                lines.push(`<div class="employee-shift-line total"><span>${shift.is_closed ? 'Итог' : 'Промежуточно'}</span><strong>${formatMoney(shift.gross_salary_amount || 0)}</strong></div>`);
            }
            body = compactMode
                ? `
                    <div class="employee-shift-status-dot ${statusClass}" aria-label="${statusLabel}" title="${statusLabel}"></div>
                    ${(shift.is_closed || day.date <= today) ? `<div class="employee-shift-compact-total">${formatMoney(shift.gross_salary_amount || 0)}</div>` : ''}
                `
                : `
                    <div class="employee-shift-status ${statusClass}">${statusLabel}</div>
                    <div class="employee-shift-lines">${lines.join('')}</div>
                    ${selectable ? `<button type="button" class="btn secondary btn-inline employee-shift-open-btn" onclick="openShiftDay('${day.date}')">Открыть детали</button>` : ''}
                `;
        }

        cells.push(`
            <article class="shift-calendar-cell employee-shift-calendar-cell ${day.date === today ? 'shift-calendar-cell--today' : ''}">
                <div class="shift-calendar-cell-head">
                    <div class="shift-calendar-day-meta">
                        <strong>${dayNumber}</strong>
                        <span>${formatDateRu(day.date)}</span>
                    </div>
                    <div class="shift-calendar-head-actions">
                        <span class="shift-calendar-count">${shiftCount ? '1' : '0'}</span>
                    </div>
                </div>
                <div class="shift-calendar-cell-body">${body}</div>
            </article>
        `);
    });

    grid.innerHTML = cells.join('');
}

async function loadSetupForLocation() {
    const location = selectedLocation();
    if (!location) return;
    let setup;
    try {
        setup = await api(`/api/payroll/shifts/setup?location=${encodeURIComponent(location)}`);
    } catch (error) {
        setup = await api(`/api/payroll/settings?location=${encodeURIComponent(location)}`);
    }
    shiftsState.employees = setup.employees || [];
    renderEmployees();
}

async function loadShiftCalendar() {
    const location = selectedLocation();
    const { month, dateFrom, dateTo } = monthDateRange();
    if (!location || !month) return;
    const [year, mon] = month.split('-').map(Number);
    const payload = await api(`/api/payroll/shifts?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}`);
    const daysByDate = new Map((payload.days || []).map(day => [day.date, day]));
    const rendered = [];
    for (let d = 1; d <= Number(dateTo.slice(8, 10)); d += 1) {
        const iso = `${year}-${String(mon).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        rendered.push(daysByDate.get(iso) || { date: iso, shifts: [] });
    }
    shiftsState.shiftDays = rendered;
    renderShiftCalendar();
    renderShiftDatePicker();
}

async function loadPayrollDays() {
    const location = selectedLocation();
    const { dateFrom, dateTo } = monthDateRange();
    if (!location || !dateFrom || !dateTo) {
        shiftsState.payrollDays = [];
        shiftsState.payrollDaysContextKey = '';
        return;
    }
    const payload = await api(`/api/payroll/shifts/day-summary?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}`);
    shiftsState.payrollDays = payload.days || [];
    shiftsState.payrollDaysContextKey = currentPayrollDaysContextKey();
    if (shiftsState.filterMode === 'date' && shiftsState.selectedDate) {
        const day = shiftsState.shiftDays.find(item => item.date === shiftsState.selectedDate);
        if (!day || !isShiftDaySelectable(day)) {
            shiftsState.filterMode = 'month';
            shiftsState.selectedDate = '';
        }
    }
    renderShiftFilterControls();
    renderPayrollDays();
}

async function refreshPageData(showSuccess = true, options = {}) {
    const { forcePayrollReload = false } = options;
    showStatus('Загружаем смены...', 'loading');
    try {
        if (forcePayrollReload) {
            shiftsState.payrollDaysLoaded = false;
            shiftsState.payrollDaysContextKey = '';
            shiftsState.payrollDays = [];
        }
        await loadSetupForLocation();
        await loadShiftCalendar();
        if (isPayrollSectionOpen()) {
            await ensurePayrollDaysLoaded(forcePayrollReload, { showLoading: true });
        } else {
            shiftsState.payrollDays = [];
            if (!shiftsState.payrollDaysLoaded || forcePayrollReload) {
                setPayrollDaysPlaceholder();
            }
        }
        if (showSuccess) {
            showStatus('Данные обновлены.', 'success');
            setTimeout(hideStatus, 1200);
        } else {
            hideStatus();
        }
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось загрузить смены.', 'error');
    }
}

window.shiftsRefresh = refreshPageData;

window.openShiftDay = async function openShiftDay(dateValue) {
    if (!dateValue) return;
    shiftsState.filterMode = 'date';
    shiftsState.selectedDate = dateValue;
    renderShiftFilterControls();
    toggleShiftDatePicker(false);
    const payrollSection = qs('shift-payroll-section');
    if (payrollSection && !payrollSection.open) {
        payrollSection.open = true;
        syncCollapseToggleText(payrollSection);
    }
    try {
        await ensurePayrollDaysLoaded(false, { showLoading: true });
        renderPayrollDays();
        qs('shift-payroll-days-container')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось загрузить детализацию смен.', 'error');
    }
};

window.deleteShift = async function deleteShift(id) {
    if (!confirm('Убрать эту смену из активного календаря?')) return;
    showScopedStatus('shift-calendar-status', 'Убираем смену...', 'loading');
    try {
        await api(`/api/payroll/shifts/${id}`, { method: 'DELETE' });
        await refreshPageData(false, { forcePayrollReload: true });
        showStatus('Смена убрана.', 'success');
        showScopedStatus('shift-calendar-status', 'Смена убрана.', 'success');
        setTimeout(hideStatus, 1200);
    } catch (error) {
        showStatus(error.message || 'Не удалось убрать смену.', 'error');
        showScopedStatus('shift-calendar-status', error.message || 'Не удалось убрать смену.', 'error');
    }
};

window.closeAdminShift = async function closeAdminShift(id) {
    showScopedStatus('shift-calendar-status', 'Закрываем смену...', 'loading');
    try {
        await api(`/api/payroll/shifts/${id}/close`, { method: 'POST' });
        await refreshPageData(false, { forcePayrollReload: true });
        showStatus('Смена закрыта.', 'success');
        showScopedStatus('shift-calendar-status', 'Смена закрыта.', 'success');
        setTimeout(hideStatus, 1200);
    } catch (error) {
        showStatus(error.message || 'Не удалось закрыть смену.', 'error');
        showScopedStatus('shift-calendar-status', error.message || 'Не удалось закрыть смену.', 'error');
    }
};

window.openShiftModal = function openShiftModal(dateValue) {
    if (qs('shift-modal-date-input')) qs('shift-modal-date-input').value = dateValue || todayIso();
    hideScopedStatus('shift-modal-status');
    qs('shift-modal')?.classList.remove('hidden');
};

function closeShiftModal() {
    qs('shift-modal')?.classList.add('hidden');
    hideScopedStatus('shift-modal-status');
}

async function saveShiftFromModal() {
    const button = qs('shift-modal-save-btn');
    const payload = {
        location: selectedLocation(),
        shift_date: qs('shift-modal-date-input')?.value,
        employee_user_id: Number(qs('shift-modal-employee-select')?.value || 0),
    };
    if (!payload.location || !payload.shift_date || !payload.employee_user_id) {
        showStatus('Выберите дату и сотрудника для смены.', 'error');
        showScopedStatus('shift-modal-status', 'Выберите дату и сотрудника для смены.', 'error');
        return;
    }
    showScopedStatus('shift-modal-status', 'Назначаем смену...', 'loading');
    try {
        setButtonLoading(button, true, 'Назначаем...');
        await api('/api/payroll/shifts', { method: 'POST', body: JSON.stringify(payload) });
        closeShiftModal();
        await refreshPageData(false, { forcePayrollReload: true });
        showStatus('Смена назначена.', 'success');
        showScopedStatus('shift-calendar-status', 'Смена назначена.', 'success');
        setTimeout(hideStatus, 1200);
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось назначить смену.', 'error');
        showScopedStatus('shift-modal-status', error.message || 'Не удалось назначить смену.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}

async function handleShiftDatePickerClick(event) {
    const target = event.target.closest('[data-shift-picker-date]');
    if (!target || target.disabled) return;
    await selectShiftDate(target.dataset.shiftPickerDate || '');
}

async function bootstrap() {
    renderMonthOptions();
    setDefaults();
    renderShiftFilterControls();
    setPayrollDaysPlaceholder();
    document.querySelectorAll('.payroll-collapse').forEach((details) => {
        syncCollapseToggleText(details);
        details.addEventListener('toggle', async () => {
            syncCollapseToggleText(details);
            if (details.id === 'shift-payroll-section' && details.open) {
                try {
                    await ensurePayrollDaysLoaded(false, { showLoading: true });
                } catch (error) {
                    console.error(error);
                    showStatus(error.message || 'Не удалось загрузить детализацию смен.', 'error');
                }
            }
        });
    });
    shiftsState.locations = fallbackLocations();
    renderLocations();
    try {
        const access = await api('/api/payroll/access');
        shiftsState.locations = normalizeLocationList(access.locations || []);
        renderLocations();
        await refreshPageData(false, { forcePayrollReload: true });
    } catch (error) {
        console.error(error);
        shiftsState.locations = fallbackLocations();
        renderLocations();
        if (selectedLocation()) {
            try {
                await refreshPageData(false, { forcePayrollReload: true });
                showStatus('Список точек из API не загрузился, показана базовая точка.', 'warning');
                return;
            } catch (fallbackError) {
                console.error(fallbackError);
            }
        }
        showStatus(error.message || 'Не удалось загрузить страницу смен.', 'error');
    }
}

qs('shift-location-select')?.addEventListener('change', async () => {
    shiftsState.filterMode = 'month';
    shiftsState.selectedDate = '';
    shiftsState.payrollDays = [];
    shiftsState.payrollDaysLoaded = false;
    shiftsState.payrollDaysContextKey = '';
    setPayrollDaysPlaceholder('Загружаем детализацию смен для новой точки...');
    await refreshPageData(false, { forcePayrollReload: true });
});
qs('shift-year-input')?.addEventListener('change', async () => {
    shiftsState.filterMode = 'month';
    shiftsState.selectedDate = '';
    shiftsState.payrollDays = [];
    shiftsState.payrollDaysLoaded = false;
    shiftsState.payrollDaysContextKey = '';
    setPayrollDaysPlaceholder('Загружаем детализацию смен за новый период...');
    await refreshPageData(false, { forcePayrollReload: true });
});
qs('shift-month-select')?.addEventListener('change', async () => {
    shiftsState.filterMode = 'month';
    shiftsState.selectedDate = '';
    shiftsState.payrollDays = [];
    shiftsState.payrollDaysLoaded = false;
    shiftsState.payrollDaysContextKey = '';
    setPayrollDaysPlaceholder('Загружаем детализацию смен за новый период...');
    await refreshPageData(false, { forcePayrollReload: true });
});
qs('shift-view-all-btn')?.addEventListener('click', () => { void setShiftFilterMode('month'); });
qs('shift-view-date-btn')?.addEventListener('click', () => { void setShiftFilterMode('date'); });
qs('shift-date-picker-btn')?.addEventListener('click', () => toggleShiftDatePicker());
qs('shift-date-picker-grid')?.addEventListener('click', handleShiftDatePickerClick);
qs('shift-modal-save-btn')?.addEventListener('click', saveShiftFromModal);
qs('shift-modal-close-btn')?.addEventListener('click', closeShiftModal);
qs('shift-modal-cancel-btn')?.addEventListener('click', closeShiftModal);
qs('shift-modal')?.addEventListener('click', (event) => {
    if (event.target === qs('shift-modal')) closeShiftModal();
});
qs('shift-floating-add-btn')?.addEventListener('click', () => openShiftModal(shiftsState.selectedDate || todayIso()));
document.addEventListener('click', (event) => {
    const wrap = qs('shift-date-picker-wrap');
    const popover = qs('shift-date-picker-popover');
    if (!wrap || !popover || popover.classList.contains('hidden')) return;
    if (!wrap.contains(event.target)) {
        toggleShiftDatePicker(false);
    }
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        if (!qs('shift-modal')?.classList.contains('hidden')) {
            closeShiftModal();
            return;
        }
        toggleShiftDatePicker(false);
    }
});

window.addEventListener('resize', () => {
    renderShiftCalendar();
    renderShiftDatePicker();
    renderPayrollDays();
});

bootstrap();
