const shiftsState = {
    user: window.currentUser || {},
    locations: [],
    employees: [],
    shiftDays: [],
    payrollDays: [],
    filterMode: 'month',
    selectedFilterDate: '',
};

const SHIFT_MONTH_NAMES = [
    'Январь',
    'Февраль',
    'Март',
    'Апрель',
    'Май',
    'Июнь',
    'Июль',
    'Август',
    'Сентябрь',
    'Октябрь',
    'Ноябрь',
    'Декабрь',
];

function qs(id) {
    return document.getElementById(id);
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

function currentYear() {
    return Number(todayIso().slice(0, 4));
}

function currentMonthNumber() {
    return Number(todayIso().slice(5, 7));
}

function monthIso() {
    return `${currentYear()}-${String(currentMonthNumber()).padStart(2, '0')}`;
}

function monthLabel(year, month) {
    if (!year || !month) return '';
    return new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(new Date(year, month - 1, 1));
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

function selectedYear() {
    const value = Number(qs('shift-year-input')?.value || currentYear());
    return Number.isFinite(value) && value >= 2020 ? value : currentYear();
}

function selectedMonthNumber() {
    const value = Number(qs('shift-month-select')?.value || currentMonthNumber());
    return Number.isFinite(value) && value >= 1 && value <= 12 ? value : currentMonthNumber();
}

function selectedMonthIso() {
    return `${selectedYear()}-${String(selectedMonthNumber()).padStart(2, '0')}`;
}

function monthDateRange() {
    const year = selectedYear();
    const month = selectedMonthNumber();
    const dateFrom = `${year}-${String(month).padStart(2, '0')}-01`;
    const dateTo = new Date(year, month, 0).toISOString().slice(0, 10);
    return { year, month, monthIso: selectedMonthIso(), dateFrom, dateTo };
}

function setDefaults() {
    if (qs('shift-year-input')) qs('shift-year-input').value = String(currentYear());
    if (qs('shift-date-input')) qs('shift-date-input').value = todayIso();
}

function renderMonthOptions() {
    const select = qs('shift-month-select');
    if (!select) return;
    select.innerHTML = SHIFT_MONTH_NAMES.map((name, index) => `<option value="${index + 1}">${name}</option>`).join('');
    select.value = String(currentMonthNumber());
}

function renderLocations() {
    const select = qs('shift-location-select');
    if (!select) return;
    select.innerHTML = shiftsState.locations.map(location => `<option value="${location}">${location}</option>`).join('');
    const defaultLocation = shiftsState.user.default_location || shiftsState.user.location || shiftsState.locations[0] || '';
    if (defaultLocation && shiftsState.locations.includes(defaultLocation)) {
        select.value = defaultLocation;
    }
}

function renderEmployees() {
    const options = shiftsState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`).join('');
    if (qs('shift-employee-select')) qs('shift-employee-select').innerHTML = options;
    if (qs('shift-modal-employee-select')) qs('shift-modal-employee-select').innerHTML = options;
}

function syncCollapseToggleText(details) {
    if (!details) return;
    const text = details.querySelector('.payroll-collapse-btn');
    if (!text) return;
    text.textContent = details.open ? 'Свернуть' : 'Развернуть';
}

function dateHasPassedOrClosed(dateValue) {
    if (!dateValue) return false;
    if (dateValue < todayIso()) return true;
    return (shiftsState.payrollDays || []).some(day => day.shift_date === dateValue && day.is_closed);
}

function availableFilterDates() {
    const allowed = new Set();
    for (const day of shiftsState.payrollDays || []) {
        if (dateHasPassedOrClosed(day.shift_date)) {
            allowed.add(day.shift_date);
        }
    }
    return [...allowed].sort();
}

function ensureValidDateFilterSelection() {
    const available = availableFilterDates();
    if (!available.length) {
        shiftsState.filterMode = 'month';
        shiftsState.selectedFilterDate = '';
        return;
    }
    const [firstAvailable] = available;
    const currentMonthPrefix = `${selectedYear()}-${String(selectedMonthNumber()).padStart(2, '0')}-`;
    if (!shiftsState.selectedFilterDate || !available.includes(shiftsState.selectedFilterDate) || !shiftsState.selectedFilterDate.startsWith(currentMonthPrefix)) {
        shiftsState.selectedFilterDate = firstAvailable;
    }
    if (shiftsState.filterMode === 'date' && !available.includes(shiftsState.selectedFilterDate)) {
        shiftsState.filterMode = 'month';
    }
}

function updateFilterControls() {
    ensureValidDateFilterSelection();
    const allBtn = qs('shift-filter-all-btn');
    const dateModeBtn = qs('shift-filter-date-mode-btn');
    const pickerWrap = qs('shift-filter-date-picker-wrap');
    const pickerBtn = qs('shift-filter-date-btn');
    const hint = qs('shift-filter-hint');
    const available = availableFilterDates();
    const inDateMode = shiftsState.filterMode === 'date';

    if (allBtn) allBtn.classList.toggle('active', !inDateMode);
    if (dateModeBtn) {
        dateModeBtn.classList.toggle('active', inDateMode);
        dateModeBtn.disabled = !available.length;
    }
    if (pickerWrap) pickerWrap.classList.toggle('hidden', !inDateMode);
    if (pickerBtn) {
        pickerBtn.disabled = !available.length;
        pickerBtn.textContent = shiftsState.selectedFilterDate ? formatDateRu(shiftsState.selectedFilterDate) : 'Выбрать дату';
    }
    if (hint) {
        if (!available.length) {
            hint.textContent = 'За выбранный месяц нет доступных завершённых дат со сменами для точечного просмотра.';
        } else if (inDateMode && shiftsState.selectedFilterDate) {
            hint.textContent = `Показана только дата ${formatDateRu(shiftsState.selectedFilterDate)}.`;
        } else {
            hint.textContent = `Показаны все даты за ${monthLabel(selectedYear(), selectedMonthNumber())}.`;
        }
    }
}

function filteredPayrollDays() {
    if (shiftsState.filterMode !== 'date' || !shiftsState.selectedFilterDate) {
        return shiftsState.payrollDays || [];
    }
    return (shiftsState.payrollDays || []).filter(day => day.shift_date === shiftsState.selectedFilterDate);
}

function renderPayrollDays() {
    const container = qs('shift-payroll-days-container');
    if (!container) return;
    updateFilterControls();
    const days = filteredPayrollDays();
    if (!days.length) {
        container.innerHTML = shiftsState.filterMode === 'date'
            ? '<div class="muted-text">На выбранную дату смены не найдены.</div>'
            : '<div class="muted-text">За выбранный месяц смен не найдено.</div>';
        return;
    }
    container.innerHTML = days.map(day => `
        <article class="payroll-day-card">
            <div class="payroll-day-header">
                <strong>${escapeHtml(day.shift_date || '')}</strong>
                <span class="payroll-chip ${day.is_closed ? 'green' : 'orange'}">${day.is_closed ? 'Закрыта' : 'Открыта'}</span>
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

function renderShiftCalendar() {
    const grid = qs('shift-calendar-grid');
    if (!grid) return;

    const days = shiftsState.shiftDays || [];
    const { year, month } = monthDateRange();
    const firstWeekday = (new Date(year, month - 1, 1).getDay() + 6) % 7;
    const cells = [];
    for (let i = 0; i < firstWeekday; i += 1) {
        cells.push('<div class="shift-calendar-cell shift-calendar-cell--empty" aria-hidden="true"></div>');
    }

    if (!days.length) {
        grid.innerHTML = `<div class="shift-calendar-empty muted-text">В ${monthLabel(year, month)} смен пока нет.</div>`;
        return;
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

function renderFilterCalendarPopover() {
    const grid = qs('shift-filter-calendar-grid');
    const title = qs('shift-filter-calendar-title');
    if (!grid || !title) return;

    const { year, month, dateTo } = monthDateRange();
    const allowed = new Set(availableFilterDates());
    title.textContent = monthLabel(year, month);
    const firstWeekday = (new Date(year, month - 1, 1).getDay() + 6) % 7;
    const daysInMonth = Number(dateTo.slice(8, 10));
    const cells = [];

    for (let i = 0; i < firstWeekday; i += 1) {
        cells.push('<div class="shift-mini-calendar-empty" aria-hidden="true"></div>');
    }

    for (let day = 1; day <= daysInMonth; day += 1) {
        const iso = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const isAvailable = allowed.has(iso);
        const isSelected = shiftsState.selectedFilterDate === iso;
        const isToday = iso === todayIso();
        cells.push(`
            <button
                type="button"
                class="shift-mini-calendar-day ${isAvailable ? 'available' : 'disabled'} ${isSelected ? 'selected' : ''} ${isToday ? 'today' : ''}"
                data-shift-filter-date="${iso}"
                ${isAvailable ? '' : 'disabled'}
            >
                <span>${day}</span>
            </button>
        `);
    }

    grid.innerHTML = cells.join('');
}

function openFilterCalendarPopover() {
    if (!availableFilterDates().length) return;
    renderFilterCalendarPopover();
    qs('shift-filter-calendar-popover')?.classList.remove('hidden');
}

function closeFilterCalendarPopover() {
    qs('shift-filter-calendar-popover')?.classList.add('hidden');
}

async function loadSetupForLocation() {
    const location = selectedLocation();
    if (!location) return;
    const setup = await api(`/api/payroll/settings?location=${encodeURIComponent(location)}`);
    shiftsState.employees = setup.employees || [];
    renderEmployees();
}

async function loadShiftCalendar() {
    const location = selectedLocation();
    const { year, month, dateFrom, dateTo } = monthDateRange();
    if (!location || !dateFrom || !dateTo) return;
    const payload = await api(`/api/payroll/shifts?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}`);
    const daysByDate = new Map((payload.days || []).map(day => [day.date, day]));
    const rendered = [];
    for (let d = 1; d <= Number(dateTo.slice(8, 10)); d += 1) {
        const iso = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        rendered.push(daysByDate.get(iso) || { date: iso, shifts: [] });
    }
    shiftsState.shiftDays = rendered;
    renderShiftCalendar();
}

async function loadPayrollDays() {
    const location = selectedLocation();
    const { dateFrom, dateTo } = monthDateRange();
    if (!location || !dateFrom || !dateTo) return;
    const payload = await api(`/api/payroll/employee-summary?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}`);
    shiftsState.payrollDays = payload.days || [];
    ensureValidDateFilterSelection();
    renderPayrollDays();
}

async function refreshPageData(showSuccess = true) {
    showStatus('Загружаем смены...', 'loading');
    try {
        await loadSetupForLocation();
        await Promise.all([
            loadShiftCalendar(),
            loadPayrollDays(),
        ]);
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

async function addShift() {
    const button = qs('add-shift-btn');
    const payload = {
        location: selectedLocation(),
        shift_date: qs('shift-date-input')?.value,
        employee_user_id: Number(qs('shift-employee-select')?.value || 0),
    };
    if (!payload.location || !payload.shift_date || !payload.employee_user_id) {
        showStatus('Выберите точку, дату и сотрудника.', 'error');
        return;
    }
    try {
        setButtonLoading(button, true, 'Назначаем...');
        await api('/api/payroll/shifts', { method: 'POST', body: JSON.stringify(payload) });
        await refreshPageData(false);
        showStatus('Смена назначена.', 'success');
        setTimeout(hideStatus, 1200);
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось назначить смену.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}

window.deleteShift = async function deleteShift(id) {
    if (!confirm('Убрать эту смену из активного календаря?')) return;
    try {
        await api(`/api/payroll/shifts/${id}`, { method: 'DELETE' });
        await refreshPageData(false);
        showStatus('Смена убрана.', 'success');
        setTimeout(hideStatus, 1200);
    } catch (error) {
        showStatus(error.message || 'Не удалось убрать смену.', 'error');
    }
};

window.closeAdminShift = async function closeAdminShift(id) {
    try {
        await api(`/api/payroll/shifts/${id}/close`, { method: 'POST' });
        await refreshPageData(false);
        showStatus('Смена закрыта.', 'success');
        setTimeout(hideStatus, 1200);
    } catch (error) {
        showStatus(error.message || 'Не удалось закрыть смену.', 'error');
    }
};

window.openShiftModal = function openShiftModal(dateValue) {
    if (qs('shift-modal-date-input')) qs('shift-modal-date-input').value = dateValue || qs('shift-date-input')?.value || todayIso();
    qs('shift-modal')?.classList.remove('hidden');
};

function closeShiftModal() {
    qs('shift-modal')?.classList.add('hidden');
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
        return;
    }
    try {
        setButtonLoading(button, true, 'Назначаем...');
        await api('/api/payroll/shifts', { method: 'POST', body: JSON.stringify(payload) });
        closeShiftModal();
        await refreshPageData(false);
        showStatus('Смена назначена.', 'success');
        setTimeout(hideStatus, 1200);
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось назначить смену.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}

function setShiftMonthMode() {
    shiftsState.filterMode = 'month';
    closeFilterCalendarPopover();
    renderPayrollDays();
}

function setShiftSpecificDateMode() {
    const available = availableFilterDates();
    if (!available.length) {
        shiftsState.filterMode = 'month';
        renderPayrollDays();
        return;
    }
    shiftsState.filterMode = 'date';
    shiftsState.selectedFilterDate = shiftsState.selectedFilterDate && available.includes(shiftsState.selectedFilterDate)
        ? shiftsState.selectedFilterDate
        : available[0];
    renderPayrollDays();
}

function handleFilterCalendarClick(event) {
    const button = event.target.closest('[data-shift-filter-date]');
    if (!button) return;
    shiftsState.filterMode = 'date';
    shiftsState.selectedFilterDate = button.dataset.shiftFilterDate || '';
    closeFilterCalendarPopover();
    renderPayrollDays();
}

async function logout() {
    await api('/api/logout', { method: 'POST' });
    window.location.href = '/login';
}

async function bootstrap() {
    setDefaults();
    renderMonthOptions();
    document.querySelectorAll('.payroll-collapse').forEach((details) => {
        syncCollapseToggleText(details);
        details.addEventListener('toggle', () => syncCollapseToggleText(details));
    });
    try {
        const access = await api('/api/payroll/access');
        shiftsState.locations = access.locations || [];
        renderLocations();
        await refreshPageData(false);
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось загрузить страницу смен.', 'error');
    }
}

qs('shift-location-select')?.addEventListener('change', () => refreshPageData(false));
qs('shift-load-btn')?.addEventListener('click', () => refreshPageData());
qs('shift-year-input')?.addEventListener('change', () => refreshPageData(false));
qs('shift-month-select')?.addEventListener('change', () => refreshPageData(false));
qs('add-shift-btn')?.addEventListener('click', addShift);
qs('logout-btn')?.addEventListener('click', logout);
qs('shift-modal-save-btn')?.addEventListener('click', saveShiftFromModal);
qs('shift-modal-close-btn')?.addEventListener('click', closeShiftModal);
qs('shift-modal-cancel-btn')?.addEventListener('click', closeShiftModal);
qs('shift-filter-all-btn')?.addEventListener('click', setShiftMonthMode);
qs('shift-filter-date-mode-btn')?.addEventListener('click', setShiftSpecificDateMode);
qs('shift-filter-date-btn')?.addEventListener('click', (event) => {
    event.stopPropagation();
    const popover = qs('shift-filter-calendar-popover');
    if (!popover) return;
    if (popover.classList.contains('hidden')) {
        openFilterCalendarPopover();
    } else {
        closeFilterCalendarPopover();
    }
});
qs('shift-filter-calendar-close-btn')?.addEventListener('click', closeFilterCalendarPopover);
qs('shift-filter-calendar-grid')?.addEventListener('click', handleFilterCalendarClick);
qs('shift-modal')?.addEventListener('click', (event) => {
    if (event.target === qs('shift-modal')) closeShiftModal();
});
document.addEventListener('click', (event) => {
    const popover = qs('shift-filter-calendar-popover');
    const wrap = qs('shift-filter-date-picker-wrap');
    if (!popover || popover.classList.contains('hidden')) return;
    if (wrap && !wrap.contains(event.target)) {
        closeFilterCalendarPopover();
    }
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
        if (!qs('shift-modal')?.classList.contains('hidden')) {
            closeShiftModal();
        }
        closeFilterCalendarPopover();
    }
});

bootstrap();
