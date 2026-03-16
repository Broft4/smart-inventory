const adminState = {
    report: null,
    selectedLocation: '',
    selectedReportId: null,
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
};

function formatDateTime(value) {
    return value || '-';
}

function safeText(value) {
    return value ?? '-';
}

function formatMoney(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '—';
    return `${number.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;
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

    const isHidden = body.style.display === 'none';
    body.style.display = isHidden ? 'grid' : 'none';
    button.textContent = isHidden ? '−' : '+';
    button.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}

window.toggleCycleCategoryBody = toggleCycleCategoryBody;

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
    document.getElementById('discrepancy-items').textContent = '0';
    document.getElementById('total-cost').textContent = '0 ₽';
    document.getElementById('total-retail').textContent = '0 ₽';
    document.getElementById('total-lost-profit').textContent = '0 ₽';
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
    adminState.locations = Array.isArray(locations) ? locations : [];

    if (adminState.selectedLocation && !adminState.locations.some(location => location.name === adminState.selectedLocation)) {
        adminState.selectedLocation = adminState.locations[0]?.name || '';
    }
    if (!adminState.selectedLocation && adminState.locations.length) {
        adminState.selectedLocation = adminState.locations[0].name;
    }

    if (locationSelect) {
        locationSelect.innerHTML = adminState.locations.map(location => `<option value="${escapeHtml(location.name)}">${escapeHtml(location.name)}</option>`).join('');
        locationSelect.value = adminState.selectedLocation || '';
    }
    if (userLocation) {
        userLocation.innerHTML = `<option value="">— не выбрано —</option>${adminState.locations.map(location => `<option value="${escapeHtml(location.name)}">${escapeHtml(location.name)}</option>`).join('')}`;
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

    container.innerHTML = users.map(user => `
        <div class="user-row">
            <div>
                <strong>${escapeHtml(user.full_name)}</strong>
                <div class="muted-text">${escapeHtml(user.username)} · ${escapeHtml(user.role)} · ${escapeHtml(user.location || 'без точки')}</div>
                <div class="muted-text">Дата рождения: ${escapeHtml(user.birth_date)} · ${user.is_active ? 'активен' : 'выключен'}</div>
            </div>
            <div class="user-row-actions">
                <button class="btn secondary btn-inline" data-user="${encodeUser(user)}" onclick="editUserFromEncoded(this.dataset.user)">Редактировать</button>
                <button class="btn danger btn-inline" onclick="deleteUser(${user.id})">Удалить</button>
            </div>
        </div>
    `).join('');
}

window.editUserFromEncoded = function (encodedUser) {
    const user = JSON.parse(decodeURIComponent(encodedUser));
    document.getElementById('user-form-title').textContent = 'Редактировать сотрудника';
    document.getElementById('user-id').value = user.id;
    document.getElementById('user-full-name').value = user.full_name;
    document.getElementById('user-birth-date').value = user.birth_date;
    document.getElementById('user-username').value = user.username;
    document.getElementById('user-password').value = '';
    document.getElementById('user-role').value = user.role;
    document.getElementById('user-location').value = user.location || '';
    document.getElementById('user-active').checked = Boolean(user.is_active);
    document.getElementById('user-form-message').textContent = '';
    document.getElementById('user-form-message').style.color = '#dc3545';
    showModal('users-modal');
    showModal('user-form-modal');
};

function resetUserForm() {
    document.getElementById('user-form-title').textContent = 'Создать сотрудника';
    document.getElementById('user-id').value = '';
    document.getElementById('user-form').reset();
    document.getElementById('user-active').checked = true;
    document.getElementById('user-form-message').textContent = '';
    document.getElementById('user-form-message').style.color = '#dc3545';
    document.getElementById('user-location').value = '';
}

function openCreateUserModal() {
    resetUserForm();
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
    const payload = {
        full_name: document.getElementById('user-full-name').value.trim(),
        birth_date: document.getElementById('user-birth-date').value,
        username: document.getElementById('user-username').value.trim(),
        role: document.getElementById('user-role').value,
        location: document.getElementById('user-location').value || null,
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
}

function renderCycleTargets(data) {
    const container = document.getElementById('cycle-targets-list');
    const meta = document.getElementById('cycle-targets-meta');
    const dateInput = document.getElementById('cycle-start-date');
    if (!container) return;

    const categories = Array.isArray(data?.categories) ? data.categories : [];
    if (meta) {
        meta.textContent = `Точка: ${data?.location || '-'} · Версия цикла: ${data?.cycle_version || '-'} · Старт: ${data?.cycle_started_at || '-'}`;
    }
    if (dateInput) {
        dateInput.value = parseRuDateToIso(data?.cycle_started_at || '');
    }

    if (!categories.length) {
        container.innerHTML = '<div class="category-card"><p class="empty-text">Нет доступных категорий для настройки цикла.</p></div>';
        return;
    }

    container.innerHTML = categories.map(category => {
        const subcategories = Array.isArray(category.subcategories) ? category.subcategories : [];
        const hasSubcategories = subcategories.length > 0;
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
                            <div class="muted-text">Выбрать всю категорию на цикл</div>
                        </div>
                    </label>
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
                                        <div class="muted-text">Выбрать отдельно только эту подкатегорию</div>
                                    </div>
                                </label>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}
            </article>
        `;
    }).join('');

    updateCycleTargetDependencyState();

    container.querySelectorAll('[data-cycle-category-id]').forEach(checkbox => {
        checkbox.addEventListener('change', () => {
            updateCycleTargetDependencyState();
        });
    });
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

            const categorySubcategories = Array.isArray(category.subcategories) ? category.subcategories : [];
            const subcategoryMatched = categorySubcategories.some(sub =>
                normalizeSearch(sub.name).includes(q)
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

    (report.employees || []).forEach(employee => {
        employeeMap.set(employee.full_name, {
            full_name: employee.full_name,
            categories: [],
            discrepancyItems: [],
            completed: employee.completed_categories || 0,
            discrepancyCount: employee.discrepancy_items || 0,
        });
    });

    categories.forEach(category => {
        const owner = category.assigned_to || 'Без закрепления';
        if (!employeeMap.has(owner)) {
            employeeMap.set(owner, {
                full_name: owner,
                categories: [],
                discrepancyItems: [],
                completed: 0,
                discrepancyCount: 0,
            });
        }

        const bucket = employeeMap.get(owner);
        bucket.categories.push({
            name: category.name,
            status: category.status,
            problemCount: (category.problem_items || []).length,
        });

        (category.problem_items || []).forEach(item => {
            bucket.discrepancyItems.push({
                category_name: category.name,
                name: item.name,
                expected: item.expected,
                actual: item.actual,
                diff: item.diff,
                checked_by: item.checked_by || owner,
                cost_total: item.cost_total,
                retail_total: item.retail_total,
                lost_profit: item.lost_profit,
            });
        });
    });

    return [...employeeMap.values()]
        .filter(employee => employee.categories.length || employee.discrepancyItems.length)
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
                normalizeSearch(category.name).includes(q)
            );

            const matchedDiscrepancies = (employee.discrepancyItems || []).filter(item => {
                return (
                    normalizeSearch(item.category_name).includes(q) ||
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
        const categoriesHtml = employee.categories.length
            ? `<div class="employee-detail-category-list">${employee.categories.map(category => `
                <div class="employee-detail-category-row">
                    <div>
                        <strong>${highlightMatch(category.name, adminState.searchQuery)}</strong>
                        <div class="muted-text">${category.problemCount ? `Проблемных товаров: ${category.problemCount}` : 'Без расхождений'}</div>
                    </div>
                    <span class="${getCategoryStatusClass(category.status, category)}">${getCategoryStatusLabel(category.status, category)}</span>
                </div>
            `).join('')}</div>`
            : '<p class="empty-text">Категории по текущим фильтрам не найдены.</p>';

        const discrepanciesHtml = employee.discrepancyItems.length
            ? `
                <div class="table-scroll">
                    <table class="admin-table">
                        <thead>
                            <tr>
                                <th>Категория</th>
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
                                        <td>${highlightMatch(item.name, adminState.searchQuery)}</td>
                                        <td class="num-cell">${item.expected}</td>
                                        <td class="num-cell">${item.actual}</td>
                                        <td class="num-cell ${diffClass}">${diffSign}${item.diff}</td>
                                        <td class="num-cell">${formatMoney(item.cost_total)}</td>
                                        <td class="num-cell">${formatMoney(item.retail_total)}</td>
                                        <td class="num-cell">${formatMoney(item.lost_profit)}</td>
                                        <td><span class="employee-pill">${highlightMatch(item.checked_by, adminState.searchQuery)}</span></td>
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            `
            : '<div class="category-empty-state green">У этого сотрудника нет расхождений по текущим фильтрам.</div>';

        return `
            <article class="category-card employee-detail-card">
                <div class="employee-detail-header">
                    <div>
                        <h3>${highlightMatch(employee.full_name, adminState.searchQuery)}</h3>
                        <p class="muted-text">Категории сотрудника и проблемные позиции в одном месте.</p>
                    </div>
                    <div class="employee-detail-kpis">
                        <div><span class="summary-label">Категорий</span><strong>${employee.categories.length}</strong></div>
                        <div><span class="summary-label">Расхождений</span><strong>${employee.discrepancyItems.length}</strong></div>
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
    document.getElementById('report-id').textContent = report.report_number ?? report.report_id ?? '-';
    document.getElementById('total-plus').textContent = `+${report.total_plus}`;
    document.getElementById('total-minus').textContent = report.total_minus;
    document.getElementById('report-status-chip').textContent = report.status || '-';
    document.getElementById('report-status-chip').className = `report-status-chip ${((report.status || '').toLowerCase().includes('заверш')) ? 'completed' : 'progress'}`;

    const countedCategories = (report.categories || []).filter(cat => !isDiagnosticsCategoryName(cat.name));
    const totalCategories = countedCategories.length;
    const completedCategories = countedCategories.filter(cat => cat.status === 'green' || cat.status === 'red').length;
    const discrepancyCategories = countedCategories.filter(cat => (cat.problem_items || []).length > 0).length;
    const discrepancyItems = countedCategories.reduce((sum, cat) => sum + (cat.problem_items || []).length, 0);

    document.getElementById('employees-count').textContent = String((report.employees || []).length);
    document.getElementById('completed-categories').textContent = `${completedCategories}/${totalCategories}`;
    document.getElementById('discrepancy-categories').textContent = String(discrepancyCategories);
    document.getElementById('discrepancy-items').textContent = String(discrepancyItems);
    document.getElementById('total-cost').textContent = formatMoney(report.total_cost || 0);
    document.getElementById('total-retail').textContent = formatMoney(report.total_retail || 0);
    document.getElementById('total-lost-profit').textContent = formatMoney(report.total_lost_profit || 0);
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
            ${employees.map(employee => `
                <article class="employee-summary-card">
                    <div class="employee-card-head">
                        <h3>${highlightMatch(employee.full_name, adminState.viewMode === 'employees' ? adminState.searchQuery : '')}</h3>
                        <button class="chip-button" type="button" onclick="filterByEmployee('${escapeHtml(employee.full_name)}')">Показать</button>
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
                    </div>
                    <div class="employee-category-chips">
                        ${employee.categories.length
                            ? employee.categories.map(category => `<span class="category-chip">${highlightMatch(category, adminState.viewMode === 'employees' ? adminState.searchQuery : '')}</span>`).join('')
                            : '<span class="muted-text">Категории ещё не закреплены</span>'}
                    </div>
                </article>
            `).join('')}
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

    categoriesContainer.innerHTML = categories.map((cat, index) => {
        const key = `${report.report_id || 'none'}:${cat.name}`;
        const isDiagnostic = isDiagnosticsCategoryName(cat.name);
        const isOpen = adminState.expandedCategories.has(key) || (!isDiagnostic && index === 0);
        const problemItems = cat.problem_items || [];
        const summaryText = isDiagnostic
            ? 'Служебная ветка. Не входит в общую ревизию.'
            : (problemItems.length
                ? `Проблемных товаров: ${problemItems.length}`
                : (cat.status === 'green' ? 'Без расхождений' : getCategoryStatusLabel(cat.status, cat)));
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
                            <span class="muted-text">${summaryText}</span>
                        </div>
                    </div>
                    <div class="admin-category-header-right">
                        <span class="${statusClass}">${statusLabel}</span>
                        <span class="collapse-icon">${isOpen ? '−' : '+'}</span>
                    </div>
                </button>
                <div class="admin-category-body ${isOpen ? '' : 'hidden'}">
                    ${problemItems.length ? `
                        ${isDiagnostic ? '' : '<div class="discrepancy-banner">⚠️ Зафиксированы расхождения</div>'}
                        <div class="table-scroll">
                            <table class="admin-table">
                                <thead>
                                    <tr>
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
                                                <td>${highlightMatch(item.name, adminState.searchQuery)}</td>
                                                <td class="num-cell">${item.expected}</td>
                                                <td class="num-cell">${item.actual}</td>
                                                <td class="num-cell ${diffClass}">${diffSign}${item.diff}</td>
                                                <td class="num-cell">${formatMoney(item.cost_total)}</td>
                                                <td class="num-cell">${formatMoney(item.retail_total)}</td>
                                                <td class="num-cell">${formatMoney(item.lost_profit)}</td>
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
        return null;
    }

    select.innerHTML = data.reports.map(report => `
        <option value="${report.report_id}" data-report-number="${report.report_number ?? ''}">${escapeHtml(report.label)}</option>
    `).join('');
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
    document.getElementById('close-users-modal-btn').addEventListener('click', () => hideModal('users-modal'));
    document.getElementById('open-create-user-btn').addEventListener('click', openCreateUserModal);
    document.getElementById('close-user-form-modal-btn').addEventListener('click', () => {
        hideModal('user-form-modal');
        resetUserForm();
    });
    document.getElementById('user-form').addEventListener('submit', submitUserForm);
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
        await reloadReportsSection(locationSelect.value);
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
    await loadLocations();
    await reloadReportsSection(adminState.selectedLocation || document.getElementById('admin-location-select').value);
});