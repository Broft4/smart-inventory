const ADMIN_UI_STATE_STORAGE_KEY = 'smart_inventory_admin_ui_state';

function loadPersistedAdminUiState() {
    try {
        const raw = localStorage.getItem(ADMIN_UI_STATE_STORAGE_KEY);
        if (!raw) return { selectedLocation: '', selectedReportId: null, selectedReportIdByLocation: {} };
        const parsed = JSON.parse(raw);
        return {
            selectedLocation: typeof parsed?.selectedLocation === 'string' ? parsed.selectedLocation : '',
            selectedReportId: Number.isFinite(Number(parsed?.selectedReportId)) ? Number(parsed.selectedReportId) : null,
            selectedReportIdByLocation: parsed?.selectedReportIdByLocation && typeof parsed.selectedReportIdByLocation === 'object'
                ? parsed.selectedReportIdByLocation
                : {},
        };
    } catch {
        return { selectedLocation: '', selectedReportId: null, selectedReportIdByLocation: {} };
    }
}

function persistAdminUiState() {
    try {
        localStorage.setItem(ADMIN_UI_STATE_STORAGE_KEY, JSON.stringify({
            selectedLocation: adminState.selectedLocation || '',
            selectedReportId: adminState.selectedReportId ?? null,
            selectedReportIdByLocation: adminState.selectedReportIdByLocation || {},
        }));
    } catch {
        // ignore storage errors
    }
}

const persistedAdminUiState = loadPersistedAdminUiState();

const adminState = {
    report: null,
    selectedLocation: persistedAdminUiState.selectedLocation || '',
    selectedReportId: persistedAdminUiState.selectedReportId ?? null,
    selectedReportIdByLocation: persistedAdminUiState.selectedReportIdByLocation || {},
    employeeFilter: '',
    discrepancyOnly: false,
    completedOnly: false,
    expandedCategories: new Set(),
    viewMode: 'categories',
    searchQuery: '',
    diagnosticsRows: [],
    diagnosticsLocation: null,
    locations: [],
    locationModalTab: 'create',
    editingLocationId: null,
    cycleTargetsPreviousCategoryIds: new Set(),
    cycleTargetsPreviousSubcategoryIds: new Set(),
};

function formatDateTime(value) {
    return value || '-';
}

function safeText(value) {
    return value ?? '-';
}

function formatMoney(value) {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${number.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
}

function formatQty(value) {
    const number = Number(value ?? 0);
    if (!Number.isFinite(number)) return '0';
    return Math.abs(number).toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 3 });
}

function formatFinishedAt(value) {
    if (!value) return '';
    return value;
}

function buildEmployeeRevisionStatus(employee, report) {
    const canManage = Boolean(report?.can_manage_employee_completion && report?.report_type !== 'final');
    if (employee?.finished_current_report) {
        return {
            label: employee.finished_at ? `Завершил: ${formatFinishedAt(employee.finished_at)}` : 'Завершил ревизию',
            className: 'green',
            actionHtml: employee.can_reopen_access && canManage && employee.user_id
                ? `<button class="chip-button chip-button-warning" type="button" onclick="reopenEmployeeRevisionAccess(${Number(employee.user_id)}, '${escapeHtml(employee.full_name)}')">Вернуть в ревизию</button>`
                : '',
        };
    }

    if (employee?.started_current_report) {
        return {
            label: employee.started_at ? `В ревизии с ${formatFinishedAt(employee.started_at)}` : 'Ревизия начата',
            className: 'orange',
            actionHtml: '',
        };
    }

    return {
        label: canManage ? 'Ревизия не начата' : 'Статус только для просмотра',
        className: canManage ? 'grey' : 'orange',
        actionHtml: '',
    };
}

async function reopenEmployeeRevisionAccess(employeeUserId, employeeName) {
    const report = adminState.report;
    if (!report?.report_id || !report?.location) {
        alert('Сначала загрузите ревизию.');
        return;
    }

    if (!confirm(`Вернуть сотрудника «${employeeName}» в текущую ревизию?`)) {
        return;
    }

    setAdminReportStatus(`Возвращаем сотрудника «${employeeName}» в ревизию...`, 'loading');

    try {
        const response = await fetch(`/api/report/${report.report_id}/reopen-employee-access`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ employee_user_id: employeeUserId }),
        });
        let payload = null;
        try {
            payload = await response.json();
        } catch {
            payload = null;
        }
        if (!response.ok) {
            throw new Error(payload?.detail || payload?.message || 'Не удалось вернуть сотрудника в ревизию.');
        }

        await loadAdminReport(report.location, report.report_id);
        setAdminReportStatus(payload?.message || `Сотрудник «${employeeName}» снова может продолжить ревизию.`, 'success');
    } catch (error) {
        console.error(error);
        setAdminReportStatus(error?.message || 'Не удалось вернуть сотрудника в ревизию.', 'error');
    }
}

function renderMoneyBreakdown(unitValue, totalValue, quantity, { highlight = false } = {}) {
    const totalHtml = totalValue != null ? formatMoney(totalValue) : '—';
    const unitHtml = unitValue != null ? `${formatMoney(unitValue)}/шт` : '—';
    const qtyHtml = quantity != null ? `× ${formatQty(quantity)}` : '';
    return `
        <div class="money-cell${highlight ? ' emphasis' : ''}">
            <strong>${totalHtml}</strong>
            <span class="muted-text">${unitHtml}${qtyHtml ? ` ${qtyHtml}` : ''}</span>
        </div>
    `;
}


function getEmployeeMoneyTotals(employee) {
    const discrepancyItems = Array.isArray(employee?.discrepancyItems) ? employee.discrepancyItems : [];
    const fallback = {
        total_cost: Number(employee?.total_cost || 0),
        total_retail: Number(employee?.total_retail || 0),
        total_lost_profit: Number(employee?.total_lost_profit || 0),
    };

    if (!discrepancyItems.length) {
        return fallback;
    }

    return discrepancyItems.reduce((totals, item) => ({
        total_cost: totals.total_cost + Number(item.cost_total || 0),
        total_retail: totals.total_retail + Number(item.retail_total || 0),
        total_lost_profit: totals.total_lost_profit + Number(item.lost_profit || 0),
    }), { total_cost: 0, total_retail: 0, total_lost_profit: 0 });
}

function buildSubcategoryFinancialSummaries(problemItems) {
    const groups = new Map();

    (problemItems || []).forEach(item => {
        const name = item.subcategory_name || 'Без подкатегории';
        if (!groups.has(name)) {
            groups.set(name, {
                subcategory_name: name,
                items_count: 0,
                total_cost: 0,
                total_retail: 0,
                total_lost_profit: 0,
            });
        }

        const bucket = groups.get(name);
        bucket.items_count += 1;
        bucket.total_cost += Number(item.cost_total || 0);
        bucket.total_retail += Number(item.retail_total || 0);
        bucket.total_lost_profit += Number(item.lost_profit || 0);
    });

    return [...groups.values()].sort((a, b) => a.subcategory_name.localeCompare(b.subcategory_name, 'ru'));
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function escapeRegExp(value) {
    return String(value ?? '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function normalizeSearch(value) {
    return String(value ?? '').trim().toLowerCase();
}

function highlightMatch(text, query) {
    const safe = escapeHtml(text);
    const normalizedQuery = normalizeSearch(query);
    if (!normalizedQuery) return safe;

    const regex = new RegExp(`(${escapeRegExp(query)})`, 'ig');
    return safe.replace(regex, '<span class="search-highlight">$1</span>');
}

function encodeUser(user) {
    return encodeURIComponent(JSON.stringify(user));
}


function isSuperadmin() {
    return Boolean(window.currentAdmin?.is_superadmin || window.currentAdmin?.role === 'superadmin');
}

function setMultiSelectValues(select, values) {
    if (!select) return;
    const wanted = new Set((values || []).map(value => String(value)));
    [...select.options].forEach(option => {
        option.selected = wanted.has(String(option.value));
    });
}

function getSelectedMultiSelectValues(select) {
    if (!select) return [];
    return [...select.selectedOptions].map(option => Number(option.value)).filter(Number.isFinite);
}

function updateUserFormByRole({ editingUser = null } = {}) {
    const roleSelect = document.getElementById('user-role');
    const locationRow = document.getElementById('user-location-row');
    const adminLocationsRow = document.getElementById('user-admin-locations-row');
    const locationSelect = document.getElementById('user-location');
    const adminLocationsSelect = document.getElementById('user-admin-locations');
    if (!roleSelect || !locationRow || !adminLocationsRow || !locationSelect || !adminLocationsSelect) return;

    const selectedRole = roleSelect.value;
    const editingCurrentAdmin = Boolean(editingUser && Number(editingUser.id) === Number(window.currentAdmin?.id));
    const currentIsSuperadmin = isSuperadmin();

    [...roleSelect.options].forEach(option => {
        if (option.value === 'superadmin') {
            option.hidden = !currentIsSuperadmin && !(editingUser && editingUser.role === 'superadmin');
        }
        if (option.value === 'admin') {
            option.hidden = !currentIsSuperadmin && !(editingUser && editingUser.role === 'admin');
        }
    });

    if (!currentIsSuperadmin && selectedRole !== 'employee') {
        roleSelect.value = editingCurrentAdmin ? 'admin' : 'employee';
    }

    const roleValue = roleSelect.value;
    const isEmployee = roleValue === 'employee';
    const isAdmin = roleValue === 'admin';

    locationRow.classList.toggle('hidden', !isEmployee);
    adminLocationsRow.classList.toggle('hidden', !isAdmin || !currentIsSuperadmin);

    roleSelect.disabled = !currentIsSuperadmin && editingCurrentAdmin;
    if (!isEmployee) {
        locationSelect.value = '';
    }
    if (!(isAdmin && currentIsSuperadmin)) {
        setMultiSelectValues(adminLocationsSelect, []);
    }
}

function showModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.classList.add('modal-open');
}

function hideModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.classList.add('hidden');
    if (document.querySelectorAll('.modal-overlay:not(.hidden)').length === 0) {
        document.body.classList.remove('modal-open');
    }
}

function getSelectedAdminLocation() {
    const locationSelect = document.getElementById('admin-location-select');
    return adminState.selectedLocation || locationSelect?.value || '';
}

function parseRuDateToIso(value) {
    const match = String(value || '').match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
    if (!match) return '';
    const [, day, month, year] = match;
    return `${year}-${month}-${day}`;
}

function toggleCycleCategoryBody(categoryId) {
    const body = document.querySelector(`[data-cycle-category-body="${CSS.escape(categoryId)}"]`);
    const button = document.querySelector(`[data-cycle-category-toggle="${CSS.escape(categoryId)}"]`);
    if (!body || !button) return;

    const isHidden = body.classList.contains('hidden');
    if (isHidden) {
        body.classList.remove('hidden');
    } else {
        body.classList.add('hidden');
    }
    button.textContent = isHidden ? '−' : '+';
    button.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}

