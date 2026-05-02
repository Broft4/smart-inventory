const PAYROLL_ALL_LOCATIONS_VALUE = '__all__';
const PAYROLL_ALL_LOCATIONS_LABEL = 'Все точки';
const PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_MS = 5 * 60 * 1000;
const PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_LOCK_TTL_MS = 8 * 60 * 1000;
const PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_CLIENT_ID = `${Date.now()}-${Math.random().toString(16).slice(2)}`;

const payrollState = {
    user: window.currentUser || {},
    locations: [],
    employees: [],
    admins: [],
    settings: null,
    categoryCatalog: [],
    requestedSettingsEffectiveFrom: '',
    summary: null,
    managerSummary: null,
    templates: [],
    expenses: [],
    employeeBonuses: [],
    audit: [],
    shiftDays: [],
    categoryFilters: {
        search: '',
        view: 'all',
        sort: 'earning_desc',
    },
    employeeView: 'salary',
    activeRecalcJob: null,
    recalcPollTimer: null,
    summaryLoadingPromise: null,
    currentShiftAutoRefreshTimer: null,
    currentShiftAutoRefreshInProgress: false,
    lastCurrentShiftAutoRefreshAt: null,
    lastViewportWidth: window.innerWidth || 0,
    expensesCollapsed: true,
};

function qs(id) {
    return document.getElementById(id);
}

function isAdminRole() {
    return ['admin', 'superadmin'].includes(payrollState.user.role);
}

function isSuperadminRole() {
    return payrollState.user.role === 'superadmin';
}

function roleDisplayName(role) {
    if (role === 'superadmin') return 'Главный управляющий';
    if (role === 'admin') return 'Управляющий';
    if (role === 'employee') return 'Сотрудник';
    return role || '—';
}