function toggleCycleCompletedSubcategories(categoryId) {
    const body = document.querySelector(`[data-cycle-completed-body="${CSS.escape(categoryId)}"]`);
    const button = document.querySelector(`[data-cycle-completed-toggle="${CSS.escape(categoryId)}"]`);
    if (!body || !button) return;

    const isHidden = body.classList.contains('hidden');
    const count = Number(button.dataset.completedCount || 0);
    if (isHidden) {
        body.classList.remove('hidden');
    } else {
        body.classList.add('hidden');
    }
    button.textContent = isHidden ? 'Скрыть пройденные' : `Показать пройденные (${count})`;
    button.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}

window.toggleCycleCategoryBody = toggleCycleCategoryBody;
window.toggleCycleCompletedSubcategories = toggleCycleCompletedSubcategories;

function updateSearchUI() {
    const input = document.getElementById('admin-search-input');
    const hint = document.getElementById('admin-search-hint');
    if (!input || !hint) return;

    if (adminState.viewMode === 'employees') {
        input.placeholder = 'Поиск по сотруднику или его категориям...';
        hint.textContent = 'В режиме «По сотрудникам» ищет по сотрудникам, их категориям и расхождениям.';
    } else {
        input.placeholder = 'Поиск по категории, подкатегории или товару...';
        hint.textContent = 'В режиме «По категориям» ищет по категориям, подкатегориям и товарам.';
    }

    input.value = adminState.searchQuery;
}

function isDiagnosticsCategoryName(name) {
    return normalizeSearch(name) === normalizeSearch('Без категории');
}

function setAdminReportStatus(message = '', type = 'loading') {
    const container = document.getElementById('admin-report-loading');
    if (!container) return;

    if (!message) {
        container.innerHTML = '';
        return;
    }

    const spinner = type === 'loading' ? '<span class="inventory-spinner" aria-hidden="true"></span>' : '';
    container.innerHTML = `
        <div class="inventory-status ${escapeHtml(type)}">
            <div class="inventory-status-row">
                ${spinner}
                <div class="inventory-status-text">${escapeHtml(message)}</div>
            </div>
        </div>
    `;
}

function resetSummary() {
    document.getElementById('report-location').textContent = '-';
    document.getElementById('report-date').textContent = '-';
    document.getElementById('report-status').textContent = '-';
    document.getElementById('report-id').textContent = '-';
    document.getElementById('total-plus').textContent = '+0';
    document.getElementById('total-minus').textContent = '0';
    document.getElementById('report-status-chip').textContent = '—';
    document.getElementById('report-status-chip').className = 'report-status-chip';
    document.getElementById('employees-count').textContent = '0';
    document.getElementById('completed-categories').textContent = '0/0';
    document.getElementById('discrepancy-categories').textContent = '0';
    document.getElementById('no-discrepancy-categories').textContent = '0';
    document.getElementById('discrepancy-items').textContent = '0';
    document.getElementById('total-cost').textContent = '0 ₽';
    document.getElementById('total-retail').textContent = '0 ₽';
    document.getElementById('total-lost-profit').textContent = '0 ₽';
    renderSelectedCycleScope({ selected_categories: [], selected_subcategories: [] });
}

function setAdminReportLoading(message = 'Загрузка данных ревизии...') {
    setAdminReportStatus(message, 'loading');

    const loadingCardHtml = `<div class="category-card"><p class="empty-text">${escapeHtml(message)}</p></div>`;
    const employeesContainer = document.getElementById('report-employees');
    const categoriesContainer = document.getElementById('report-categories');
    const employeeDetailsContainer = document.getElementById('report-employee-details');

    resetSummary();
    if (employeesContainer) employeesContainer.innerHTML = loadingCardHtml;
    if (categoriesContainer) categoriesContainer.innerHTML = loadingCardHtml;
    if (employeeDetailsContainer) employeeDetailsContainer.innerHTML = loadingCardHtml;
}

function renderDiagnostics(rows, location) {
    const summary = document.getElementById('diagnostics-summary');
    const container = document.getElementById('diagnostics-content');
    if (!summary || !container) return;

    const noCategoryCount = rows.filter(row => normalizeSearch(row.issue_type).includes('без категории')).length;
    const noSubcategoryCount = rows.filter(row => normalizeSearch(row.issue_type).includes('без подкатегории')).length;
    summary.textContent = `Точка: ${location}. Найдено записей: ${rows.length}. Без категории: ${noCategoryCount}. Без подкатегории: ${noSubcategoryCount}.`;

    if (!rows.length) {
        container.innerHTML = '<div class="category-card"><p class="empty-text">Проблемных товаров не найдено.</p></div>';
        return;
    }

    container.innerHTML = `
        <div class="table-scroll diagnostics-table-wrap">
            <table class="admin-table diagnostics-table">
                <thead>
                    <tr>
                        <th>Тип проблемы</th>
                        <th>Товар</th>
                        <th>Остаток</th>
                        <th>Куда попал</th>
                        <th>Путь папок</th>
                        <th>Источник папки</th>
                        <th>Поиск карточки</th>
                        <th>Причина</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows.map(row => `
                        <tr>
                            <td>${escapeHtml(row.issue_type)}</td>
                            <td>
                                <strong>${escapeHtml(row.item_name)}</strong>
                                <div class="muted-text">${escapeHtml(row.item_id)}</div>
                            </td>
                            <td class="num-cell">${escapeHtml(row.expected_qty)}</td>
                            <td>
                                <div>${escapeHtml(row.category_name)}</div>
                                <div class="muted-text">${escapeHtml(row.subcategory_name)}</div>
                            </td>
                            <td>${escapeHtml(row.folder_path || '-')}</td>
                            <td>${escapeHtml(row.folder_source || '-')}</td>
                            <td>${escapeHtml(row.assortment_lookup || '-')}</td>
                            <td>${escapeHtml(row.reason || '-')}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

async function downloadDiagnosticsCsv(location, triggerButton = null) {
    const button = triggerButton || document.getElementById('diagnostics-export-btn');
    const originalText = button?.textContent || 'Скачать CSV';
    if (button) {
        button.disabled = true;
        button.textContent = 'Готовим CSV...';
    }

    try {
        const response = await fetch(`/api/inventory-diagnostics/export?location=${encodeURIComponent(location)}`);
        if (!response.ok) {
            const text = await response.text();
            throw new Error(text || 'Не удалось выгрузить диагностику.');
        }

        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const utfMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
        const filename = utfMatch ? decodeURIComponent(utfMatch[1]) : (asciiMatch?.[1] || `inventory_diagnostics_${location}.csv`);
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
    } catch (error) {
        console.error(error);
        alert('Не удалось скачать CSV с диагностикой.');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = originalText;
        }
    }
}

async function openDiagnosticsModal(location, triggerButton) {
    const summary = document.getElementById('diagnostics-summary');
    const container = document.getElementById('diagnostics-content');
    if (summary) summary.textContent = '';
    if (container) container.innerHTML = '<p>Загрузка диагностики...</p>';
    showModal('diagnostics-modal');

    const originalText = triggerButton?.textContent || 'Выгрузить ошибки разметки';
    if (triggerButton) {
        triggerButton.disabled = true;
        triggerButton.textContent = 'Проверяем разметку...';
    }

    try {
        const response = await fetch(`/api/inventory-diagnostics?location=${encodeURIComponent(location)}`);
        if (!response.ok) {
            const text = await response.text();
            throw new Error(text || 'Не удалось получить диагностику.');
        }
        const data = await response.json();
        adminState.diagnosticsRows = Array.isArray(data.rows) ? data.rows : [];
        adminState.diagnosticsLocation = location;
        renderDiagnostics(adminState.diagnosticsRows, location);
    } catch (error) {
        console.error(error);
        if (container) {
            container.innerHTML = '<div class="category-card"><p class="empty-text error-text">Не удалось загрузить диагностику разметки.</p></div>';
        }
    } finally {
        if (triggerButton) {
            triggerButton.disabled = false;
            triggerButton.textContent = originalText;
        }
    }
}



function renderLocationOptions(locations) {
    const locationSelect = document.getElementById('admin-location-select');
    const userLocation = document.getElementById('user-location');
    const adminLocations = document.getElementById('user-admin-locations');
    adminState.locations = Array.isArray(locations) ? locations : [];

    if (adminState.selectedLocation && !adminState.locations.some(location => location.name === adminState.selectedLocation)) {
        adminState.selectedLocation = adminState.locations[0]?.name || '';
        adminState.selectedReportId = adminState.selectedLocation
            ? Number(adminState.selectedReportIdByLocation?.[adminState.selectedLocation] || null)
            : null;
    }
    if (!adminState.selectedLocation && adminState.locations.length) {
        adminState.selectedLocation = adminState.locations[0].name;
        adminState.selectedReportId = Number(adminState.selectedReportIdByLocation?.[adminState.selectedLocation] || null) || null;
    }
    persistAdminUiState();

    if (locationSelect) {
        locationSelect.innerHTML = adminState.locations.length
            ? adminState.locations.map(location => `<option value="${escapeHtml(location.name)}">${escapeHtml(location.name)}</option>`).join('')
            : '<option value="">Нет доступных точек</option>';
        locationSelect.value = adminState.selectedLocation || '';
    }
    if (userLocation) {
        userLocation.innerHTML = `<option value="">— не выбрано —</option>${adminState.locations.map(location => `<option value="${escapeHtml(location.name)}">${escapeHtml(location.name)}</option>`).join('')}`;
    }
    if (adminLocations) {
        adminLocations.innerHTML = adminState.locations.map(location => `<option value="${escapeHtml(location.id)}">${escapeHtml(location.name)}</option>`).join('');
    }

    renderLocationManageList();

    if (adminState.editingLocationId && !adminState.locations.some(location => location.id === adminState.editingLocationId)) {
        adminState.editingLocationId = null;
    }
    if (!adminState.editingLocationId && adminState.locations.length) {
        selectLocationForEdit(adminState.locations[0].id);
    }
}

function getLocationById(locationId) {
    return adminState.locations.find(location => Number(location.id) === Number(locationId)) || null;
}

function setMessage(messageEl, text = '', color = '#dc3545') {
    if (!messageEl) return;
    messageEl.style.color = color;
    messageEl.textContent = text;
}

function switchLocationModalTab(tab) {
    adminState.locationModalTab = tab;
    document.querySelectorAll('[data-location-tab]').forEach(button => {
        button.classList.toggle('active', button.dataset.locationTab === tab);
    });
    document.querySelectorAll('.location-tab-panel').forEach(panel => {
        panel.classList.toggle('hidden', panel.id !== `location-tab-${tab}`);
    });

    if (tab === 'manage' && !adminState.editingLocationId && adminState.locations.length) {
        selectLocationForEdit(adminState.locations[0].id);
    }
}

function openLocationModal(tab = 'create') {
    showModal('location-modal');
    switchLocationModalTab(tab);
    renderLocationManageList();
}

function fillStoreSelect(select, stores, selectedId = '', emptyLabel = 'Выберите склад') {
    if (!select) return;
    select.innerHTML = `<option value="">${escapeHtml(emptyLabel)}</option>${(stores || []).map(store => `<option value="${escapeHtml(store.id)}" data-store-name="${escapeHtml(store.name)}" ${store.id === selectedId ? 'selected' : ''}>${escapeHtml(store.name)}</option>`).join('')}`;
}

function renderLocationManageList() {
    const container = document.getElementById('location-manage-list');
    if (!container) return;

    if (!adminState.locations.length) {
        container.innerHTML = '<div class="employee-empty-state">Точек пока нет.</div>';
        return;
    }

    container.innerHTML = adminState.locations.map(location => `
        <button type="button" class="location-manage-row ${Number(location.id) === Number(adminState.editingLocationId) ? 'active' : ''}" data-location-edit-id="${location.id}">
            <div>
                <strong>${escapeHtml(location.name)}</strong>
                <div class="muted-text">Склад: ${escapeHtml(location.ms_store_name || 'не выбран')}</div>
            </div>
            <span class="location-manage-row-arrow">→</span>
        </button>
    `).join('');
}

function selectLocationForEdit(locationId) {
    const location = getLocationById(locationId);
    const idInput = document.getElementById('location-edit-id');
    const nameInput = document.getElementById('location-edit-name');
    const tokenInput = document.getElementById('location-edit-token');
    const storeSelect = document.getElementById('location-edit-store-select');
    const message = document.getElementById('location-edit-form-message');

    if (!location || !idInput || !nameInput || !tokenInput || !storeSelect) return;

    adminState.editingLocationId = Number(location.id);
    idInput.value = String(location.id);
    nameInput.value = location.name || '';
    tokenInput.value = '';
    tokenInput.dataset.currentToken = location.ms_token || '';
    fillStoreSelect(
        storeSelect,
        location.ms_store_id ? [{ id: location.ms_store_id, name: location.ms_store_name || 'Текущий склад' }] : [],
        location.ms_store_id || '',
        location.ms_store_id ? 'Текущий склад' : 'Сначала загрузите список'
    );
    setMessage(message, '');
    renderLocationManageList();
}

async function loadStoresForSelect(token, select, message, selectedId = '') {
    if (!token) {
        setMessage(message, 'Введите токен.');
        return;
    }
    setMessage(message, '');
    if (select) select.innerHTML = '<option value="">Загрузка...</option>';

    const response = await fetch('/api/locations/stores', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ms_token: token }),
    });
    const data = await response.json();
    if (!response.ok) {
        setMessage(message, data.detail || 'Не удалось получить список складов.');
        if (select) select.innerHTML = '<option value="">Ошибка загрузки</option>';
        return;
    }

    fillStoreSelect(select, data.stores || [], selectedId);
}

async function loadLocations() {
    const response = await fetch('/api/locations');
    if (!response.ok) throw new Error('Не удалось загрузить список точек');
    const data = await response.json();
    renderLocationOptions(data.locations || []);
}

async function loadStoresByToken() {
    const token = document.getElementById('location-token').value.trim();
    const message = document.getElementById('location-form-message');
    const select = document.getElementById('location-store-select');
    await loadStoresForSelect(token, select, message);
}

async function loadEditStoresByToken() {
    const location = getLocationById(adminState.editingLocationId);
    const tokenInput = document.getElementById('location-edit-token');
    const select = document.getElementById('location-edit-store-select');
    const message = document.getElementById('location-edit-form-message');
    const token = tokenInput?.value.trim() || tokenInput?.dataset?.currentToken || location?.ms_token || '';
    await loadStoresForSelect(token, select, message, location?.ms_store_id || '');
}

async function submitLocationForm(event) {
    event.preventDefault();
    const message = document.getElementById('location-form-message');
    const select = document.getElementById('location-store-select');
    const selected = select.options[select.selectedIndex];
    const payload = {
        name: document.getElementById('location-name').value.trim(),
        ms_token: document.getElementById('location-token').value.trim(),
        ms_store_id: select.value,
        ms_store_name: selected?.dataset?.storeName || selected?.textContent || '',
    };
    const response = await fetch('/api/locations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
        setMessage(message, data.detail || data.message || 'Не удалось создать точку.');
        return;
    }
    setMessage(message, data.message || 'Точка создана.', '#1f9d55');
    document.getElementById('location-form')?.reset();
    document.getElementById('location-store-select').innerHTML = '<option value="">Сначала загрузите список</option>';
    await loadLocations();
    adminState.selectedLocation = data.location?.name || adminState.selectedLocation;
    const adminLocationSelect = document.getElementById('admin-location-select');
    if (adminLocationSelect) adminLocationSelect.value = adminState.selectedLocation;
    if (data.location?.id) {
        selectLocationForEdit(data.location.id);
        switchLocationModalTab('manage');
    }
    await reloadReportsSection(adminState.selectedLocation);
}

async function submitLocationEditForm(event) {
    event.preventDefault();
    const location = getLocationById(adminState.editingLocationId);
    const message = document.getElementById('location-edit-form-message');
    const select = document.getElementById('location-edit-store-select');
    if (!location) {
        setMessage(message, 'Сначала выберите точку слева.');
        return;
    }

    const tokenInput = document.getElementById('location-edit-token');
    const selected = select.options[select.selectedIndex];
    const resolvedToken = tokenInput?.value.trim() || tokenInput?.dataset?.currentToken || location.ms_token || '';
    const payload = {
        name: document.getElementById('location-edit-name').value.trim(),
        ms_token: resolvedToken,
        ms_store_id: select.value || location.ms_store_id || '',
        ms_store_name: selected?.dataset?.storeName || selected?.textContent || location.ms_store_name || '',
    };

    const response = await fetch(`/api/locations/${location.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
        setMessage(message, data.detail || data.message || 'Не удалось обновить точку.');
        return;
    }

    const oldName = location.name;
    setMessage(message, data.message || 'Точка обновлена.', '#1f9d55');
    await loadLocations();
    adminState.editingLocationId = data.location?.id || location.id;
    selectLocationForEdit(adminState.editingLocationId);

    if (adminState.selectedLocation === oldName) {
        adminState.selectedLocation = data.location?.name || adminState.selectedLocation;
        const adminLocationSelect = document.getElementById('admin-location-select');
        if (adminLocationSelect) adminLocationSelect.value = adminState.selectedLocation;
        await reloadReportsSection(adminState.selectedLocation);
    }
}

async function deleteSelectedLocation() {
    const location = getLocationById(adminState.editingLocationId);
    const message = document.getElementById('location-edit-form-message');
    if (!location) {
        setMessage(message, 'Сначала выберите точку слева.');
        return;
    }
    if (!confirm(`Удалить точку «${location.name}»?`)) return;

    const response = await fetch(`/api/locations/${location.id}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok) {
        setMessage(message, data.detail || data.message || 'Не удалось удалить точку.');
        return;
    }

    const deletedName = location.name;
    setMessage(message, data.message || 'Точка удалена.', '#1f9d55');
    await loadLocations();

    if (adminState.selectedLocation === deletedName) {
        adminState.selectedLocation = adminState.locations[0]?.name || '';
        const adminLocationSelect = document.getElementById('admin-location-select');
        if (adminLocationSelect) adminLocationSelect.value = adminState.selectedLocation || '';
        if (adminState.selectedLocation) {
            await reloadReportsSection(adminState.selectedLocation);
        }
    }
}

function renderUsers(users) {
    const container = document.getElementById('users-list');
    if (!users.length) {
        container.innerHTML = '<p>Пользователей пока нет.</p>';
        return;
    }

    container.innerHTML = users.map(user => {
        const locationInfo = user.role === 'admin'
            ? (Array.isArray(user.admin_locations) && user.admin_locations.length ? `точки: ${escapeHtml(user.admin_locations.join(', '))}` : 'точки не назначены')
            : escapeHtml(user.location || 'без точки');
        return `
            <div class="user-row">
                <div>
                    <strong>${escapeHtml(user.full_name)}</strong>
                    <div class="muted-text">${escapeHtml(user.username)} · ${escapeHtml(user.role)} · ${locationInfo}</div>
                    <div class="muted-text">Дата рождения: ${escapeHtml(user.birth_date)} · ${user.is_active ? 'активен' : 'выключен'}</div>
                </div>
                <div class="user-row-actions">
                    <button class="btn secondary btn-inline" data-user="${encodeUser(user)}" onclick="editUserFromEncoded(this.dataset.user)">Редактировать</button>
                    <button class="btn danger btn-inline" onclick="deleteUser(${user.id})">Удалить</button>
                </div>
            </div>
        `;
    }).join('');
}

window.editUserFromEncoded = function (encodedUser) {
    const user = JSON.parse(decodeURIComponent(encodedUser));
    document.getElementById('user-form-title').textContent = 'Редактировать пользователя';
    document.getElementById('user-id').value = user.id;
    document.getElementById('user-full-name').value = user.full_name;
    document.getElementById('user-birth-date').value = user.birth_date;
    document.getElementById('user-username').value = user.username;
    document.getElementById('user-password').value = '';
    document.getElementById('user-role').value = user.role;
    document.getElementById('user-location').value = user.location || '';
    setMultiSelectValues(document.getElementById('user-admin-locations'), user.admin_location_ids || []);
    document.getElementById('user-active').checked = Boolean(user.is_active);
    document.getElementById('user-form-message').textContent = '';
    document.getElementById('user-form-message').style.color = '#dc3545';
    updateUserFormByRole({ editingUser: user });
    showModal('users-modal');
    showModal('user-form-modal');
};

function resetUserForm() {
    document.getElementById('user-form-title').textContent = 'Создать пользователя';
    document.getElementById('user-id').value = '';
    document.getElementById('user-form').reset();
    document.getElementById('user-active').checked = true;
    document.getElementById('user-form-message').textContent = '';
    document.getElementById('user-form-message').style.color = '#dc3545';
    document.getElementById('user-location').value = '';
    setMultiSelectValues(document.getElementById('user-admin-locations'), []);
    if (!isSuperadmin()) {
        document.getElementById('user-role').value = 'employee';
    }
    updateUserFormByRole();
}

function openCreateUserModal() {
    resetUserForm();
    updateUserFormByRole();
    showModal('users-modal');
    showModal('user-form-modal');
}

async function loadUsers() {
    const response = await fetch('/api/users');
    if (!response.ok) throw new Error('Ошибка загрузки пользователей');
    const data = await response.json();
    renderUsers(data.users);
}

async function extractErrorMessage(response) {
    try {
        const data = await response.json();
        if (Array.isArray(data.detail)) {
            return data.detail.map(item => item.msg).join(', ');
        }
        if (typeof data.detail === 'string') return data.detail;
        if (typeof data.message === 'string') return data.message;
        return 'Не удалось сохранить пользователя.';
    } catch {
        return 'Не удалось сохранить пользователя.';
    }
}