function formatMoney(value) {
    const num = Number(value || 0);
    return `${num.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
}

function isPayrollReturnsCategory(category) {
    const categoryId = String(category?.category_id || '').trim();
    const categoryName = String(category?.category_name || '').trim();
    const salesAmount = Number(category?.sales_amount || 0);
    const returnAmount = Number(category?.return_amount || 0);
    const netSalesAmount = Number(category?.net_sales_amount || 0);
    const isUncategorized = categoryId === '__other__' || categoryName === 'Без категории';
    return isUncategorized && (returnAmount > 0.009 || salesAmount < -0.009 || netSalesAmount < -0.009);
}

function payrollCategoryDisplayName(category) {
    if (isPayrollReturnsCategory(category)) {
        return 'Возвраты';
    }
    return String(category?.category_name || category?.name || '').trim();
}

function isDisplayedReturnsCategory(category) {
    const categoryName = payrollCategoryDisplayName(category);
    return categoryName === 'Возвраты';
}

function formatPayrollCategoryRate(category) {
    if (isDisplayedReturnsCategory(category)) {
        return '—';
    }
    return `${Number(category?.rate_percent || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%`;
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

function expenseModeLabel(mode) {
    return String(mode || '').trim() === 'spread' ? 'Растянуть на месяц' : 'Одним днём';
}
function defaultManualExpenseDate() {
    const selectedMonth = selectedMonthStart('expenses-month-input');
    const today = todayIso();
    return today.startsWith(selectedMonth.slice(0, 7)) ? today : selectedMonth;
}
function syncManualExpenseDefaults({ forceDate = false } = {}) {
    const modeInput = qs('manual-expense-mode');
    const dateInput = qs('manual-expense-date');
    if (modeInput && !modeInput.value) {
        modeInput.value = 'single_day';
    }
    if (dateInput && (forceDate || !dateInput.value)) {
        dateInput.value = defaultManualExpenseDate();
    }
}

function defaultEmployeeBonusDate() {
    const selectedMonth = selectedMonthStart('employee-bonuses-month-input');
    const today = todayIso();
    return today.startsWith(selectedMonth.slice(0, 7)) ? today : selectedMonth;
}
function syncEmployeeBonusDefaults({ forceDate = false } = {}) {
    const dateInput = qs('employee-bonus-date');
    if (dateInput && (forceDate || !dateInput.value)) {
        dateInput.value = defaultEmployeeBonusDate();
    }
}
function formatApiErrorMessage(payload) {
    const detail = payload?.detail ?? payload?.message ?? payload;

    const fromValidationItem = (item) => {
        const loc = Array.isArray(item?.loc) ? item.loc.map(String).join('.') : '';
        if (loc.includes('name')) {
            return 'Введите название.';
        }
        if (typeof item?.msg === 'string' && item.msg.trim()) {
            return item.msg.trim();
        }
        return '';
    };

    if (typeof detail === 'string' && detail.trim()) {
        return detail.trim();
    }
    if (Array.isArray(detail)) {
        const messages = detail
            .map((item) => {
                if (typeof item === 'string') return item.trim();
                if (item && typeof item === 'object') return fromValidationItem(item);
                return '';
            })
            .filter(Boolean);
        if (messages.length) {
            return messages[0];
        }
    }
    if (detail && typeof detail === 'object') {
        if (typeof detail.message === 'string' && detail.message.trim()) {
            return detail.message.trim();
        }
        if (typeof detail.error === 'string' && detail.error.trim()) {
            return detail.error.trim();
        }
    }
    return 'Ошибка запроса';
}
function setSettingsCategoryInputsDisabled(disabled) {
    const card = qs('settings-rates-card');
    if (!card) return;
    card.classList.toggle('is-disabled', Boolean(disabled));
    card.setAttribute('aria-busy', disabled ? 'true' : 'false');
    card.querySelectorAll('input, select, textarea, button').forEach((element) => {
        element.disabled = Boolean(disabled);
    });
}
function setSettingsLoading(isLoading, text = 'Загружаем настройки...') {
    const indicators = [qs('settings-section-loading'), qs('settings-category-loading')].filter(Boolean);
    indicators.forEach((indicator) => {
        indicator.classList.toggle('hidden', !isLoading);
        const textNode = indicator.querySelector('[data-loading-text]') || indicator.querySelector('span:last-child');
        if (textNode) {
            textNode.textContent = text;
        }
    });
    setSettingsCategoryInputsDisabled(isLoading);
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


function normalizeCategoryDisplayNet(category) {
    const salesAmount = Number(category?.sales_amount || 0);
    if (Math.abs(salesAmount) > 1e-9) {
        return salesAmount;
    }
    return Number(category?.net_sales_amount || 0);
}

function normalizeCategoryProfit(category) {
    const explicitProfit = Number(category?.profit_amount);
    if (Number.isFinite(explicitProfit)) {
        return explicitProfit;
    }
    return normalizeCategoryDisplayNet(category) - Number(category?.cost_amount || 0);
}

function sumPayrollCategoryColumns(categories = []) {
    return (categories || []).reduce((acc, category) => {
        acc.sales_amount += Number(category?.sales_amount || 0);
        acc.return_amount += Number(category?.return_amount || 0);
        acc.net_sales_amount += normalizeCategoryDisplayNet(category);
        acc.cost_amount += Number(category?.cost_amount || 0);
        acc.earning_amount += Number(category?.earning_amount || 0);
        acc.profit_amount += normalizeCategoryProfit(category);
        return acc;
    }, {
        sales_amount: 0,
        return_amount: 0,
        net_sales_amount: 0,
        cost_amount: 0,
        earning_amount: 0,
        profit_amount: 0,
    });
}

function stopRecalcPolling() {
    if (payrollState.recalcPollTimer) {
        clearTimeout(payrollState.recalcPollTimer);
        payrollState.recalcPollTimer = null;
    }
}

async function refreshAfterRecalcFinished() {
    await Promise.allSettled([
        loadShiftCalendar(),
        loadExpenseTemplatesAndEntries(),
        loadSummary(),
        loadAudit(),
    ]);
}

async function pollRecalcStatus(jobId) {
    const location = selectedLocation();
    if (!location || !jobId) return;
    stopRecalcPolling();
    try {
        const payload = await api(`/api/payroll/recalc-status?location=${encodeURIComponent(location)}&job_id=${jobId}`);
        const job = payload?.job || null;
        payrollState.activeRecalcJob = job;
        if (!job) return;
        if (job.status === 'done') {
            const processed = Number(job?.result?.updated || job?.result?.processed || 0);
            const message = processed > 0
                ? `Настройки сохранены. Пересчёт завершён, обновлено смен: ${processed}.`
                : 'Настройки сохранены. Пересчёт завершён.';
            showStatus(message, 'success');
            showScopedStatus('settings-status', message, 'success');
            await refreshAfterRecalcFinished();
            stopRecalcPolling();
            return;
        }
        if (job.status === 'failed') {
            const message = job.error_text || job.message || 'Не удалось пересчитать смены по новым правилам.';
            showStatus(message, 'error');
            showScopedStatus('settings-status', message, 'error');
            stopRecalcPolling();
            return;
        }
        const progressTotal = Number(job.progress_total || 0);
        const progressCurrent = Number(job.progress_current || 0);
        const progressText = progressTotal > 0
            ? `Идёт пересчёт закрытых смен: ${progressCurrent} из ${progressTotal}.`
            : (job.message || 'Идёт пересчёт закрытых смен...');
        showStatus(progressText, 'loading');
        showScopedStatus('settings-status', progressText, 'loading');
        payrollState.recalcPollTimer = setTimeout(() => {
            pollRecalcStatus(jobId).catch((error) => console.error(error));
        }, 2500);
    } catch (error) {
        console.error(error);
        payrollState.recalcPollTimer = setTimeout(() => {
            pollRecalcStatus(jobId).catch((pollError) => console.error(pollError));
        }, 4000);
    }
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
        throw new Error(formatApiErrorMessage(payload));
    }
    return payload;
}

function formatLocalDateIso(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function todayIso() {
    return formatLocalDateIso(new Date());
}

function dateRangeIncludesIso(dateFrom, dateTo, isoDate) {
    const start = String(dateFrom || '').trim();
    const end = String(dateTo || '').trim();
    const target = String(isoDate || '').trim();
    if (!start || !end || !target) return false;
    return start <= target && target <= end;
}

function selectedPayrollPeriodIncludesToday() {
    return dateRangeIncludesIso(qs('payroll-date-from')?.value, qs('payroll-date-to')?.value, todayIso());
}

function selectedShiftCalendarIncludesToday() {
    const shiftMonth = String(qs('shift-month-input')?.value || '').trim();
    return Boolean(shiftMonth) && shiftMonth === todayIso().slice(0, 7);
}

function currentShiftAutoRefreshLockKey() {
    const location = selectedLocation();
    const employee = selectedEmployeeId() || 'all';
    return `payroll-current-shift-refresh-lock:${todayIso()}:${location}:${employee}`;
}

function acquireCurrentShiftAutoRefreshLock() {
    const location = selectedLocation();
    if (!location || isAllLocationsSelected(location)) return null;
    const key = currentShiftAutoRefreshLockKey();
    const now = Date.now();
    try {
        const current = JSON.parse(window.localStorage.getItem(key) || 'null');
        if (current?.expiresAt && current.expiresAt > now && current.owner !== PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_CLIENT_ID) {
            return null;
        }
        const next = {
            owner: PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_CLIENT_ID,
            expiresAt: now + PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_LOCK_TTL_MS,
        };
        window.localStorage.setItem(key, JSON.stringify(next));
        const saved = JSON.parse(window.localStorage.getItem(key) || 'null');
        return saved?.owner === PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_CLIENT_ID ? key : null;
    } catch {
        return key;
    }
}

function releaseCurrentShiftAutoRefreshLock(key) {
    if (!key) return;
    try {
        const current = JSON.parse(window.localStorage.getItem(key) || 'null');
        if (current?.owner === PAYROLL_CURRENT_SHIFT_AUTO_REFRESH_CLIENT_ID) {
            window.localStorage.removeItem(key);
        }
    } catch {
        // ignore localStorage errors
    }
}

function shouldAutoRefreshCurrentShift() {
    // Автообновление по таймеру отключено.
    // Актуальная выгрузка текущей открытой смены выполняется только при нажатии «Показать».
    return false;
}

function monthIso() {
    return todayIso().slice(0, 7);
}

function payrollSettingsDateStorageKey(location) {
    const normalizedLocation = String(location || '').trim() || '__default__';
    return `payroll-settings-effective-from:${normalizedLocation}`;
}

function getStoredSettingsEffectiveFrom(location) {
    try {
        return window.localStorage.getItem(payrollSettingsDateStorageKey(location)) || '';
    } catch {
        return '';
    }
}

function storeSettingsEffectiveFrom(location, value) {
    const normalizedValue = String(value || '').trim();
    if (!location) return;
    try {
        if (normalizedValue) {
            window.localStorage.setItem(payrollSettingsDateStorageKey(location), normalizedValue);
        } else {
            window.localStorage.removeItem(payrollSettingsDateStorageKey(location));
        }
    } catch {
        // ignore localStorage errors
    }
}

function selectedSettingsEffectiveFrom() {
    const inputValue = String(qs('settings-effective-from')?.value || '').trim();
    if (inputValue) return inputValue;
    return getStoredSettingsEffectiveFrom(selectedLocation()) || '';
}

function setDefaultDates() {
    if (qs('payroll-date-from')) qs('payroll-date-from').value = todayIso();
    if (qs('payroll-date-to')) qs('payroll-date-to').value = todayIso();
    if (qs('settings-effective-from')) qs('settings-effective-from').value = todayIso();
    if (qs('shift-month-input')) qs('shift-month-input').value = monthIso();
    if (qs('expenses-month-input')) qs('expenses-month-input').value = monthIso();
    if (qs('employee-bonuses-month-input')) qs('employee-bonuses-month-input').value = monthIso();
    if (qs('shift-date-input')) qs('shift-date-input').value = todayIso();
    syncManualExpenseDefaults({ forceDate: true });
    syncEmployeeBonusDefaults({ forceDate: true });
}

function selectedLocation() {
    return qs('payroll-location-select').value;
}

function isAllLocationsSelected(value = selectedLocation()) {
    return String(value || '').trim() === PAYROLL_ALL_LOCATIONS_VALUE;
}

function canSelectAllLocations() {
    return isAdminRole() && normalizeLocationList(payrollState.locations || []).length > 1;
}

function selectedEmployeeId() {
    if (isAllLocationsSelected()) return null;
    const raw = qs('payroll-employee-select')?.value || '';
    return raw ? Number(raw) : null;
}

function selectedMonthStart(inputId) {
    const raw = qs(inputId).value || monthIso();
    return `${raw}-01`;
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
        [...(qs('payroll-location-select')?.options || [])].map((option) => option.value || option.textContent || '')
    );
}

function fallbackLocations() {
    return normalizeLocationList([
        ...currentLocationOptionsFromDom(),
        ...(Array.isArray(payrollState.user.accessible_locations) ? payrollState.user.accessible_locations : []),
        payrollState.user.default_location,
        payrollState.user.location,
    ]);
}

function getRequestedPayrollView() {
    try {
        const value = new URLSearchParams(window.location.search).get('view');
        return value === 'shifts' ? 'shifts' : 'salary';
    } catch {
        return 'salary';
    }
}

function renderLocations() {
    const select = qs('payroll-location-select');
    if (!select) return;
    const previousValue = select.value;
    const locations = normalizeLocationList(payrollState.locations.length ? payrollState.locations : fallbackLocations());
    payrollState.locations = locations;
    const options = [];
    if (isAdminRole() && locations.length > 1) {
        options.push(`<option value="${PAYROLL_ALL_LOCATIONS_VALUE}">${PAYROLL_ALL_LOCATIONS_LABEL}</option>`);
    }
    options.push(...locations.map(location => `<option value="${escapeHtml(location)}">${escapeHtml(location)}</option>`));
    select.innerHTML = options.join('');
    const validValues = new Set([...select.options].map((option) => option.value));
    const defaultLocation = payrollState.user.default_location || payrollState.user.location || locations[0] || '';
    if (previousValue && validValues.has(previousValue)) {
        select.value = previousValue;
    } else if (defaultLocation && validValues.has(defaultLocation)) {
        select.value = defaultLocation;
    } else if (select.options.length) {
        select.value = select.options[0].value;
    }
    syncAllLocationsModeControls();
}

function syncAllLocationsModeControls() {
    const allLocations = isAllLocationsSelected();
    const disabledCards = ['admin-settings-card', 'expenses-card', 'employee-bonuses-card'];
    disabledCards.forEach((id) => {
        const card = qs(id);
        if (card && isAdminRole()) {
            card.classList.toggle('hidden', allLocations);
            card.setAttribute('aria-disabled', allLocations ? 'true' : 'false');
        }
    });
    if (allLocations) {
        setSettingsLoading(false);
        payrollState.templates = [];
        payrollState.expenses = [];
        payrollState.employeeBonuses = [];
    }
}

function renderUsersForLocation() {
    const employeeLabel = qs('payroll-employee-label');
    const employeeSelect = qs('payroll-employee-select');
    const shiftEmployeeSelect = qs('shift-employee-select');
    const shiftModalEmployeeSelect = qs('shift-modal-employee-select');
    const auditEmployeeSelect = qs('audit-employee-filter');
    const employeeBonusEmployeeSelect = qs('employee-bonus-employee');
    const employeeOptions = payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`).join('');
    if (!isAdminRole()) {
        employeeLabel.classList.add('hidden');
    } else if (isAllLocationsSelected()) {
        employeeLabel.classList.remove('hidden');
        employeeSelect.innerHTML = '<option value="">Все сотрудники по всем точкам</option>';
        employeeSelect.disabled = true;
    } else {
        employeeLabel.classList.remove('hidden');
        employeeSelect.disabled = false;
        employeeSelect.innerHTML = ['<option value="">Все / я</option>', ...payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`)].join('');
    }
    if (shiftEmployeeSelect) shiftEmployeeSelect.innerHTML = employeeOptions;
    if (shiftModalEmployeeSelect) shiftModalEmployeeSelect.innerHTML = employeeOptions;
    if (employeeBonusEmployeeSelect) employeeBonusEmployeeSelect.innerHTML = ['<option value="">Выберите сотрудника</option>', ...payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`)].join('');
    if (auditEmployeeSelect) {
        const people = new Map();
        [...(payrollState.employees || []), ...(payrollState.admins || [])].forEach(item => {
            if (item?.id != null) people.set(String(item.id), item.full_name);
        });
        auditEmployeeSelect.innerHTML = ['<option value="">Все сотрудники</option>', ...[...people.entries()].map(([id, name]) => `<option value="${id}">${escapeHtml(name)}</option>`)].join('');
    }
}

function syncEmployeePayrollTabs() {
    const switcher = qs('employee-payroll-switcher');
    const salaryCard = qs('payroll-summary-card');
    const calendarCard = qs('employee-calendar-card');
    const detailCard = qs('employee-shift-details-card');
    const adminShiftCard = qs('payroll-period-shifts-card');
    if (isAdminRole()) {
        switcher?.classList.add('hidden');
        salaryCard?.classList.remove('hidden');
        calendarCard?.classList.add('hidden');
        detailCard?.classList.add('hidden');
        adminShiftCard?.classList.remove('hidden');
        return;
    }
    if (!switcher) {
        salaryCard?.classList.remove('hidden');
        calendarCard?.classList.add('hidden');
        detailCard?.classList.remove('hidden');
        adminShiftCard?.classList.add('hidden');
        return;
    }

    switcher.classList.remove('hidden');
    const activeView = payrollState.employeeView === 'shifts' ? 'shifts' : 'salary';
    salaryCard?.classList.toggle('hidden', activeView !== 'salary');
    calendarCard?.classList.toggle('hidden', activeView !== 'shifts');
    detailCard?.classList.toggle('hidden', activeView !== 'shifts');
    adminShiftCard?.classList.add('hidden');

    document.querySelectorAll('[data-payroll-view]').forEach((button) => {
        const isActive = button.dataset.payrollView === activeView;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
}

function setEmployeePayrollView(view) {
    payrollState.employeeView = view === 'shifts' ? 'shifts' : 'salary';
    syncEmployeePayrollTabs();
}

function mergeCategoryCatalog(...sources) {
    const result = [];
    const seen = new Set();
    sources.flat().forEach((item) => {
        if (!item) return;
        const id = String(item.category_id || item.id || '').trim();
        const name = payrollCategoryDisplayName(item);
        const nameKey = normalizeSearch(name);
        if (!id || !name || seen.has(id) || seen.has(`name:${nameKey}`)) return;
        seen.add(id);
        seen.add(`name:${nameKey}`);
        result.push({ id, name });
    });
    result.sort((left, right) => left.name.localeCompare(right.name, 'ru'));
    payrollState.categoryCatalog = result;
    return result;
}

function renderShiftCategoryBreakdown(categories = []) {
    const rows = Array.isArray(categories) ? categories.filter((category) => {
        const net = Number(category?.net_sales_amount || 0);
        const earned = Number(category?.earning_amount || 0);
        const sales = Number(category?.sales_amount || 0);
        const returns = Number(category?.return_amount || 0);
        const cost = Number(category?.cost_amount || 0);
        return Math.abs(net) > 1e-9 || Math.abs(earned) > 1e-9 || Math.abs(sales) > 1e-9 || Math.abs(returns) > 1e-9 || Math.abs(cost) > 1e-9;
    }) : [];
    if (!rows.length) {
        return '<div class="muted-text">По этой смене нет начислений по категориям.</div>';
    }
    const totals = sumPayrollCategoryColumns(rows);
    return `
        <div class="table-wrap payroll-table-wrap payroll-shift-categories-wrap">
            <table class="table payroll-table payroll-category-table payroll-shift-category-table">
                <thead>
                    <tr>
                        <th>Категория</th>
                        <th>%</th>
                        <th>Продажи</th>
                        <th>Возвраты</th>
                        <th>Себестоимость</th>
                        <th>Начислено</th>
                        <th>Прибыль</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map((category) => `
                        <tr>
                            <td data-label="Категория">${escapeHtml(payrollCategoryDisplayName(category))}</td>
                            <td data-label="%">${formatPayrollCategoryRate(category)}</td>
                            <td data-label="Продажи">${formatMoney(category.sales_amount || 0)}</td>
                            <td data-label="Возвраты">${formatMoney(category.return_amount || 0)}</td>
                            <td data-label="Себестоимость">${formatMoney(category.cost_amount || 0)}</td>
                            <td data-label="Начислено"><strong>${formatMoney(category.earning_amount || 0)}</strong></td>
                            <td data-label="Прибыль"><strong>${formatMoney(normalizeCategoryProfit(category))}</strong></td>
                        </tr>
                    `).join('')}
                    <tr class="payroll-table-total-row">
                        <td data-label="Категория"><strong>Всего</strong></td>
                        <td data-label="%">—</td>
                        <td data-label="Продажи"><strong>${formatMoney(totals.sales_amount)}</strong></td>
                        <td data-label="Возвраты"><strong>${formatMoney(totals.return_amount)}</strong></td>
                        <td data-label="Себестоимость"><strong>${formatMoney(totals.cost_amount)}</strong></td>
                        <td data-label="Начислено"><strong>${formatMoney(totals.earning_amount)}</strong></td>
                        <td data-label="Прибыль"><strong>${formatMoney(totals.profit_amount)}</strong></td>
                    </tr>
                </tbody>
            </table>
        </div>
    `;
}

function renderShiftBonusComments(day) {
    const bonuses = Array.isArray(day?.employee_bonuses) ? day.employee_bonuses : [];
    if (!bonuses.length) return '';
    return `
        <div class="payroll-shift-section-head">
            <h3>Премии и комментарии</h3>
            <p class="muted-text">Сумма премии делится поровну на все смены сотрудника в месяце.</p>
        </div>
        <div class="expense-entry-grid payroll-shift-bonus-list">
            ${bonuses.map((bonus) => `
                <article class="expense-entry-card">
                    <div class="expense-entry-card-head">
                        <div>
                            <strong>${formatMoney(bonus.share_amount || 0)} за эту смену</strong>
                            <div class="muted-text">Всего премия: ${formatMoney(bonus.amount || 0)} · смен в месяце: ${Number(bonus.shift_count || 0)}</div>
                        </div>
                    </div>
                    ${bonus.comment ? `<div class="muted-text">${escapeHtml(bonus.comment)}</div>` : '<div class="muted-text">Комментарий не указан.</div>'}
                </article>
            `).join('')}
        </div>
    `;
}

function renderShiftDetailsInto(containerId, summary, { audience = 'admin' } = {}) {
    const container = qs(containerId);
    if (!container) return;
    const days = Array.isArray(summary?.days) ? summary.days : [];
    if (!days.length) {
        container.innerHTML = '<div class="muted-text">За выбранный период смен не найдено.</div>';
        return;
    }
    container.innerHTML = days.map((day) => {
        return `
            <details class="payroll-day-card payroll-day-card--accordion">
                <summary class="payroll-day-toggle">
                    <div>
                        <strong>${escapeHtml(day.shift_date || '')}</strong>
                        <div class="muted-text">${escapeHtml(day.employee_name || '')}${day.location ? ` · ${escapeHtml(day.location)}` : ''}</div>
                    </div>
                    <div class="payroll-day-toggle-side">
                        <span class="payroll-chip ${day.is_closed ? 'green' : 'orange'}">${day.is_closed ? 'Закрыта' : 'Открыта'}</span>
                        <span class="payroll-day-total-badge">${formatMoney(day.gross_salary_amount || 0)}</span>
                    </div>
                </summary>
                <div class="payroll-day-accordion-body">
                    <div class="payroll-day-grid payroll-day-grid--emphasis">
                        <div><span class="summary-label">Выручка</span><strong>${formatMoney(day.gross_sales_amount || 0)}</strong></div>
                        <div><span class="summary-label">Возвраты</span><strong>${formatMoney(day.return_amount || 0)}</strong></div>
                        <div><span class="summary-label">Чистая выручка</span><strong>${formatMoney(day.net_sales_amount || 0)}</strong></div>
                        <div><span class="summary-label">Выход</span><strong>${formatMoney(day.exit_amount || 0)}</strong></div>
                        <div><span class="summary-label">Бонус к выходу</span><strong>${formatMoney(day.bonus_amount || 0)}</strong></div>
                        <div><span class="summary-label">Бонус по категориям</span><strong>${formatMoney(day.category_earnings_total || 0)}</strong></div>
                        <div><span class="summary-label">Премия</span><strong>${formatMoney(day.employee_bonus_amount || 0)}</strong></div>
                        <div><span class="summary-label">Итог за смену</span><strong>${formatMoney(day.gross_salary_amount || 0)}</strong></div>
                    </div>
                    <div class="payroll-shift-section-head">
                        <h3>Начисления по категориям</h3>
                        <p class="muted-text">${audience === 'employee' ? 'По этой смене видно, сколько вам начислено по каждой категории.' : 'По этой смене видно, за какие категории сотрудник получил начисления.'}</p>
                    </div>
                    ${renderShiftCategoryBreakdown(day.categories || [])}
                    ${renderShiftBonusComments(day)}
                </div>
            </details>
        `;
    }).join('');
}

function renderSummary(summary) {
    payrollState.summary = summary;
    mergeCategoryCatalog(
        payrollState.categoryCatalog || [],
        payrollState.settings?.category_rates || [],
        summary?.categories || [],
    );
    qs('kpi-shifts').textContent = String((summary.days || []).length);
    qs('kpi-exit').textContent = formatMoney(summary.totals?.exit_amount || 0);
    qs('kpi-bonus').textContent = formatMoney(summary.totals?.bonus_amount || 0);
    qs('kpi-category').textContent = formatMoney(summary.totals?.category_earnings_total || 0);
    if (qs('kpi-employee-bonus')) qs('kpi-employee-bonus').textContent = formatMoney(summary.employee_bonuses_total ?? summary.totals?.employee_bonus_amount ?? 0);
    qs('kpi-employee-expenses').textContent = formatMoney(summary.employee_expenses_total || 0);
    qs('kpi-payout').textContent = formatMoney(summary.net_payout_amount || 0);

    const daysContainer = qs('payroll-days-container');
    if (daysContainer) {
        const shiftCount = Array.isArray(summary.days) ? summary.days.length : 0;
        if (!shiftCount) {
            daysContainer.innerHTML = '<div class="muted-text">За выбранный период смен не найдено. Когда смены появятся, общий итог и сводка по категориям заполнятся автоматически.</div>';
        } else if (isAdminRole()) {
            daysContainer.innerHTML = `<div class="muted-text">Найдено смен: <strong>${shiftCount}</strong>. Ниже есть отдельный блок с подробной детализацией по каждой смене за выбранный период.</div>`;
        } else {
            daysContainer.innerHTML = `<div class="muted-text">Найдено смен: <strong>${shiftCount}</strong>. Здесь показан общий итог по зарплате за период. Календарь и подробности по каждой смене доступны на отдельной странице <strong>«Смены»</strong>.</div>`;
        }
    }

    renderEmployeeShiftCalendar(summary);
    applyPayrollCategoryFiltersFromUi();
    if (isAdminRole()) {
        qs('payroll-period-shifts-card')?.classList.remove('hidden');
        renderShiftDetailsInto('payroll-period-shifts-container', summary, { audience: 'admin' });
        renderSettings();
    } else {
        renderShiftDetailsInto('employee-shift-details-container', summary, { audience: 'employee' });
    }
    syncEmployeePayrollTabs();
}

function getFilteredPayrollCategories(categories = []) {
    const search = normalizeSearch(payrollState.categoryFilters.search);
    const view = payrollState.categoryFilters.view || 'all';
    const sort = payrollState.categoryFilters.sort || 'earning_desc';

    const filtered = (categories || []).filter(category => {
        const categoryName = payrollCategoryDisplayName(category);
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
            return payrollCategoryDisplayName(left).localeCompare(payrollCategoryDisplayName(right), 'ru');
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

    const totals = sumPayrollCategoryColumns(filtered);
    categoryTbody.innerHTML = filtered.length
        ? `${filtered.map(category => `
            <tr>
                <td data-label="Категория">${escapeHtml(payrollCategoryDisplayName(category))}</td>
                <td data-label="%">${formatPayrollCategoryRate(category)}</td>
                <td data-label="Продажи">${formatMoney(category.sales_amount)}</td>
                <td data-label="Возвраты">${formatMoney(category.return_amount)}</td>
                <td data-label="Себестоимость">${formatMoney(category.cost_amount || 0)}</td>
                <td data-label="Начислено"><strong>${formatMoney(category.earning_amount)}</strong></td>
                <td data-label="Прибыль"><strong>${formatMoney(normalizeCategoryProfit(category))}</strong></td>
            </tr>
        `).join('')}
        <tr class="payroll-table-total-row">
            <td data-label="Категория"><strong>Всего</strong></td>
            <td data-label="%">—</td>
            <td data-label="Продажи"><strong>${formatMoney(totals.sales_amount)}</strong></td>
            <td data-label="Возвраты"><strong>${formatMoney(totals.return_amount)}</strong></td>
            <td data-label="Себестоимость"><strong>${formatMoney(totals.cost_amount)}</strong></td>
            <td data-label="Начислено"><strong>${formatMoney(totals.earning_amount)}</strong></td>
            <td data-label="Прибыль"><strong>${formatMoney(totals.profit_amount)}</strong></td>
        </tr>`
        : '<tr><td colspan="7" class="muted-text">По текущим фильтрам категории не найдены.</td></tr>';
}

function applyPayrollCategoryFiltersFromUi() {
    payrollState.categoryFilters.search = qs('payroll-category-search')?.value || '';
    payrollState.categoryFilters.view = qs('payroll-category-view')?.value || 'all';
    payrollState.categoryFilters.sort = qs('payroll-category-sort')?.value || 'earning_desc';
    renderPayrollCategoryTable(payrollState.summary?.categories || []);
}

window.applyPayrollCategoryFiltersFromUi = applyPayrollCategoryFiltersFromUi;

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
                    `<div class="employee-shift-line"><span>Премия</span><strong>${formatMoney(day.employee_bonus_amount || 0)}</strong></div>`,
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
    qs('manager-net-sales').textContent = formatMoney((summary.revenue_amount ?? summary.gross_sales_amount ?? summary.net_sales_amount) || 0);
    if (qs('manager-returns')) qs('manager-returns').textContent = formatMoney(summary.return_amount || 0);
    qs('manager-cost').textContent = formatMoney(summary.cost_amount || 0);
    qs('manager-employee-salary').textContent = formatMoney(summary.employee_salary_total || 0);
    qs('manager-expenses').textContent = formatMoney(summary.expenses_total || 0);
    qs('manager-profit').textContent = formatMoney(summary.operating_profit_before_manager_salary || 0);
    qs('manager-salary').textContent = summary.is_all_locations
        ? formatMoney(summary.manager_salary_amount || 0)
        : `${formatMoney(summary.manager_salary_amount || 0)} (${Number(summary.manager_rate_percent || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%)`;
    if (qs('manager-profit-after-manager')) qs('manager-profit-after-manager').textContent = formatMoney(summary.net_profit_after_manager_salary || 0);
    qs('manager-responsible-line').textContent = summary.is_all_locations
        ? 'Выбран режим «Все точки»: прибыль и зарплата управляющих суммируются по доступным точкам.'
        : (summary.responsible_admin_name
            ? `Ответственный управляющий точки: ${summary.responsible_admin_name}`
            : 'Ответственный управляющий для точки пока не назначен.');
}

function renderManagerBrackets() {
    const wrap = qs('settings-manager-brackets');
    const card = qs('settings-manager-brackets-card');
    if (!wrap || !card) return;
    if (!isSuperadminRole()) {
        card.classList.add('hidden');
        wrap.innerHTML = '';
        return;
    }
    card.classList.remove('hidden');
    const brackets = Array.isArray(payrollState.settings?.manager_salary_brackets) && payrollState.settings.manager_salary_brackets.length
        ? payrollState.settings.manager_salary_brackets
        : [
            { threshold: 200000, rate_percent: 25 },
            { threshold: 125000, rate_percent: 20 },
            { threshold: 100000, rate_percent: 15 },
            { threshold: 50000, rate_percent: 10 },
        ];
    wrap.innerHTML = brackets.map((row, index) => `
        <div class="settings-threshold-row">
            <label>Порог чистой прибыли<input type="number" min="0" step="0.01" data-manager-threshold value="${Number(row.threshold || 0)}"></label>
            <label>Процент управляющего<input type="number" min="0" step="0.01" data-manager-rate value="${Number(row.rate_percent || 0)}"></label>
            <button type="button" class="btn danger btn-inline" onclick="removeManagerBracket(${index})">Удалить</button>
        </div>
    `).join('');
}

window.removeManagerBracket = function removeManagerBracket(index) {
    if (!isSuperadminRole()) return;
    const rows = collectManagerBracketsFromUi();
    rows.splice(index, 1);
    payrollState.settings = { ...payrollState.settings, manager_salary_brackets: rows };
    renderManagerBrackets();
};

function renderSettings() {
    const card = qs('admin-settings-card');
    if (!isAdminRole() || isAllLocationsSelected()) {
        card.classList.add('hidden');
        return;
    }
    card.classList.remove('hidden');
    const settings = payrollState.settings || {};
    const selectedBonusCategories = new Set(settings.bonus_category_ids || []);
    const effectiveFromInputValue = payrollState.requestedSettingsEffectiveFrom || settings.effective_from || getStoredSettingsEffectiveFrom(selectedLocation()) || todayIso();
    qs('settings-effective-from').value = effectiveFromInputValue;
    qs('settings-exit').value = settings.exit_amount ?? 2000;
    qs('settings-threshold').value = settings.bonus_threshold ?? 40000;
    qs('settings-bonus').value = settings.bonus_amount ?? 500;
    qs('settings-other-rate').value = settings.other_rate_percent ?? 3;
    qs('settings-admin-select').innerHTML = ['<option value="">—</option>', ...payrollState.admins.map(admin => `<option value="${admin.id}">${escapeHtml(admin.full_name)} (${roleDisplayName(admin.role)})</option>`)].join('');
    if (settings.responsible_admin_user_id) qs('settings-admin-select').value = String(settings.responsible_admin_user_id);
    const existing = new Map((settings.category_rates || []).map(item => [item.category_id, item.rate_percent]));
    qs('settings-category-rates').innerHTML = payrollState.categoryCatalog.map(category => {
        const isExcludedFromExitBonus = selectedBonusCategories.size > 0 && !selectedBonusCategories.has(category.id);
        return `
        <label class="settings-rate-card">
            <span class="settings-rate-name">${escapeHtml(category.name)}</span>
            <label class="checkbox-like expense-checkbox-card expense-checkbox-card--inline settings-bonus-checkbox">
                <input
                    type="checkbox"
                    data-bonus-category-id="${escapeHtml(category.id)}"
                    ${isExcludedFromExitBonus ? 'checked' : ''}
                >
                Исключить из бонуса к выходу
            </label>
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
    `}).join('');
    renderManagerBrackets();
    const categoryLoadingVisible = !qs('settings-category-loading')?.classList.contains('hidden');
    setSettingsCategoryInputsDisabled(categoryLoadingVisible);
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
    if (!isAdminRole() || isAllLocationsSelected()) {
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


function expenseEmployeeName(entry) {
    if (!entry?.assigned_employee_user_id) return 'Без привязки';
    return payrollState.employees.find(item => Number(item.id) === Number(entry.assigned_employee_user_id))?.full_name || 'Сотрудник';
}

function syncExpenseEntriesToggle() {
    const list = qs('expense-entry-tbody');
    const button = qs('expense-entries-toggle-btn');
    if (!list || !button) return;
    const hidden = list.classList.contains('hidden');
    button.textContent = hidden ? 'Развернуть расходы' : 'Свернуть расходы';
}

function toggleExpenseEntries() {
    const list = qs('expense-entry-tbody');
    if (!list) return;
    list.classList.toggle('hidden');
    payrollState.expensesCollapsed = list.classList.contains('hidden');
    syncExpenseEntriesToggle();
}

function syncExpenseEntryToggle(details) {
    if (!details) return;
    const toggle = details.querySelector('.expense-entry-toggle-text');
    if (!toggle) return;
    toggle.textContent = details.open ? 'Свернуть' : 'Развернуть';
}

function renderExpenses() {
    const employeeOptions = ['<option value="">Без привязки</option>', ...payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`)].join('');
    const list = qs('expense-entry-tbody');
    if (!list) return;
    list.innerHTML = (payrollState.expenses || []).map(entry => {
        const title = escapeHtml(entry.name || entry.template_name || 'Расход');
        const distributionText = entry.distribution_mode === 'spread'
            ? 'Растянут на весь месяц'
            : `Списывается одним днём · ${escapeHtml(formatDateRu(entry.expense_date))}`;
        const subtitle = entry.is_manual
            ? `Свободный расход без шаблона · ${distributionText}`
            : `${entry.amount_type === 'static' ? 'Статический расход' : 'Динамический расход'} · ${distributionText}`;
        const employeeName = escapeHtml(expenseEmployeeName(entry));
        const paidLabel = entry.is_paid ? 'Оплачен' : 'Не оплачен · в статистику не входит';
        const amountLabel = formatMoney(entry.amount);
        return `
        <details class="expense-entry-card ${entry.is_manual ? 'manual' : ''} expense-entry-card--accordion">
            <summary class="expense-entry-toggle">
                <div>
                    <h4>${title}</h4>
                    <p class="muted-text">${subtitle}</p>
                </div>
                <div class="expense-entry-toggle-side">
                    <span class="expense-entry-badge">${amountLabel}</span>
                    <span class="muted-text expense-entry-toggle-meta">${employeeName} · ${paidLabel}</span>
                    <span class="btn secondary btn-inline expense-entry-toggle-text">Развернуть</span>
                </div>
            </summary>
            <div class="expense-entry-accordion-body">
                <div class="expense-entry-form">
                    <label>
                        Сумма
                        <input type="number" min="0" step="0.01" data-expense-amount="${entry.id}" value="${entry.amount}">
                    </label>
                    <label>
                        Как учитывать
                        <select data-expense-mode="${entry.id}">
                            <option value="spread">Растянуть на месяц</option>
                            <option value="single_day">Одним днём</option>
                        </select>
                    </label>
                    <label>
                        Дата расхода
                        <input type="date" data-expense-date="${entry.id}" value="${escapeHtml(entry.expense_date || entry.month_start || '')}">
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
                    <button type="button" class="btn secondary btn-inline btn-with-loader" data-expense-save="${entry.id}" onclick="saveExpenseEntry(${entry.id})">
                        <span class="btn-loader hidden" aria-hidden="true"></span>
                        <span class="btn-label">Сохранить расход</span>
                    </button>
                    ${entry.is_manual ? `<button type="button" class="btn danger btn-inline" onclick="deleteExpenseEntry(${entry.id})">Удалить</button>` : ''}
                </div>
                <div id="expense-status-${entry.id}" class="inventory-status hidden save-action-status"></div>
            </div>
        </details>`;
    }).join('') || '<div class="empty-text">Нет расходов за выбранный месяц.</div>';
    (payrollState.expenses || []).forEach(entry => {
        const select = document.querySelector(`[data-expense-employee="${entry.id}"]`);
        if (select && entry.assigned_employee_user_id) select.value = String(entry.assigned_employee_user_id);
        const modeSelect = document.querySelector(`[data-expense-mode="${entry.id}"]`);
        if (modeSelect) modeSelect.value = entry.distribution_mode || 'spread';
    });
    list.classList.toggle('hidden', payrollState.expensesCollapsed);
    syncExpenseEntriesToggle();
    list.querySelectorAll('.expense-entry-card--accordion').forEach((details) => {
        syncExpenseEntryToggle(details);
        details.addEventListener('toggle', () => syncExpenseEntryToggle(details));
    });
}



function bonusEmployeeName(entry) {
    return payrollState.employees.find(item => Number(item.id) === Number(entry?.employee_user_id))?.full_name
        || entry?.employee_name
        || 'Сотрудник';
}

function renderEmployeeBonuses() {
    const card = qs('employee-bonuses-card');
    const list = qs('employee-bonus-entry-tbody');
    if (!isAdminRole() || isAllLocationsSelected()) {
        card?.classList.add('hidden');
        return;
    }
    card?.classList.remove('hidden');
    if (!list) return;
    const employeeOptions = ['<option value="">Выберите сотрудника</option>', ...payrollState.employees.map(item => `<option value="${item.id}">${escapeHtml(item.full_name)}</option>`)].join('');
    list.innerHTML = (payrollState.employeeBonuses || []).map(entry => {
        const employeeName = escapeHtml(bonusEmployeeName(entry));
        const stateLabel = entry.is_active ? 'Активна' : 'Неактивна';
        return `
        <details class="expense-entry-card expense-entry-card--accordion ${entry.is_active ? '' : 'inactive'}">
            <summary class="expense-entry-toggle">
                <div>
                    <h4>Премия · ${employeeName}</h4>
                    <p class="muted-text">${escapeHtml(formatDateRu(entry.bonus_date || entry.month_start))}</p>
                </div>
                <div class="expense-entry-toggle-side">
                    <span class="expense-entry-badge">${formatMoney(entry.amount || 0)}</span>
                    <span class="muted-text expense-entry-toggle-meta">${stateLabel}</span>
                    <span class="btn secondary btn-inline expense-entry-toggle-text">Развернуть</span>
                </div>
            </summary>
            <div class="expense-entry-accordion-body">
                <div class="expense-entry-form">
                    <label>
                        Сотрудник
                        <select data-employee-bonus-employee="${entry.id}">${employeeOptions}</select>
                    </label>
                    <label>
                        Сумма
                        <input type="number" min="0" step="0.01" data-employee-bonus-amount="${entry.id}" value="${entry.amount}">
                    </label>
                    <label>
                        Дата премии
                        <input type="date" data-employee-bonus-date="${entry.id}" value="${escapeHtml(entry.bonus_date || entry.month_start || '')}">
                    </label>
                    <label class="checkbox-like expense-checkbox-card expense-checkbox-card--inline">
                        <input type="checkbox" data-employee-bonus-active="${entry.id}" ${entry.is_active ? 'checked' : ''}>
                        Учитывать в зарплате
                    </label>
                    <label class="expense-comment-field expense-entry-comment">
                        Комментарий
                        <textarea rows="3" data-employee-bonus-comment="${entry.id}" placeholder="Комментарий к премии">${escapeHtml(entry.comment || '')}</textarea>
                    </label>
                </div>
                <div class="expense-entry-actions">
                    <button type="button" class="btn secondary btn-inline btn-with-loader" data-employee-bonus-save="${entry.id}" onclick="saveEmployeeBonus(${entry.id})">
                        <span class="btn-loader hidden" aria-hidden="true"></span>
                        <span class="btn-label">Сохранить премию</span>
                    </button>
                    <button type="button" class="btn danger btn-inline" onclick="deleteEmployeeBonus(${entry.id})">Удалить</button>
                </div>
                <div id="employee-bonus-status-${entry.id}" class="inventory-status hidden save-action-status"></div>
            </div>
        </details>`;
    }).join('') || '<div class="empty-text">Премий за выбранный месяц пока нет.</div>';
    (payrollState.employeeBonuses || []).forEach(entry => {
        const select = document.querySelector(`[data-employee-bonus-employee="${entry.id}"]`);
        if (select) select.value = String(entry.employee_user_id || '');
    });
    list.querySelectorAll('.expense-entry-card--accordion').forEach((details) => {
        syncExpenseEntryToggle(details);
        details.addEventListener('toggle', () => syncExpenseEntryToggle(details));
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
    if (log.entity_type === 'payroll_settings' && log.action_type === 'update_version') {
        return `${when} · ${actor} обновил правила зарплаты с датой вступления ${formatDateRu(details.effective_from)}.`;
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

    if (log.entity_type === 'employee_bonus') {
        const employeeName = payrollState.employees.find(item => Number(item.id) === Number(details.employee_user_id))?.full_name || details.employee_name || 'сотруднику';
        if (log.action_type === 'create') {
            return `${when} · ${actor} добавил премию ${employeeName} на ${formatMoney(details.amount || 0)}.`;
        }
        if (log.action_type === 'update') {
            const after = details.after || {};
            const name = payrollState.employees.find(item => Number(item.id) === Number(after.employee_user_id))?.full_name || after.employee_name || employeeName;
            return `${when} · ${actor} изменил премию ${name}.`;
        }
        if (log.action_type === 'delete') {
            return `${when} · ${actor} удалил премию ${employeeName} на ${formatMoney(details.amount || 0)}.`;
        }
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
    stopRecalcPolling();
    payrollState.activeRecalcJob = null;
    syncAllLocationsModeControls();
    if (isAllLocationsSelected(location)) {
        payrollState.settings = null;
        payrollState.employees = [];
        payrollState.admins = [];
        renderUsersForLocation();
        renderSettings();
        renderTemplates();
        renderEmployeeBonuses();
        await loadAudit();
        return;
    }
    const requestedEffectiveFrom = selectedSettingsEffectiveFrom() || getStoredSettingsEffectiveFrom(location);
    const effectiveFromQuery = requestedEffectiveFrom ? `&effective_from=${encodeURIComponent(requestedEffectiveFrom)}` : '';
    setSettingsLoading(true, 'Загружаем настройки точки...');
    try {
        const setup = await api(`/api/payroll/settings?location=${encodeURIComponent(location)}${effectiveFromQuery}`);
        payrollState.requestedSettingsEffectiveFrom = setup.requested_effective_from || requestedEffectiveFrom || setup.settings?.effective_from || '';
        storeSettingsEffectiveFrom(location, payrollState.requestedSettingsEffectiveFrom);
        payrollState.settings = setup.settings;
        payrollState.employees = setup.employees || [];
        payrollState.admins = setup.admins || [];
        mergeCategoryCatalog(setup.category_catalog || [], payrollState.settings?.category_rates || []);
        renderUsersForLocation();
        renderSettings();

        await Promise.all([
            loadShiftCalendar(),
            loadExpenseTemplatesAndEntries(),
            loadEmployeeBonuses(),
            loadAudit(),
        ]);

        if (setup?.recalc_job?.job_id) {
            pollRecalcStatus(setup.recalc_job.job_id).catch((error) => console.error(error));
        }
    } finally {
        setSettingsLoading(false);
    }
}

async function loadSummary(options = {}) {
    const silent = Boolean(options?.silent);
    if (payrollState.summaryLoadingPromise) {
        return payrollState.summaryLoadingPromise;
    }

    const run = (async () => {
        const location = selectedLocation();
        const dateFrom = qs('payroll-date-from').value;
        const dateTo = qs('payroll-date-to').value;
        if (!location || !dateFrom || !dateTo) return null;
        syncAllLocationsModeControls();
        if (!silent) {
            showStatus('Загружаем расчёт зарплаты...', 'loading');
        }
        try {
            const employeeId = selectedEmployeeId();
            const employeeQuery = employeeId ? `&employee_user_id=${employeeId}` : '';
            const summary = await api(`/api/payroll/employee-summary?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}${employeeQuery}`);
            renderSummary(summary);
            if (isAdminRole()) {
                const managerSummary = await api(`/api/payroll/manager-summary?location=${encodeURIComponent(location)}&date_from=${dateFrom}&date_to=${dateTo}`);
                renderManagerSummary(managerSummary);
            }
            if (silent) {
                payrollState.lastCurrentShiftAutoRefreshAt = new Date();
            } else {
                showStatus('Данные обновлены.', 'success');
                setTimeout(hideStatus, 1500);
            }
            return summary;
        } catch (error) {
            console.error(error);
            if (!silent) {
                showStatus(error.message || 'Не удалось загрузить зарплату.', 'error');
            }
            return null;
        }
    })();

    payrollState.summaryLoadingPromise = run;
    try {
        return await run;
    } finally {
        if (payrollState.summaryLoadingPromise === run) {
            payrollState.summaryLoadingPromise = null;
        }
    }
}

window.payrollLoadSummary = loadSummary;

async function loadShiftCalendar() {
    if (!isAdminRole() || !qs('shift-month-input') || !qs('shift-calendar-grid')) return;
    const location = selectedLocation();
    const month = selectedMonthStart('shift-month-input');
    const [year, mon] = month.split('-').map(Number);
    const dateFrom = `${year}-${String(mon).padStart(2, '0')}-01`;
    const dateTo = formatLocalDateIso(new Date(year, mon, 0));
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

async function refreshCurrentShiftAutomatically() {
    if (payrollState.currentShiftAutoRefreshInProgress) return;
    if (!shouldAutoRefreshCurrentShift()) return;

    const lockKey = acquireCurrentShiftAutoRefreshLock();
    if (!lockKey) return;

    payrollState.currentShiftAutoRefreshInProgress = true;
    try {
        await loadSummary({ silent: true });
        if (isAdminRole() && selectedShiftCalendarIncludesToday()) {
            await loadShiftCalendar();
        }
    } catch (error) {
        console.error(error);
    } finally {
        payrollState.currentShiftAutoRefreshInProgress = false;
        releaseCurrentShiftAutoRefreshLock(lockKey);
    }
}

function startCurrentShiftAutoRefresh() {
    if (payrollState.currentShiftAutoRefreshTimer) {
        clearInterval(payrollState.currentShiftAutoRefreshTimer);
        payrollState.currentShiftAutoRefreshTimer = null;
    }
}

async function loadExpenseTemplatesAndEntries() {
    if (!isAdminRole() || isAllLocationsSelected()) {
        payrollState.templates = [];
        payrollState.expenses = [];
        renderTemplates();
        renderExpenses();
        return;
    }
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
    syncManualExpenseDefaults();
}


function collectManagerBracketsFromUi() {
    return [...document.querySelectorAll('.settings-threshold-row')]
        .map(row => ({
            threshold: Number(row.querySelector('[data-manager-threshold]')?.value || 0),
            rate_percent: Number(row.querySelector('[data-manager-rate]')?.value || 0),
        }))
        .filter(item => Number.isFinite(item.threshold) && Number.isFinite(item.rate_percent));
}

window.addManagerBracket = function addManagerBracket() {
    if (!isSuperadminRole()) return;
    const rows = collectManagerBracketsFromUi();
    rows.push({ threshold: 0, rate_percent: 0 });
    payrollState.settings = { ...payrollState.settings, manager_salary_brackets: rows };
    renderManagerBrackets();
};

async function saveSettings() {
    if (isAllLocationsSelected()) {
        showStatus('В режиме «Все точки» настройки процентов недоступны. Выберите конкретную точку.', 'warning');
        return;
    }
    const button = qs('save-settings-btn');
    const categoryRates = [...document.querySelectorAll('[data-category-rate-id]')]
        .map(input => ({
            category_id: input.dataset.categoryRateId,
            category_name: input.dataset.categoryRateName,
            rate_percent: input.value === '' ? null : Number(input.value),
        }))
        .filter(item => item.rate_percent !== null && Number.isFinite(item.rate_percent));
    const excludedBonusCategoryIds = new Set(
        [...document.querySelectorAll('[data-bonus-category-id]:checked')]
            .map(input => input.dataset.bonusCategoryId)
            .filter(Boolean)
    );
    const bonusCategoryIds = (payrollState.categoryCatalog || [])
        .map(category => category.id)
        .filter(categoryId => categoryId && !excludedBonusCategoryIds.has(categoryId));
    const payload = {
        location: selectedLocation(),
        effective_from: qs('settings-effective-from').value,
        exit_amount: Number(qs('settings-exit').value || 0),
        bonus_threshold: Number(qs('settings-threshold').value || 0),
        bonus_amount: Number(qs('settings-bonus').value || 0),
        other_rate_percent: Number(qs('settings-other-rate').value || 0),
        responsible_admin_user_id: qs('settings-admin-select').value ? Number(qs('settings-admin-select').value) : null,
        bonus_category_ids: bonusCategoryIds,
        manager_salary_brackets: isSuperadminRole() ? collectManagerBracketsFromUi() : [],
        category_rates: categoryRates,
    };
    showStatus('Сохраняем новую версию правил...', 'loading');
    showScopedStatus('settings-status', 'Сохраняем правила...', 'loading');
    setButtonLoading(button, true, 'Сохраняем...');
    try {
        payrollState.requestedSettingsEffectiveFrom = payload.effective_from || '';
        storeSettingsEffectiveFrom(selectedLocation(), payrollState.requestedSettingsEffectiveFrom);
        const response = await api('/api/payroll/settings', { method: 'PUT', body: JSON.stringify(payload) });
        payrollState.requestedSettingsEffectiveFrom = response.requested_effective_from || payload.effective_from || payrollState.requestedSettingsEffectiveFrom;
        storeSettingsEffectiveFrom(selectedLocation(), payrollState.requestedSettingsEffectiveFrom);
        payrollState.settings = response.settings || payrollState.settings;
        payrollState.employees = response.employees || payrollState.employees;
        payrollState.admins = response.admins || payrollState.admins;
        mergeCategoryCatalog(response.category_catalog || [], payrollState.settings?.category_rates || [], payrollState.categoryCatalog || []);
        renderUsersForLocation();
        renderSettings();
        await Promise.all([
            loadSummary(),
            loadExpenseTemplatesAndEntries(),
            loadEmployeeBonuses(),
            loadAudit(),
            loadShiftCalendar(),
        ]);
        const recalcJobId = response?.recalc_job?.job_id;
        if (recalcJobId) {
            payrollState.activeRecalcJob = response.recalc_job;
            const queuedMessage = 'Версия правил сохранена. Запущен живой пересчёт смен по выбранному периоду...';
            showStatus(queuedMessage, 'loading');
            showScopedStatus('settings-status', queuedMessage, 'loading');
            pollRecalcStatus(recalcJobId).catch((error) => console.error(error));
        } else {
            const successMessage = payload.effective_from
                ? `Версия правил сохранена. Новые правила применяются с ${formatDateRu(payload.effective_from)}.`
                : 'Версия правил сохранена.';
            showStatus(successMessage, 'success');
            showScopedStatus('settings-status', successMessage, 'success');
        }
    } catch (error) {
        console.error(error);
        showStatus(error.message || 'Не удалось сохранить правила.', 'error');
        showScopedStatus('settings-status', error.message || 'Не удалось сохранить правила.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}

window.saveSettings = saveSettings;
window.openShiftModal = openShiftModal;

function openShiftModal(dateValue) {
    const modal = qs('shift-modal');
    if (!modal) return;
    qs('shift-modal-date-input').value = dateValue || todayIso();
    if (qs('shift-modal-employee-select') && qs('shift-employee-select')) {
        qs('shift-modal-employee-select').value = qs('shift-employee-select').value || '';
    }
    modal.classList.remove('hidden');
}

function closeShiftModal() {
    const modal = qs('shift-modal');
    if (!modal) return;
    modal.classList.add('hidden');
}

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
    if (isAllLocationsSelected()) {
        showStatus('В режиме «Все точки» расходы недоступны. Выберите конкретную точку.', 'warning');
        return;
    }
    const button = qs('create-expense-template-btn');
    const payload = {
        location: selectedLocation(),
        name: qs('expense-template-name').value,
        amount_type: qs('expense-template-type').value,
        default_amount: qs('expense-template-default').value ? Number(qs('expense-template-default').value) : null,
        assign_to_employee_by_default: qs('expense-template-employee').checked,
    };
    showScopedStatus('create-expense-template-status', 'Сохраняем шаблон расхода...', 'loading');
    setButtonLoading(button, true, 'Сохраняем...');
    try {
        await api('/api/payroll/expense-templates', { method: 'POST', body: JSON.stringify(payload) });
        qs('expense-template-name').value = '';
        qs('expense-template-default').value = '';
        qs('expense-template-employee').checked = false;
        await loadExpenseTemplatesAndEntries();
        await loadAudit();
        showStatus('Шаблон расхода добавлен.', 'success');
        showScopedStatus('create-expense-template-status', 'Шаблон расхода сохранён.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось добавить шаблон.', 'error');
        showScopedStatus('create-expense-template-status', error.message || 'Не удалось добавить шаблон.', 'error');
    } finally {
        setButtonLoading(button, false);
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
    const button = document.querySelector(`[data-expense-save="${id}"]`);
    const amount = Number(document.querySelector(`[data-expense-amount="${id}"]`).value || 0);
    const isPaid = document.querySelector(`[data-expense-paid="${id}"]`).checked;
    const employeeValue = document.querySelector(`[data-expense-employee="${id}"]`).value;
    const applyToEmployeeSalary = document.querySelector(`[data-expense-apply="${id}"]`).checked;
    const distributionMode = document.querySelector(`[data-expense-mode="${id}"]`)?.value || 'spread';
    const expenseDate = document.querySelector(`[data-expense-date="${id}"]`)?.value || '';
    const comment = document.querySelector(`[data-expense-comment="${id}"]`)?.value || '';
    const payload = {
        amount,
        is_paid: isPaid,
        assigned_employee_user_id: employeeValue ? Number(employeeValue) : null,
        apply_to_employee_salary: applyToEmployeeSalary,
        distribution_mode: distributionMode,
        expense_date: expenseDate || null,
        comment,
    };
    showScopedStatus(`expense-status-${id}`, 'Сохраняем расход...', 'loading');
    setButtonLoading(button, true, 'Сохраняем...');
    try {
        await api(`/api/payroll/expenses/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        await loadExpenseTemplatesAndEntries();
        await loadSummary();
        await loadAudit();
        showStatus('Расход сохранён.', 'success');
        showScopedStatus(`expense-status-${id}`, 'Расход сохранён.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось сохранить расход.', 'error');
        showScopedStatus(`expense-status-${id}`, error.message || 'Не удалось сохранить расход.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
};

async function createManualExpense() {
    if (isAllLocationsSelected()) {
        showStatus('В режиме «Все точки» расходы недоступны. Выберите конкретную точку.', 'warning');
        return;
    }
    const button = qs('create-manual-expense-btn');
    const payload = {
        location: selectedLocation(),
        month_start: selectedMonthStart('expenses-month-input'),
        name: qs('manual-expense-name').value.trim(),
        amount: Number(qs('manual-expense-amount').value || 0),
        distribution_mode: qs('manual-expense-mode').value || 'single_day',
        expense_date: qs('manual-expense-date').value || null,
        assigned_employee_user_id: qs('manual-expense-employee').value ? Number(qs('manual-expense-employee').value) : null,
        is_paid: qs('manual-expense-paid').checked,
        apply_to_employee_salary: qs('manual-expense-apply').checked,
        comment: qs('manual-expense-comment').value.trim(),
    };
    if (!payload.name) {
        showStatus('Введите название.', 'error');
        showScopedStatus('create-manual-expense-status', 'Введите название.', 'error');
        return;
    }
    showScopedStatus('create-manual-expense-status', 'Сохраняем свободный расход...', 'loading');
    setButtonLoading(button, true, 'Сохраняем...');
    try {
        await api('/api/payroll/expenses/manual', { method: 'POST', body: JSON.stringify(payload) });
        qs('manual-expense-name').value = '';
        qs('manual-expense-amount').value = '';
        qs('manual-expense-mode').value = 'single_day';
        qs('manual-expense-employee').value = '';
        qs('manual-expense-paid').checked = false;
        qs('manual-expense-apply').checked = false;
        qs('manual-expense-comment').value = '';
        syncManualExpenseDefaults({ forceDate: true });
        await loadExpenseTemplatesAndEntries();
        await loadSummary();
        await loadAudit();
        showStatus('Свободный расход добавлен.', 'success');
        showScopedStatus('create-manual-expense-status', 'Свободный расход сохранён.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось добавить свободный расход.', 'error');
        showScopedStatus('create-manual-expense-status', error.message || 'Не удалось добавить свободный расход.', 'error');
    } finally {
        setButtonLoading(button, false);
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

async function createEmployeeBonus() {
    if (isAllLocationsSelected()) {
        showStatus('В режиме «Все точки» премии недоступны. Выберите конкретную точку.', 'warning');
        return;
    }
    const button = qs('create-employee-bonus-btn');
    const payload = {
        location: selectedLocation(),
        month_start: selectedMonthStart('employee-bonuses-month-input'),
        employee_user_id: qs('employee-bonus-employee').value ? Number(qs('employee-bonus-employee').value) : null,
        amount: Number(qs('employee-bonus-amount').value || 0),
        bonus_date: qs('employee-bonus-date').value || null,
        comment: qs('employee-bonus-comment').value.trim(),
    };
    if (!payload.employee_user_id) {
        showStatus('Выберите сотрудника для премии.', 'error');
        showScopedStatus('create-employee-bonus-status', 'Выберите сотрудника.', 'error');
        return;
    }
    if (!payload.amount || payload.amount <= 0) {
        showStatus('Введите сумму премии.', 'error');
        showScopedStatus('create-employee-bonus-status', 'Введите сумму премии.', 'error');
        return;
    }
    showScopedStatus('create-employee-bonus-status', 'Сохраняем премию...', 'loading');
    setButtonLoading(button, true, 'Сохраняем...');
    try {
        await api('/api/payroll/employee-bonuses', { method: 'POST', body: JSON.stringify(payload) });
        qs('employee-bonus-amount').value = '';
        qs('employee-bonus-comment').value = '';
        syncEmployeeBonusDefaults({ forceDate: true });
        await loadEmployeeBonuses();
        await loadSummary();
        await loadAudit();
        showStatus('Премия добавлена.', 'success');
        showScopedStatus('create-employee-bonus-status', 'Премия сохранена.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось добавить премию.', 'error');
        showScopedStatus('create-employee-bonus-status', error.message || 'Не удалось добавить премию.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
}

window.saveEmployeeBonus = async function saveEmployeeBonus(id) {
    const button = document.querySelector(`[data-employee-bonus-save="${id}"]`);
    const employeeValue = document.querySelector(`[data-employee-bonus-employee="${id}"]`)?.value || '';
    const payload = {
        employee_user_id: employeeValue ? Number(employeeValue) : null,
        amount: Number(document.querySelector(`[data-employee-bonus-amount="${id}"]`)?.value || 0),
        bonus_date: document.querySelector(`[data-employee-bonus-date="${id}"]`)?.value || null,
        comment: document.querySelector(`[data-employee-bonus-comment="${id}"]`)?.value || '',
        is_active: Boolean(document.querySelector(`[data-employee-bonus-active="${id}"]`)?.checked),
    };
    if (!payload.employee_user_id) {
        showScopedStatus(`employee-bonus-status-${id}`, 'Выберите сотрудника.', 'error');
        return;
    }
    showScopedStatus(`employee-bonus-status-${id}`, 'Сохраняем премию...', 'loading');
    setButtonLoading(button, true, 'Сохраняем...');
    try {
        await api(`/api/payroll/employee-bonuses/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        await loadEmployeeBonuses();
        await loadSummary();
        await loadAudit();
        showStatus('Премия сохранена.', 'success');
        showScopedStatus(`employee-bonus-status-${id}`, 'Премия сохранена.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось сохранить премию.', 'error');
        showScopedStatus(`employee-bonus-status-${id}`, error.message || 'Не удалось сохранить премию.', 'error');
    } finally {
        setButtonLoading(button, false);
    }
};

window.deleteEmployeeBonus = async function deleteEmployeeBonus(id) {
    if (!confirm('Удалить эту премию?')) return;
    try {
        await api(`/api/payroll/employee-bonuses/${id}`, { method: 'DELETE' });
        await loadEmployeeBonuses();
        await loadSummary();
        await loadAudit();
        showStatus('Премия удалена.', 'success');
    } catch (error) {
        showStatus(error.message || 'Не удалось удалить премию.', 'error');
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

async function bootstrap() {
    setDefaultDates();
    initializeCollapseSections();
    if (!isAdminRole()) {
        payrollState.employeeView = getRequestedPayrollView();
    }
    syncEmployeePayrollTabs();
    payrollState.locations = fallbackLocations();
    renderLocations();
    try {
        const access = await api('/api/payroll/access');
        payrollState.locations = normalizeLocationList(access.locations || []);
        renderLocations();
        await loadSetupForLocation();
        await loadSummary();
        startCurrentShiftAutoRefresh();
    } catch (error) {
        console.error(error);
        payrollState.locations = fallbackLocations();
        renderLocations();
        if (selectedLocation()) {
            try {
                await loadSetupForLocation();
                await loadSummary();
                startCurrentShiftAutoRefresh();
                showStatus('Список точек из API не загрузился, показана базовая точка.', 'warning');
                return;
            } catch (fallbackError) {
                console.error(fallbackError);
            }
        }
        showStatus(error.message || 'Не удалось загрузить страницу зарплаты.', 'error');
    }
}

qs('payroll-location-select').addEventListener('change', async () => {
    const location = selectedLocation();
    payrollState.requestedSettingsEffectiveFrom = getStoredSettingsEffectiveFrom(location) || '';
    syncAllLocationsModeControls();
    await loadSetupForLocation();
    await loadSummary();
});
qs('settings-effective-from')?.addEventListener('change', async (event) => {
    const nextValue = String(event?.target?.value || '').trim();
    payrollState.requestedSettingsEffectiveFrom = nextValue;
    storeSettingsEffectiveFrom(selectedLocation(), nextValue);
    try {
        await loadSetupForLocation();
    } catch (error) {
        console.error(error);
        showScopedStatus('settings-status', error.message || 'Не удалось загрузить правила на выбранную дату.', 'error');
    }
});
qs('payroll-employee-select')?.addEventListener('change', loadSummary);
qs('payroll-date-from')?.addEventListener('change', () => {
    if (shouldAutoRefreshCurrentShift()) {
        refreshCurrentShiftAutomatically().catch((error) => console.error(error));
    }
});
qs('payroll-date-to')?.addEventListener('change', () => {
    if (shouldAutoRefreshCurrentShift()) {
        refreshCurrentShiftAutomatically().catch((error) => console.error(error));
    }
});
qs('settings-add-manager-bracket-btn')?.addEventListener('click', () => window.addManagerBracket());
qs('add-shift-btn')?.addEventListener('click', addShift);
qs('shift-month-input')?.addEventListener('change', loadShiftCalendar);
qs('expenses-month-input')?.addEventListener('change', async () => {
    syncManualExpenseDefaults({ forceDate: true });
    await loadExpenseTemplatesAndEntries();
});
qs('employee-bonuses-month-input')?.addEventListener('change', async () => {
    syncEmployeeBonusDefaults({ forceDate: true });
    await loadEmployeeBonuses();
});
qs('create-expense-template-btn')?.addEventListener('click', createExpenseTemplate);
qs('create-manual-expense-btn')?.addEventListener('click', createManualExpense);
qs('create-employee-bonus-btn')?.addEventListener('click', createEmployeeBonus);
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
qs('expense-entries-toggle-btn')?.addEventListener('click', toggleExpenseEntries);
qs('audit-toggle-btn')?.addEventListener('click', () => {
    const list = qs('audit-log-list');
    const button = qs('audit-toggle-btn');
    const hidden = list.classList.toggle('hidden');
    button.textContent = hidden ? 'Развернуть журнал' : 'Свернуть журнал';
});
qs('audit-date-filter')?.addEventListener('change', renderAudit);
qs('audit-employee-filter')?.addEventListener('change', renderAudit);
qs('audit-clear-filters-btn')?.addEventListener('click', clearAuditFilters);
document.querySelectorAll('[data-payroll-view]')?.forEach((button) => {
    button.addEventListener('click', () => setEmployeePayrollView(button.dataset.payrollView));
});
document.querySelectorAll('.payroll-collapse').forEach((details) => {
    details.addEventListener('toggle', () => syncCollapseToggleText(details));
});


document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !qs('shift-modal')?.classList.contains('hidden')) {
        closeShiftModal();
    }
});

window.addEventListener('resize', () => {
    const nextWidth = window.innerWidth || 0;
    if (nextWidth === payrollState.lastViewportWidth) return;
    payrollState.lastViewportWidth = nextWidth;
    if (payrollState.summary) {
        renderSummary(payrollState.summary);
    }
});

bootstrap();