async function submitUserForm(event) {
    event.preventDefault();
    const userId = document.getElementById('user-id').value;
    const message = document.getElementById('user-form-message');
    message.textContent = '';
    message.style.color = '#dc3545';

    const password = document.getElementById('user-password').value;
    const selectedRole = document.getElementById('user-role').value;
    const payload = {
        full_name: document.getElementById('user-full-name').value.trim(),
        birth_date: document.getElementById('user-birth-date').value,
        username: document.getElementById('user-username').value.trim(),
        role: selectedRole,
        location: selectedRole === 'employee' ? (document.getElementById('user-location').value || null) : null,
        admin_location_ids: selectedRole === 'admin' ? getSelectedMultiSelectValues(document.getElementById('user-admin-locations')) : [],
        is_active: document.getElementById('user-active').checked,
    };

    if (userId) {
        if (password.trim()) payload.password = password;
    } else {
        payload.password = password;
    }

    const url = userId ? `/api/users/${userId}` : '/api/users';
    const method = userId ? 'PUT' : 'POST';

    try {
        const response = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            message.textContent = await extractErrorMessage(response);
            return;
        }

        const data = await response.json();
        message.style.color = '#1f9d55';
        message.textContent = data.message || 'Пользователь сохранён.';
        await loadUsers();

        setTimeout(() => {
            hideModal('user-form-modal');
            resetUserForm();
        }, 500);
    } catch (error) {
        console.error(error);
        message.textContent = 'Ошибка сохранения пользователя.';
    }
}

window.deleteUser = async function (userId) {
    if (!confirm('Удалить сотрудника?')) return;
    const response = await fetch(`/api/users/${userId}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok) {
        alert(data.detail || data.message || 'Не удалось удалить пользователя.');
        return;
    }
    await loadUsers();
};


function updateCycleTargetDependencyState() {
    document.querySelectorAll('[data-cycle-category-id]').forEach(categoryCheckbox => {
        const categoryId = categoryCheckbox.dataset.cycleCategoryId;
        const subCheckboxes = document.querySelectorAll(`[data-cycle-subcategory-for="${CSS.escape(categoryId)}"]`);
        const categorySelected = Boolean(categoryCheckbox.checked);

        subCheckboxes.forEach(subCheckbox => {
            const wasChecked = subCheckbox.checked;
            subCheckbox.disabled = categorySelected;
            if (categorySelected && wasChecked) {
                subCheckbox.checked = false;
            }
        });
    });

    renderCycleTargetsSelectionSummary();
}

function renderCycleTargetsSelectionSummary() {
    const selectedCountNode = document.getElementById('cycle-targets-selected-count');
    const filteredCountNode = document.getElementById('cycle-targets-filtered-count');
    const categoryCheckboxes = [...document.querySelectorAll('[data-cycle-category-id]')];
    const subcategoryCheckboxes = [...document.querySelectorAll('[data-cycle-subcategory-id]')];
    const completedSubcategoryNodes = [...document.querySelectorAll('[data-cycle-completed-subcategory-id]')];

    if (selectedCountNode) {
        const selectedCategoryCount = categoryCheckboxes.filter(node => node.checked).length;
        const selectedSubcategoryCount = subcategoryCheckboxes.filter(node => node.checked && !node.disabled).length;
        selectedCountNode.textContent = `Новый выбор: категорий ${selectedCategoryCount}. Подкатегорий ${selectedSubcategoryCount}.`;
    }

    if (filteredCountNode) {
        filteredCountNode.textContent = `Показано категорий: ${categoryCheckboxes.length}. Уже пройдено подкатегорий в цикле: ${completedSubcategoryNodes.length}.`;
    }
}

function clearCycleTargetsSelection() {
    document.querySelectorAll('[data-cycle-category-id], [data-cycle-subcategory-id]').forEach(checkbox => {
        checkbox.checked = false;
        checkbox.disabled = false;
    });
    updateCycleTargetDependencyState();
}

function rememberCycleTargetsPreviousSelection(data) {
    const categories = Array.isArray(data?.categories) ? data.categories : [];
    adminState.cycleTargetsPreviousCategoryIds = new Set(
        categories.filter(category => category?.selected).map(category => String(category.id)),
    );
    adminState.cycleTargetsPreviousSubcategoryIds = new Set(
        categories.flatMap(category => (Array.isArray(category?.subcategories) ? category.subcategories : []))
            .filter(subcategory => subcategory?.selected)
            .map(subcategory => String(subcategory.id)),
    );
}

function applyPreviousCycleTargetsSelection() {
    const message = document.getElementById('cycle-targets-message');
    const categoryIds = adminState.cycleTargetsPreviousCategoryIds || new Set();
    const subcategoryIds = adminState.cycleTargetsPreviousSubcategoryIds || new Set();

    if (!categoryIds.size && !subcategoryIds.size) {
        setMessage(message, 'Прошлого выбора для этой точки пока нет.', '#6b7280');
        return;
    }

    let appliedCategories = 0;
    let appliedSubcategories = 0;

    document.querySelectorAll('[data-cycle-category-id]').forEach(checkbox => {
        const shouldCheck = categoryIds.has(String(checkbox.dataset.cycleCategoryId || ''));
        if (shouldCheck && !checkbox.checked) {
            checkbox.checked = true;
            appliedCategories += 1;
        }
    });

    document.querySelectorAll('[data-cycle-subcategory-id]').forEach(checkbox => {
        const shouldCheck = subcategoryIds.has(String(checkbox.dataset.cycleSubcategoryId || ''));
        if (!shouldCheck) return;
        const parentCategoryId = String(checkbox.dataset.cycleSubcategoryFor || '');
        const parentCheckbox = document.querySelector(`[data-cycle-category-id="${CSS.escape(parentCategoryId)}"]`);
        if (parentCheckbox?.checked) return;
        if (!checkbox.checked) {
            checkbox.checked = true;
            appliedSubcategories += 1;
        }
    });

    updateCycleTargetDependencyState();
    setMessage(
        message,
        `Добавлен прошлый выбор: категорий ${appliedCategories}, подкатегорий ${appliedSubcategories}. Можно отметить новые и сохранить.`,
        '#1f9d55',
    );
}

function renderSelectedCycleScope(report) {
    const categoriesContainer = document.getElementById('selected-cycle-categories-list');
    const subcategoriesContainer = document.getElementById('selected-cycle-subcategories-list');
    if (!categoriesContainer || !subcategoriesContainer) return;

    const selectedCategories = Array.isArray(report?.selected_categories) ? report.selected_categories : [];
    const selectedSubcategories = Array.isArray(report?.selected_subcategories) ? report.selected_subcategories : [];

    categoriesContainer.innerHTML = selectedCategories.length
        ? selectedCategories.map(name => `<span class="category-chip">${highlightMatch(name, adminState.searchQuery)}</span>`).join('')
        : '<span class="muted-text">Нет выбранных категорий</span>';

    subcategoriesContainer.innerHTML = selectedSubcategories.length
        ? selectedSubcategories.map(name => `<span class="category-chip">${highlightMatch(name, adminState.searchQuery)}</span>`).join('')
        : '<span class="muted-text">Нет выбранных подкатегорий</span>';
}

function renderCycleTargets(data) {
    const container = document.getElementById('cycle-targets-list');
    const meta = document.getElementById('cycle-targets-meta');
    const dateInput = document.getElementById('cycle-start-date');
    if (!container) return;

    const categories = Array.isArray(data?.categories) ? data.categories : [];
    rememberCycleTargetsPreviousSelection(data);
    if (meta) {
        meta.textContent = `Точка: ${data?.location || '-'} · Версия цикла: ${data?.cycle_version || '-'} · Старт: ${data?.cycle_started_at || '-'}`;
    }
    if (dateInput) {
        dateInput.value = parseRuDateToIso(data?.cycle_started_at || '');
    }

    if (!categories.length) {
        container.innerHTML = '<div class="category-card"><p class="empty-text">Нет доступных категорий для настройки цикла.</p></div>';
        renderCycleTargetsSelectionSummary();
        return;
    }

    container.innerHTML = categories.map(category => {
        const subcategories = Array.isArray(category.subcategories) ? category.subcategories : [];
        const completedSubcategories = Array.isArray(category.completed_subcategories) ? category.completed_subcategories : [];
        const hasSubcategories = subcategories.length > 0;
        const hasCompletedSubcategories = completedSubcategories.length > 0;
        const expanded = category.selected || subcategories.some(sub => sub.selected);
        return `
            <article class="category-card admin-category-card status-grey">
                <div class="admin-category-header">
                    <label class="checkbox-row" style="display:flex;align-items:flex-start;gap:10px;flex:1;cursor:pointer;">
                        <input
                            type="checkbox"
                            data-cycle-category-id="${escapeHtml(category.id)}"
                            ${category.selected ? 'checked' : ''}
                        >
                        <div>
                            <strong>${escapeHtml(category.name)}</strong>
                            <div class="muted-text">Выбрать всю категорию на цикл. Сотрудники увидят только новые подкатегории, ещё не пройденные в этом цикле.</div>
                        </div>
                    </label>
                    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end;">
                        ${hasCompletedSubcategories ? `
                            <button
                                type="button"
                                class="chip-button"
                                data-cycle-completed-toggle="${escapeHtml(category.id)}"
                                data-completed-count="${completedSubcategories.length}"
                                aria-expanded="false"
                                onclick="toggleCycleCompletedSubcategories('${escapeHtml(category.id)}')"
                            >Показать пройденные (${completedSubcategories.length})</button>
                        ` : ''}
                        ${hasSubcategories ? `
                            <button
                                type="button"
                                class="chip-button"
                                data-cycle-category-toggle="${escapeHtml(category.id)}"
                                aria-expanded="${expanded ? 'true' : 'false'}"
                                onclick="toggleCycleCategoryBody('${escapeHtml(category.id)}')"
                            >${expanded ? '−' : '+'}</button>
                        ` : ''}
                    </div>
                </div>
                ${hasSubcategories ? `
                    <div class="admin-category-body ${expanded ? '' : 'hidden'}" data-cycle-category-body="${escapeHtml(category.id)}">
                        <div style="display:grid;gap:8px;">
                            ${subcategories.map(subcategory => `
                                <label class="checkbox-row" style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;">
                                    <input
                                        type="checkbox"
                                        data-cycle-subcategory-id="${escapeHtml(subcategory.id)}"
                                        data-cycle-subcategory-for="${escapeHtml(category.id)}"
                                        ${subcategory.selected ? 'checked' : ''}
                                        ${category.selected ? 'disabled' : ''}
                                    >
                                    <div>
                                        <strong>${escapeHtml(subcategory.name)}</strong>
                                        <div class="muted-text">Выбрать отдельно только эту новую подкатегорию</div>
                                    </div>
                                </label>
                            `).join('')}
                        </div>
                    </div>
                ` : `
                    <div class="admin-category-body" data-cycle-category-body="${escapeHtml(category.id)}">
                        <div class="muted-text">Все текущие подкатегории уже пройдены в этом 15-дневном цикле.</div>
                    </div>
                `}
                ${hasCompletedSubcategories ? `
                    <div class="admin-category-body hidden" data-cycle-completed-body="${escapeHtml(category.id)}" style="margin-top:12px;border-top:1px solid rgba(148,163,184,0.25);padding-top:12px;">
                        <div class="success-text" style="margin-bottom:8px;font-weight:600;">Успешно пройденные подкатегории</div>
                        <div class="employee-category-chips">
                            ${completedSubcategories.map(subcategory => `<span class="category-chip" data-cycle-completed-subcategory-id="${escapeHtml(subcategory.id)}">${escapeHtml(subcategory.name)}</span>`).join('')}
                        </div>
                    </div>
                ` : ''}
            </article>
        `;
    }).join('');

    updateCycleTargetDependencyState();

    container.querySelectorAll('[data-cycle-category-id], [data-cycle-subcategory-id]').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            updateCycleTargetDependencyState();
        });
    });

    clearCycleTargetsSelection();
}

async function openCycleTargetsModal() {
    const location = getSelectedAdminLocation();
    const container = document.getElementById('cycle-targets-list');
    const message = document.getElementById('cycle-targets-message');
    const meta = document.getElementById('cycle-targets-meta');

    if (!location) {
        alert('Сначала выберите точку.');
        return;
    }

    if (container) container.innerHTML = '<p>Загрузка...</p>';
    if (meta) meta.textContent = '';
    setMessage(message, '');
    showModal('cycle-targets-modal');

    const response = await fetch(`/api/cycle-targets?location=${encodeURIComponent(location)}`);
    let data = null;
    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (!response.ok) {
        throw new Error(data?.detail || data?.message || 'Не удалось загрузить категории цикла.');
    }

    renderCycleTargets(data);
}

async function saveCycleTargetsSelection() {
    const location = getSelectedAdminLocation();
    const message = document.getElementById('cycle-targets-message');
    const dateInput = document.getElementById('cycle-start-date');

    if (!location) {
        setMessage(message, 'Сначала выберите точку.');
        return;
    }

    const categoryIds = [...document.querySelectorAll('[data-cycle-category-id]:checked')].map(node => node.dataset.cycleCategoryId).filter(Boolean);
    const subcategoryIds = [...document.querySelectorAll('[data-cycle-subcategory-id]:checked')].map(node => node.dataset.cycleSubcategoryId).filter(Boolean);
    const hasPreviousSelection = Boolean(adminState.cycleTargetsPreviousCategoryIds?.size || adminState.cycleTargetsPreviousSubcategoryIds?.size);

    if (!categoryIds.length && !subcategoryIds.length && hasPreviousSelection) {
        setMessage(message, 'Пустой выбор не сохраняется. Оставлен предыдущий выбор цикла.', '#6b7280');
        await openCycleTargetsModal();
        return;
    }

    setMessage(message, 'Сохраняем...', '#6b7280');

    const response = await fetch('/api/cycle-targets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            location,
            cycle_started_at: dateInput?.value || null,
            category_ids: categoryIds,
            subcategory_ids: subcategoryIds,
        }),
    });

    let data = null;
    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (!response.ok) {
        setMessage(message, data?.detail || data?.message || 'Не удалось сохранить категории цикла.');
        return;
    }

    setMessage(message, data?.message || 'Категории цикла сохранены.', '#1f9d55');
    await openCycleTargetsModal();
    await reloadReportsSection(location);
}

function getCategoryStatusLabel(status, category = null) {
    const name = typeof category === 'string' ? category : (category?.name || '');
    if (isDiagnosticsCategoryName(name)) return 'Не входит в ревизию';
    if (status === 'green') return 'Завершена';
    if (status === 'orange') return 'В работе';
    if (status === 'red') return 'Есть расхождения';
    return 'Не начата';
}

function getCategoryStatusClass(status, category = null) {
    const name = typeof category === 'string' ? category : (category?.name || '');
    if (isDiagnosticsCategoryName(name)) return 'status-chip grey';
    if (status === 'green') return 'status-chip green';
    if (status === 'orange') return 'status-chip orange';
    if (status === 'red') return 'status-chip red';
    return 'status-chip grey';
}

function setViewMode(mode) {
    adminState.viewMode = mode === 'employees' ? 'employees' : 'categories';

    const categoriesBlock = document.getElementById('report-categories');
    const employeesBlock = document.getElementById('report-employee-details');
    const buttons = document.querySelectorAll('[data-view-mode]');

    buttons.forEach(button => {
        button.classList.toggle('active', button.dataset.viewMode === adminState.viewMode);
    });

    if (categoriesBlock && employeesBlock) {
        categoriesBlock.classList.toggle('hidden', adminState.viewMode !== 'categories');
        employeesBlock.classList.toggle('hidden', adminState.viewMode !== 'employees');
    }

    updateSearchUI();

    if (adminState.report) {
        renderAllReportViews(adminState.report);
    }
}

function getFilteredCategories(report) {
    let categories = [...(report.categories || [])];

    if (adminState.employeeFilter) {
        categories = categories.filter(cat => cat.assigned_to === adminState.employeeFilter);
    }

    if (adminState.discrepancyOnly) {
        categories = categories.filter(cat => (cat.problem_items || []).length > 0);
    }

    if (adminState.completedOnly) {
        categories = categories.filter(cat => ['green', 'red'].includes((cat.status || '').toLowerCase()));
    }

    return categories;
}

function filterCategoriesBySearch(categories) {
    if (adminState.viewMode !== 'categories') return categories;

    const q = normalizeSearch(adminState.searchQuery);
    if (!q) return categories;

    return categories
        .map(category => {
            const categoryName = normalizeSearch(category.name);
            const assignedName = normalizeSearch(category.assigned_to || '');

            const categorySubcategories = Array.isArray(category.selected_subcategories) ? category.selected_subcategories : [];
            const completedSubcategories = Array.isArray(category.completed_subcategories) ? category.completed_subcategories : [];
            const inProgressSubcategories = Array.isArray(category.in_progress_subcategories) ? category.in_progress_subcategories : [];
            const subcategoryMatched = categorySubcategories.some(subName =>
                normalizeSearch(subName).includes(q)
            ) || completedSubcategories.some(sub =>
                normalizeSearch(sub.name).includes(q) || normalizeSearch(sub.checked_by || '').includes(q)
            ) || inProgressSubcategories.some(sub =>
                normalizeSearch(sub.name).includes(q) || normalizeSearch(sub.assigned_to || '').includes(q)
            );

            const matchedProblemItems = (category.problem_items || []).filter(item => {
                const itemName = normalizeSearch(item.name);
                const checkedBy = normalizeSearch(item.checked_by || '');
                const subcategoryName = normalizeSearch(item.subcategory_name || '');
                return (
                    itemName.includes(q) ||
                    checkedBy.includes(q) ||
                    subcategoryName.includes(q)
                );
            });

            const matched =
                categoryName.includes(q) ||
                assignedName.includes(q) ||
                subcategoryMatched ||
                matchedProblemItems.length > 0;

            if (!matched) return null;

            const categoryMatchedDirectly =
                categoryName.includes(q) || assignedName.includes(q) || subcategoryMatched;

            return {
                ...category,
                problem_items: categoryMatchedDirectly
                    ? (category.problem_items || [])
                    : matchedProblemItems,
            };
        })
        .filter(Boolean);
}

function buildEmployeeDetailGroups(report) {
    const employeeMap = new Map();
    const categories = getFilteredCategories(report);
    const knownEmployeeNames = new Set((report.employees || []).map(employee => employee.full_name));

    const ensureEmployeeBucket = (name, base = {}) => {
        if (!employeeMap.has(name)) {
            employeeMap.set(name, {
                user_id: base.user_id || null,
                full_name: name,
                categories: [],
                discrepancyItems: [],
                completed: base.completed || 0,
                discrepancyCount: base.discrepancyCount || 0,
                total_cost: Number(base.total_cost || 0),
                total_retail: Number(base.total_retail || 0),
                total_lost_profit: Number(base.total_lost_profit || 0),
                started_current_report: Boolean(base.started_current_report),
                started_at: base.started_at || null,
                finished_current_report: Boolean(base.finished_current_report),
                finished_at: base.finished_at || null,
                can_reopen_access: Boolean(base.can_reopen_access),
            });
        }
        return employeeMap.get(name);
    };

    (report.employees || []).forEach(employee => {
        ensureEmployeeBucket(employee.full_name, {
            user_id: employee.user_id || null,
            completed: employee.completed_categories || 0,
            discrepancyCount: employee.discrepancy_items || 0,
            total_cost: Number(employee.total_cost || 0),
            total_retail: Number(employee.total_retail || 0),
            total_lost_profit: Number(employee.total_lost_profit || 0),
            started_current_report: Boolean(employee.started_current_report),
            started_at: employee.started_at || null,
            finished_current_report: Boolean(employee.finished_current_report),
            finished_at: employee.finished_at || null,
            can_reopen_access: Boolean(employee.can_reopen_access),
        });
    });

    categories.forEach(category => {
        const categoryOwner = category.assigned_to || 'Без закрепления';
        const completedSubcategories = Array.isArray(category.completed_subcategories) ? category.completed_subcategories : [];
        const inProgressSubcategories = Array.isArray(category.in_progress_subcategories) ? category.in_progress_subcategories : [];
        const problemItems = Array.isArray(category.problem_items) ? category.problem_items : [];
        const ownerNames = new Set();

        if (category.assigned_to && knownEmployeeNames.has(category.assigned_to)) {
            ownerNames.add(category.assigned_to);
        }

        completedSubcategories.forEach(sub => {
            if (sub?.checked_by && knownEmployeeNames.has(sub.checked_by)) {
                ownerNames.add(sub.checked_by);
            }
        });

        inProgressSubcategories.forEach(sub => {
            if (sub?.assigned_to && knownEmployeeNames.has(sub.assigned_to)) {
                ownerNames.add(sub.assigned_to);
            }
        });

        problemItems.forEach(item => {
            if (item?.checked_by && knownEmployeeNames.has(item.checked_by)) {
                ownerNames.add(item.checked_by);
            }
        });

        if (!ownerNames.size) {
            ownerNames.add(categoryOwner);
        }

        ownerNames.forEach(owner => {
            const bucket = ensureEmployeeBucket(owner);
            const employeeCompletedSubcategories = completedSubcategories.filter(sub => {
                if (!sub?.checked_by) return owner === 'Без закрепления';
                return sub.checked_by === owner;
            });
            const employeeInProgressSubcategories = inProgressSubcategories.filter(sub => {
                if (!sub?.assigned_to) return owner === 'Без закрепления';
                return sub.assigned_to === owner;
            });
            const employeeProblemItems = problemItems.filter(item => {
                const responsibleEmployee = item.checked_by || categoryOwner || 'Без закрепления';
                return responsibleEmployee === owner;
            });

            const shouldIncludeCategory =
                category.assigned_to === owner ||
                employeeCompletedSubcategories.length > 0 ||
                employeeInProgressSubcategories.length > 0 ||
                employeeProblemItems.length > 0;

            if (!shouldIncludeCategory) {
                return;
            }

            bucket.categories.push({
                name: category.name,
                status: category.status,
                problemCount: employeeProblemItems.length,
                in_progress_subcategories: employeeInProgressSubcategories,
                completed_subcategories: employeeCompletedSubcategories,
            });
        });

        problemItems.forEach(item => {
            const responsibleEmployee = item.checked_by || categoryOwner || 'Без закрепления';
            const bucket = ensureEmployeeBucket(responsibleEmployee);
            bucket.discrepancyItems.push({
                category_name: category.name,
                subcategory_name: item.subcategory_name || '-',
                name: item.name,
                expected: item.expected,
                actual: item.actual,
                diff: item.diff,
                checked_by: item.checked_by || responsibleEmployee,
                cost_price: item.cost_price,
                retail_price: item.retail_price,
                cost_total: item.cost_total,
                retail_total: item.retail_total,
                lost_profit: item.lost_profit,
            });
        });
    });

    return [...employeeMap.values()]
        .filter(employee => employee.categories.length || employee.discrepancyItems.length || employee.started_current_report || employee.finished_current_report)
        .sort((a, b) => a.full_name.localeCompare(b.full_name, 'ru'));
}

function filterEmployeeGroupsBySearch(employees) {
    if (adminState.viewMode !== 'employees') return employees;

    const q = normalizeSearch(adminState.searchQuery);
    if (!q) return employees;

    return employees
        .map(employee => {
            const employeeName = normalizeSearch(employee.full_name);

            const matchedCategories = (employee.categories || []).filter(category =>
                normalizeSearch(category.name).includes(q) ||
                (category.completed_subcategories || []).some(sub =>
                    normalizeSearch(sub.name).includes(q) || normalizeSearch(sub.checked_by || '').includes(q)
                ) ||
                (category.in_progress_subcategories || []).some(sub =>
                    normalizeSearch(sub.name).includes(q) || normalizeSearch(sub.assigned_to || '').includes(q)
                )
            );

            const matchedDiscrepancies = (employee.discrepancyItems || []).filter(item => {
                return (
                    normalizeSearch(item.category_name).includes(q) ||
                    normalizeSearch(item.subcategory_name || '').includes(q) ||
                    normalizeSearch(item.name).includes(q) ||
                    normalizeSearch(item.checked_by).includes(q)
                );
            });

            const matched =
                employeeName.includes(q) ||
                matchedCategories.length > 0 ||
                matchedDiscrepancies.length > 0;

            if (!matched) return null;

            const employeeMatchedDirectly = employeeName.includes(q);

            return {
                ...employee,
                categories: employeeMatchedDirectly ? (employee.categories || []) : matchedCategories,
                discrepancyItems: employeeMatchedDirectly ? (employee.discrepancyItems || []) : matchedDiscrepancies,
            };
        })
        .filter(Boolean);
}

function getFilteredEmployeeSummaries(report) {
    let employees = [...(report.employees || [])];

    if (adminState.employeeFilter) {
        employees = employees.filter(employee => employee.full_name === adminState.employeeFilter);
    }

    if (adminState.discrepancyOnly) {
        employees = employees.filter(employee => (employee.discrepancy_items || 0) > 0);
    }

    if (adminState.viewMode === 'employees') {
        const q = normalizeSearch(adminState.searchQuery);
        if (q) {
            employees = employees.filter(employee => {
                const employeeName = normalizeSearch(employee.full_name);
                const categoriesText = normalizeSearch((employee.categories || []).join(' '));
                return employeeName.includes(q) || categoriesText.includes(q);
            });
        }
    }

    return employees;
}

function renderEmployeeDetails(report) {
    const container = document.getElementById('report-employee-details');
    if (!container) return;

    let employees = buildEmployeeDetailGroups(report);
    employees = filterEmployeeGroupsBySearch(employees);

    if (!employees.length) {
        container.innerHTML = '<div class="category-card"><p class="empty-text">По текущим фильтрам сотрудники не найдены.</p></div>';
        return;
    }

    container.innerHTML = employees.map(employee => {
        const moneyTotals = getEmployeeMoneyTotals(employee);
        const categoriesHtml = employee.categories.length
            ? `<div class="employee-detail-category-list">${employee.categories.map(category => {
                const inProgressSubcategories = Array.isArray(category.in_progress_subcategories) ? category.in_progress_subcategories : [];
                const completedSubcategories = Array.isArray(category.completed_subcategories) ? category.completed_subcategories : [];
                const inProgressSubcategoriesHtml = inProgressSubcategories.length
                    ? `
                        <div class="category-card" style="margin-top:10px;padding:12px 14px;box-shadow:none;border:1px solid rgba(148,163,184,.24);">
                            <div class="muted-text" style="margin-bottom:8px;">Взято сотрудником в этой ревизии</div>
                            <div class="employee-category-chips">${inProgressSubcategories.map(sub => `<span class="category-chip category-chip--warning">${highlightMatch(sub.name, adminState.searchQuery)}</span>`).join('')}</div>
                        </div>
                    `
                    : '';
                const completedSubcategoriesHtml = completedSubcategories.length
                    ? `
                        <div class="category-card category-card--success" style="margin-top:10px;padding:12px 14px;box-shadow:none;">
                            <div class="success-text" style="margin-bottom:8px;font-weight:600;">Успешно пройденные в этой ревизии</div>
                            <div class="employee-category-chips">${completedSubcategories.map(sub => `<span class="category-chip category-chip--success">${highlightMatch(sub.name, adminState.searchQuery)}</span>`).join('')}</div>
                        </div>
                    `
                    : '';
                const emptyStateHtml = !inProgressSubcategories.length && !completedSubcategories.length
                    ? '<div class="muted-text" style="margin-top:10px;">По этой категории у сотрудника пока нет взятых или успешно завершённых подкатегорий.</div>'
                    : '';
                return `
                <div class="employee-detail-category-row">
                    <div>
                        <strong>${highlightMatch(category.name, adminState.searchQuery)}</strong>
                        <div class="muted-text">${category.problemCount ? `Проблемных товаров: ${category.problemCount}` : 'Без расхождений'}</div>
                        ${inProgressSubcategoriesHtml}
                        ${completedSubcategoriesHtml}
                        ${emptyStateHtml}
                    </div>
                    <span class="${getCategoryStatusClass(category.status, category)}">${getCategoryStatusLabel(category.status, category)}</span>
                </div>
            `;
            }).join('')}</div>`
            : '<p class="empty-text">Категории по текущим фильтрам не найдены.</p>';

        const discrepanciesHtml = employee.discrepancyItems.length
            ? `
                <div class="table-scroll">
                    <table class="admin-table">
                        <thead>
                            <tr>
                                <th>Категория</th>
                                <th>Подкатегория</th>
                                <th>Товар</th>
                                <th>План</th>
                                <th>Факт</th>
                                <th>Разница</th>
                                <th>Себестоимость</th>
                                <th>Розница</th>
                                <th>Утерянная прибыль</th>
                                <th>Сотрудник</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${employee.discrepancyItems.map(item => {
                                const diffSign = item.diff > 0 ? '+' : '';
                                const diffClass = item.diff > 0 ? 'diff-plus' : 'diff-minus';
                                return `
                                    <tr>
                                        <td>${highlightMatch(item.category_name, adminState.searchQuery)}</td>
                                        <td>${highlightMatch(item.subcategory_name || '-', adminState.searchQuery)}</td>
                                        <td>${highlightMatch(item.name, adminState.searchQuery)}</td>
                                        <td class="num-cell">${item.expected}</td>
                                        <td class="num-cell">${item.actual}</td>
                                        <td class="num-cell ${diffClass}">${diffSign}${item.diff}</td>
                                        <td class="num-cell">${renderMoneyBreakdown(item.cost_price, item.cost_total, item.diff)}</td>
                                        <td class="num-cell">${renderMoneyBreakdown(item.retail_price, item.retail_total, item.diff)}</td>
                                        <td class="num-cell">${renderMoneyBreakdown((item.retail_price !== null && item.retail_price !== undefined && item.cost_price !== null && item.cost_price !== undefined) ? Number(item.retail_price) - Number(item.cost_price) : null, item.lost_profit, item.diff, { highlight: true })}</td>
                                        <td><span class="employee-pill">${highlightMatch(item.checked_by, adminState.searchQuery)}</span></td>
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            `
            : '<div class="category-empty-state green">У этого сотрудника нет расхождений по текущим фильтрам.</div>';

        const revisionStatus = buildEmployeeRevisionStatus(employee, report);
        return `
            <article class="category-card employee-detail-card">
                <div class="employee-detail-header">
                    <div>
                        <h3>${highlightMatch(employee.full_name, adminState.searchQuery)}</h3>
                        <p class="muted-text">Категории сотрудника и проблемные позиции в одном месте.</p>
                        ${(employee.user_id || employee.finished_current_report) ? `
                        <div class="employee-revision-row" style="margin-top:8px;">
                            <span class="status-chip ${revisionStatus.className}">${escapeHtml(revisionStatus.label)}</span>
                            ${revisionStatus.actionHtml}
                        </div>
                        ` : ''}
                    </div>
                    <div class="employee-detail-kpis">
                        <div><span class="summary-label">Категорий</span><strong>${employee.categories.length}</strong></div>
                        <div><span class="summary-label">Расхождений</span><strong>${employee.discrepancyItems.length}</strong></div>
                    </div>
                </div>
                <div class="employee-detail-economics">
                    <div class="employee-detail-economics-card">
                        <span class="summary-label">Себестоимость</span>
                        <strong>${formatMoney(moneyTotals.total_cost)}</strong>
                    </div>
                    <div class="employee-detail-economics-card">
                        <span class="summary-label">Розница</span>
                        <strong>${formatMoney(moneyTotals.total_retail)}</strong>
                    </div>
                    <div class="employee-detail-economics-card accent">
                        <span class="summary-label">Утерянная прибыль</span>
                        <strong>${formatMoney(moneyTotals.total_lost_profit)}</strong>
                    </div>
                </div>
                <div class="employee-detail-section">
                    <h4>Категории</h4>
                    ${categoriesHtml}
                </div>
                <div class="employee-detail-section">
                    <h4>Расхождения</h4>
                    ${discrepanciesHtml}
                </div>
            </article>
        `;
    }).join('');
}

function populateEmployeeFilter(report) {
    const select = document.getElementById('admin-employee-filter');
    const current = adminState.employeeFilter;
    const options = ['<option value="">Все сотрудники</option>'];

    (report.employees || []).forEach(employee => {
        options.push(`<option value="${escapeHtml(employee.full_name)}">${escapeHtml(employee.full_name)}</option>`);
    });

    select.innerHTML = options.join('');
    select.value = current;
}

function updateSummary(report) {
    document.getElementById('report-location').textContent = report.location;
    document.getElementById('report-date').textContent = formatDateTime(report.date);
    document.getElementById('report-status').textContent = `${report.status || '-'}${report.report_type === 'final' ? ' · итоговая' : ''}`;
    document.getElementById('report-id').textContent = report.report_type === 'final' ? 'Итоговая' : (report.report_number ?? report.report_id ?? '-');
    document.getElementById('total-plus').textContent = `+${report.total_plus}`;
    document.getElementById('total-minus').textContent = report.total_minus;
    document.getElementById('report-status-chip').textContent = report.status || '-';
    document.getElementById('report-status-chip').className = `report-status-chip ${((report.status || '').toLowerCase().includes('заверш')) ? 'completed' : 'progress'}`;

    const countedCategories = (report.categories || []).filter(cat => !isDiagnosticsCategoryName(cat.name));
    const totalCategories = countedCategories.length;
    const completedCategories = countedCategories.filter(cat => cat.status === 'green' || cat.status === 'red').length;
    const discrepancyCategories = countedCategories.filter(cat => (cat.problem_items || []).length > 0).length;
    const noDiscrepancyCategories = countedCategories.filter(cat => (cat.status || '').toLowerCase() === 'green').length;
    const discrepancyItems = countedCategories.reduce((sum, cat) => sum + (cat.problem_items || []).length, 0);

    document.getElementById('employees-count').textContent = String((report.employees || []).length);
    document.getElementById('completed-categories').textContent = `${completedCategories}/${totalCategories}`;
    document.getElementById('discrepancy-categories').textContent = String(discrepancyCategories);
    document.getElementById('no-discrepancy-categories').textContent = String(noDiscrepancyCategories);
    document.getElementById('discrepancy-items').textContent = String(discrepancyItems);
    document.getElementById('total-cost').textContent = formatMoney(report.total_cost);
    document.getElementById('total-retail').textContent = formatMoney(report.total_retail);
    document.getElementById('total-lost-profit').textContent = formatMoney(report.total_lost_profit);
    renderSelectedCycleScope(report);
}

function renderEmployees(report) {
    const container = document.getElementById('report-employees');
    const employees = getFilteredEmployeeSummaries(report);

    if (!employees.length) {
        container.innerHTML = '<p class="empty-text">По текущим фильтрам сотрудники не найдены.</p>';
        return;
    }

    container.innerHTML = `
        <div class="employee-summary-grid">
            ${employees.map(employee => {
                const revisionStatus = buildEmployeeRevisionStatus(employee, report);
                return `
                <article class="employee-summary-card">
                    <div class="employee-card-head">
                        <h3>${highlightMatch(employee.full_name, adminState.viewMode === 'employees' ? adminState.searchQuery : '')}</h3>
                        <div class="employee-card-actions">
                            ${revisionStatus.actionHtml}
                            <button class="chip-button" type="button" onclick="filterByEmployee('${escapeHtml(employee.full_name)}')">Показать</button>
                        </div>
                    </div>
                    <div class="employee-revision-row">
                        <span class="status-chip ${revisionStatus.className}">${escapeHtml(revisionStatus.label)}</span>
                    </div>
                    <div class="employee-meta-grid">
                        <div>
                            <span class="summary-label">Категории</span>
                            <strong>${employee.categories.length}</strong>
                        </div>
                        <div>
                            <span class="summary-label">Завершено</span>
                            <strong>${employee.completed_categories}</strong>
                        </div>
                        <div>
                            <span class="summary-label">Расхождений</span>
                            <strong>${employee.discrepancy_items}</strong>
                        </div>
                        <div>
                            <span class="summary-label">Себестоимость</span>
                            <strong>${formatMoney(employee.total_cost)}</strong>
                        </div>
                        <div>
                            <span class="summary-label">Розница</span>
                            <strong>${formatMoney(employee.total_retail)}</strong>
                        </div>
                        <div>
                            <span class="summary-label">Утерянная прибыль</span>
                            <strong>${formatMoney(employee.total_lost_profit)}</strong>
                        </div>
                    </div>
                    <div class="employee-category-chips">
                        ${employee.categories.length
                            ? employee.categories.map(category => `<span class="category-chip">${highlightMatch(category, adminState.viewMode === 'employees' ? adminState.searchQuery : '')}</span>`).join('')
                            : '<span class="muted-text">Категории ещё не закреплены</span>'}
                    </div>
                </article>
            `; }).join('')}
        </div>
    `;
}

function renderCategories(report) {
    const categoriesContainer = document.getElementById('report-categories');
    let categories = getFilteredCategories(report);
    categories = filterCategoriesBySearch(categories);

    if (!categories.length) {
        categoriesContainer.innerHTML = '<div class="category-card"><p class="empty-text">По текущим фильтрам ничего не найдено.</p></div>';
        return;
    }

    categoriesContainer.innerHTML = categories.map((cat) => {
        const key = `${report.report_id || 'none'}:${cat.name}`;
        const isDiagnostic = isDiagnosticsCategoryName(cat.name);
        const isOpen = adminState.expandedCategories.has(key);
        const problemItems = cat.problem_items || [];
        const selectedSubcategories = Array.isArray(cat.selected_subcategories) ? cat.selected_subcategories : [];
        const inProgressSubcategories = Array.isArray(cat.in_progress_subcategories) ? cat.in_progress_subcategories : [];
        const remainingSubcategories = Array.isArray(cat.remaining_subcategories) ? cat.remaining_subcategories : [];
        const completedSubcategories = Array.isArray(cat.completed_subcategories) ? cat.completed_subcategories : [];
        const selectionText = cat.selected_on_cycle
            ? 'На цикл выбрана вся категория'
            : (selectedSubcategories.length ? `На цикл выбрано подкатегорий: ${selectedSubcategories.length}` : '');
        const summaryText = isDiagnostic
            ? 'Служебная ветка. Не входит в общую ревизию.'
            : (problemItems.length
                ? `Проблемных товаров: ${problemItems.length}`
                : (cat.status === 'green' ? 'Без расхождений' : getCategoryStatusLabel(cat.status, cat)));
        const subcategoryFinancialSummaries = buildSubcategoryFinancialSummaries(problemItems);
        const statusClass = getCategoryStatusClass(cat.status, cat);
        const statusLabel = getCategoryStatusLabel(cat.status, cat);
        const articleStatusClass = isDiagnostic ? 'status-grey' : `status-${cat.status}`;

        return `
            <article class="category-card admin-category-card ${articleStatusClass}">
                <button class="admin-category-header" type="button" onclick="toggleAdminCategory('${escapeHtml(key)}')">
                    <div>
                        <h3>${highlightMatch(cat.name, adminState.searchQuery)}</h3>
                        <div class="admin-category-subline">
                            <span class="assigned-badge">${cat.assigned_to ? `Закреплена за: ${highlightMatch(cat.assigned_to, adminState.searchQuery)}` : 'Категория пока не закреплена'}</span>
                            <span class="muted-text">${selectionText ? `${selectionText} · ${summaryText}` : summaryText}</span>
                        </div>
                    </div>
                    <div class="admin-category-header-right">
                        <span class="${statusClass}">${statusLabel}</span>
                        <span class="collapse-icon">${isOpen ? '−' : '+'}</span>
                    </div>
                </button>
                <div class="admin-category-body ${isOpen ? '' : 'hidden'}">
                    ${(!isDiagnostic && (cat.selected_on_cycle || selectedSubcategories.length || inProgressSubcategories.length || completedSubcategories.length || remainingSubcategories.length)) ? `
                        <div class="category-card" style="margin-bottom:12px;padding:14px 16px;box-shadow:none;border:1px solid rgba(148,163,184,.24);">
                            <div class="muted-text" style="margin-bottom:8px;">Взято сотрудниками в этой ревизии</div>
                            ${inProgressSubcategories.length
                                ? `<div class="employee-category-chips">${inProgressSubcategories.map(sub => `<span class="category-chip category-chip--warning">${highlightMatch(sub.name, adminState.searchQuery)}${sub.assigned_to ? ` · ${highlightMatch(sub.assigned_to, adminState.searchQuery)}` : ''}</span>`).join('')}</div>`
                                : '<div class="muted-text">В этой ревизии по этой категории ещё нет незавершённых взятых подкатегорий.</div>'}
                        </div>
                    ` : ''}
                    ${completedSubcategories.length ? `
                        <div class="category-card category-card--success" style="margin-bottom:12px;padding:14px 16px;box-shadow:none;">
                            <div class="success-text" style="margin-bottom:8px;font-weight:600;">Успешно пройденные подкатегории в этой ревизии</div>
                            <div class="employee-category-chips">
                                ${completedSubcategories.map(sub => `<span class="category-chip category-chip--success">${highlightMatch(sub.name, adminState.searchQuery)}${sub.checked_by ? ` · ${highlightMatch(sub.checked_by, adminState.searchQuery)}` : ''}</span>`).join('')}
                            </div>
                        </div>
                    ` : ''}
                    ${problemItems.length ? `
                        ${isDiagnostic ? '' : '<div class="discrepancy-banner">⚠️ Зафиксированы расхождения</div>'}
                        ${subcategoryFinancialSummaries.length ? `
                            <div class="admin-category-economics">
                                ${subcategoryFinancialSummaries.map(summary => `
                                    <div class="admin-category-economics-card">
                                        <span class="summary-label">${highlightMatch(summary.subcategory_name, adminState.searchQuery)} · товаров: ${summary.items_count}</span>
                                        <strong>Себестоимость: ${formatMoney(summary.total_cost)}</strong>
                                        <div class="muted-text">Розница: ${formatMoney(summary.total_retail)}</div>
                                        <div class="muted-text">Утерянная прибыль: ${formatMoney(summary.total_lost_profit)}</div>
                                    </div>
                                `).join('')}
                            </div>
                        ` : ''}
                        <div class="table-scroll">
                            <table class="admin-table">
                                <thead>
                                    <tr>
                                        <th>Подкатегория</th>
                                        <th>Товар</th>
                                        <th>План</th>
                                        <th>Факт</th>
                                        <th>Разница</th>
                                        <th>Себестоимость</th>
                                        <th>Розница</th>
                                        <th>Утерянная прибыль</th>
                                        <th>Сотрудник</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    ${problemItems.map(item => {
                                        const diffSign = item.diff > 0 ? '+' : '';
                                        const diffClass = item.diff > 0 ? 'diff-plus' : 'diff-minus';
                                        return `
                                            <tr>
                                                <td>${highlightMatch(item.subcategory_name || '-', adminState.searchQuery)}</td>
                                                <td>${highlightMatch(item.name, adminState.searchQuery)}</td>
                                                <td class="num-cell">${item.expected}</td>
                                                <td class="num-cell">${item.actual}</td>
                                                <td class="num-cell ${diffClass}">${diffSign}${item.diff}</td>
                                                <td class="num-cell">${renderMoneyBreakdown(item.cost_price, item.cost_total, item.diff)}</td>
                                                <td class="num-cell">${renderMoneyBreakdown(item.retail_price, item.retail_total, item.diff)}</td>
                                                <td class="num-cell">${renderMoneyBreakdown((item.retail_price !== null && item.retail_price !== undefined && item.cost_price !== null && item.cost_price !== undefined) ? Number(item.retail_price) - Number(item.cost_price) : null, item.lost_profit, item.diff, { highlight: true })}</td>
                                                <td><span class="employee-pill">${highlightMatch(item.checked_by || '-', adminState.searchQuery)}</span></td>
                                            </tr>
                                        `;
                                    }).join('')}
                                </tbody>
                            </table>
                        </div>
                    ` : `
                        <div class="category-empty-state ${isDiagnostic ? 'grey' : cat.status}">
                            ${isDiagnostic
                                ? 'Эта служебная категория не включается в общую ревизию.'
                                : cat.status === 'green'
                                    ? 'Категория завершена без расхождений.'
                                    : cat.status === 'orange'
                                        ? 'Категория закреплена и находится в работе.'
                                        : 'По категории пока нет зафиксированных данных.'}
                        </div>
                    `}
                </div>
            </article>
        `;
    }).join('');
}

function renderAllReportViews(report) {
    renderEmployees(report);
    renderCategories(report);
    renderEmployeeDetails(report);
}

window.toggleAdminCategory = function (key) {
    if (adminState.expandedCategories.has(key)) {
        adminState.expandedCategories.delete(key);
    } else {
        adminState.expandedCategories.add(key);
    }
    if (adminState.report) renderCategories(adminState.report);
};

window.filterByEmployee = function (fullName) {
    adminState.employeeFilter = fullName;
    document.getElementById('admin-employee-filter').value = fullName;
    setViewMode('employees');
};

async function loadReportsList(location) {
    const select = document.getElementById('admin-report-select');
    select.disabled = true;
    select.innerHTML = '<option>Загрузка...</option>';
    setAdminReportLoading(`Загружаем список ревизий для точки «${location}»...`);

    const response = await fetch(`/api/reports?location=${encodeURIComponent(location)}`);
    if (!response.ok) throw new Error('Ошибка загрузки списка ревизий');
    const data = await response.json();

    if (!data.reports.length) {
        select.innerHTML = '<option value="">Нет сохранённых ревизий</option>';
        select.disabled = true;
        adminState.selectedReportId = null;
        if (location) {
            delete adminState.selectedReportIdByLocation[location];
        }
        persistAdminUiState();
        return null;
    }

    select.innerHTML = data.reports.map(report => `
        <option value="${report.report_id}" data-report-number="${report.report_number ?? ''}">${escapeHtml(report.label)}</option>
    `).join('');

    const persistedReportId = Number(adminState.selectedReportIdByLocation?.[location] || adminState.selectedReportId || null);
    if (persistedReportId && data.reports.some(report => Number(report.report_id) === persistedReportId)) {
        select.value = String(persistedReportId);
    }

    select.disabled = false;
    return Number(select.value);
}

async function loadAdminReport(location, reportId) {
    const categoriesContainer = document.getElementById('report-categories');
    const employeesContainer = document.getElementById('report-employees');
    const employeeDetailsContainer = document.getElementById('report-employee-details');

    const reportSelect = document.getElementById('admin-report-select');
    const selectedOption = reportSelect?.selectedOptions?.[0] || null;
    const selectedReportNumber = selectedOption && Number(selectedOption.value) === Number(reportId)
        ? (selectedOption.dataset.reportNumber || '')
        : '';

    setAdminReportLoading(reportId
        ? `Загружаем ревизию №${selectedReportNumber || reportId} для точки «${location}»...`
        : `Загружаем последнюю ревизию для точки «${location}»...`);

    try {
        const params = new URLSearchParams({ location });
        if (reportId) params.set('report_id', String(reportId));

        const response = await fetch(`/api/report?${params.toString()}`);
        let payload = null;
        try {
            payload = await response.json();
        } catch {
            payload = null;
        }
        if (!response.ok) {
            throw new Error(payload?.detail || payload?.message || 'Ошибка загрузки отчёта');
        }
        const report = payload;
        adminState.report = report;
        adminState.selectedReportId = report.report_id || null;
        if (adminState.selectedLocation) {
            adminState.selectedReportIdByLocation[adminState.selectedLocation] = adminState.selectedReportId;
        }
        persistAdminUiState();

        updateSummary(report);
        populateEmployeeFilter(report);
        renderAllReportViews(report);
        setAdminReportStatus('');
    } catch (error) {
        console.error(error);
        setAdminReportStatus(error?.message || 'Не удалось загрузить данные ревизии.', 'error');
        employeesContainer.innerHTML = '<p class="empty-text error-text">Ошибка загрузки данных о сотрудниках.</p>';
        if (employeeDetailsContainer) {
            employeeDetailsContainer.innerHTML = '<div class="category-card"><p class="empty-text error-text">Ошибка загрузки детализации по сотрудникам.</p></div>';
        }
        categoriesContainer.innerHTML = '<div class="category-card"><p class="empty-text error-text">Ошибка загрузки данных ревизии.</p></div>';
    }
}

async function deleteSelectedReport() {
    const locationSelect = document.getElementById('admin-location-select');
    const reportSelect = document.getElementById('admin-report-select');
    const reportId = reportSelect.value;
    if (!reportId) return;

    if (!confirm('Удалить выбранную ревизию?')) return;
    const response = await fetch(`/api/report/${reportId}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok) {
        alert(data.detail || data.message || 'Не удалось удалить ревизию.');
        return;
    }
    await reloadReportsSection(locationSelect.value);
}

async function reloadReportsSection(location) {
    adminState.selectedLocation = location;
    adminState.selectedReportId = Number(adminState.selectedReportIdByLocation?.[location] || adminState.selectedReportId || null) || null;
    persistAdminUiState();
    adminState.employeeFilter = '';
    adminState.expandedCategories.clear();

    const employeeFilterSelect = document.getElementById('admin-employee-filter');
    if (employeeFilterSelect) {
        employeeFilterSelect.value = '';
    }

    const reportSelect = document.getElementById('admin-report-select');
    if (!location) {
        if (reportSelect) {
            reportSelect.innerHTML = '<option value="">Нет точек</option>';
            reportSelect.disabled = true;
            reportSelect.onchange = null;
        }
        return;
    }

    const reportId = await loadReportsList(location);
    await loadAdminReport(location, reportId);

    reportSelect.onchange = async () => {
        const selected = reportSelect.value ? Number(reportSelect.value) : null;
        adminState.selectedReportId = selected;
        if (location) {
            if (selected) {
                adminState.selectedReportIdByLocation[location] = selected;
            } else {
                delete adminState.selectedReportIdByLocation[location];
            }
        }
        persistAdminUiState();
        adminState.expandedCategories.clear();
        await loadAdminReport(location, selected);
    };
}

async function deleteSelectedEmployeeReport() {
    // заглушка, если вдруг понадобится отдельная логика потом
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    location.href = '/login';
}

function initModalCloseBehavior() {
    document.querySelectorAll('.modal-overlay').forEach((overlay) => {
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) {
                overlay.classList.add('hidden');
                if (document.querySelectorAll('.modal-overlay:not(.hidden)').length === 0) {
                    document.body.classList.remove('modal-open');
                }
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    const locationSelect = document.getElementById('admin-location-select');
    const employeeFilter = document.getElementById('admin-employee-filter');
    const discrepancyOnly = document.getElementById('admin-discrepancy-only');
    const completedOnly = document.getElementById('admin-completed-only');
    const searchInput = document.getElementById('admin-search-input');
    const userRoleSelect = document.getElementById('user-role');

    document.getElementById('logout-btn').addEventListener('click', logout);
    document.getElementById('open-users-btn').addEventListener('click', async () => {
        showModal('users-modal');
        await loadUsers();
    });
    document.getElementById('open-create-location-btn')?.addEventListener('click', () => openLocationModal('create'));
    document.getElementById('open-cycle-targets-btn')?.addEventListener('click', async () => {
        try {
            await openCycleTargetsModal();
        } catch (error) {
            console.error(error);
            alert('Не удалось загрузить категории цикла.');
        }
    });
    document.getElementById('close-location-modal-btn')?.addEventListener('click', () => hideModal('location-modal'));
    document.getElementById('load-stores-btn')?.addEventListener('click', loadStoresByToken);
    document.getElementById('load-edit-stores-btn')?.addEventListener('click', loadEditStoresByToken);
    document.getElementById('location-form')?.addEventListener('submit', submitLocationForm);
    document.getElementById('location-edit-form')?.addEventListener('submit', submitLocationEditForm);
    document.getElementById('delete-location-btn')?.addEventListener('click', deleteSelectedLocation);
    document.querySelectorAll('[data-location-tab]').forEach(button => {
        button.addEventListener('click', () => switchLocationModalTab(button.dataset.locationTab || 'create'));
    });
    document.getElementById('location-manage-list')?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-location-edit-id]');
        if (!button) return;
        selectLocationForEdit(Number(button.dataset.locationEditId));
    });
    document.getElementById('close-cycle-targets-modal-btn')?.addEventListener('click', () => hideModal('cycle-targets-modal'));
    document.getElementById('save-cycle-targets-btn')?.addEventListener('click', saveCycleTargetsSelection);
    document.getElementById('apply-previous-cycle-targets-btn')?.addEventListener('click', applyPreviousCycleTargetsSelection);
    document.getElementById('close-users-modal-btn').addEventListener('click', () => hideModal('users-modal'));
    document.getElementById('open-create-user-btn').textContent = isSuperadmin() ? 'Добавить пользователя' : 'Добавить сотрудника';
    document.getElementById('open-create-user-btn').addEventListener('click', openCreateUserModal);
    document.getElementById('close-user-form-modal-btn').addEventListener('click', () => {
        hideModal('user-form-modal');
        resetUserForm();
    });
    document.getElementById('user-form').addEventListener('submit', submitUserForm);
    userRoleSelect?.addEventListener('change', () => updateUserFormByRole());
    document.getElementById('user-form-reset').addEventListener('click', resetUserForm);
    document.getElementById('delete-report-btn').addEventListener('click', deleteSelectedReport);
    document.getElementById('download-diagnostics-btn')?.addEventListener('click', async () => {
        const location = locationSelect.value;
        if (!location) return;
        await openDiagnosticsModal(location, document.getElementById('download-diagnostics-btn'));
    });
    document.getElementById('close-diagnostics-modal-btn')?.addEventListener('click', () => hideModal('diagnostics-modal'));
    document.getElementById('diagnostics-export-btn')?.addEventListener('click', async () => {
        const location = adminState.diagnosticsLocation || locationSelect.value;
        if (!location) return;
        await downloadDiagnosticsCsv(location, document.getElementById('diagnostics-export-btn'));
    });

    document.querySelectorAll('[data-view-mode]').forEach(button => {
        button.addEventListener('click', () => setViewMode(button.dataset.viewMode));
    });

    document.addEventListener('click', async (event) => {
        const actionButton = event.target.closest('[data-open-modal-action]');
        if (!actionButton) return;

        if (actionButton.dataset.openModalAction === 'location') {
            openLocationModal('create');
            return;
        }

        if (actionButton.dataset.openModalAction === 'cycle-targets') {
            try {
                await openCycleTargetsModal();
            } catch (error) {
                console.error(error);
                alert('Не удалось загрузить категории цикла.');
            }
        }
    });

    locationSelect.addEventListener('change', async () => {
        const nextLocation = locationSelect.value;
        adminState.selectedLocation = nextLocation;
        adminState.selectedReportId = Number(adminState.selectedReportIdByLocation?.[nextLocation] || null) || null;
        persistAdminUiState();
        await reloadReportsSection(nextLocation);
    });

    employeeFilter.addEventListener('change', () => {
        adminState.employeeFilter = employeeFilter.value;
        if (adminState.report) {
            renderAllReportViews(adminState.report);
        }
    });

    discrepancyOnly.addEventListener('change', () => {
        adminState.discrepancyOnly = discrepancyOnly.checked;
        if (adminState.report) {
            renderAllReportViews(adminState.report);
        }
    });

    completedOnly?.addEventListener('change', () => {
        adminState.completedOnly = completedOnly.checked;
        if (adminState.report) {
            renderAllReportViews(adminState.report);
        }
    });

    searchInput?.addEventListener('input', () => {
        adminState.searchQuery = searchInput.value.trim();
        if (adminState.report) {
            renderAllReportViews(adminState.report);
        }
    });

    initModalCloseBehavior();
    setViewMode('categories');
    updateUserFormByRole();
    await loadLocations();
    await reloadReportsSection(adminState.selectedLocation || document.getElementById('admin-location-select').value);
});