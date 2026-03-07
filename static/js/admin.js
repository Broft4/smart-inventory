function formatDateTime(value) {
    return value || '-';
}

function encodeUser(user) {
    return encodeURIComponent(JSON.stringify(user));
}

function showModal(modalId) {
    document.getElementById(modalId).classList.remove('hidden');
    document.body.classList.add('modal-open');
}

function hideModal(modalId) {
    document.getElementById(modalId).classList.add('hidden');
    if (document.querySelectorAll('.modal-overlay:not(.hidden)').length === 0) {
        document.body.classList.remove('modal-open');
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
                <strong>${user.full_name}</strong>
                <div class="muted-text">${user.username} · ${user.role} · ${user.location || 'без точки'}</div>
                <div class="muted-text">Дата рождения: ${user.birth_date} · ${user.is_active ? 'активен' : 'выключен'}</div>
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
    document.getElementById('user-location').required = true;
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

        if (typeof data.detail === 'string') {
            return data.detail;
        }

        if (typeof data.message === 'string') {
            return data.message;
        }

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

    const locationValue = document.getElementById('user-location').value || null;
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
        if (password.trim()) {
            payload.password = password;
        }
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
        message.style.color = 'green';
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

async function loadReportsList(location) {
    const select = document.getElementById('admin-report-select');
    select.disabled = true;
    select.innerHTML = '<option>Загрузка...</option>';

    const response = await fetch(`/api/reports?location=${encodeURIComponent(location)}`);
    if (!response.ok) throw new Error('Ошибка загрузки списка ревизий');
    const data = await response.json();

    if (!data.reports.length) {
        select.innerHTML = '<option value="">Нет сохраненных ревизий</option>';
        select.disabled = true;
        return null;
    }

    select.innerHTML = data.reports.map(report => (`<option value="${report.report_id}">${report.label}</option>`)).join('');
    select.disabled = false;
    return Number(select.value);
}

async function loadAdminReport(location, reportId) {
    const locationSpan = document.getElementById('report-location');
    const dateSpan = document.getElementById('report-date');
    const statusSpan = document.getElementById('report-status');
    const idSpan = document.getElementById('report-id');
    const totalPlusSpan = document.getElementById('total-plus');
    const totalMinusSpan = document.getElementById('total-minus');
    const categoriesContainer = document.getElementById('report-categories');

    try {
        const params = new URLSearchParams({ location });
        if (reportId) params.set('report_id', String(reportId));

        const response = await fetch(`/api/report?${params.toString()}`);
        if (!response.ok) throw new Error('Ошибка загрузки отчета');
        const report = await response.json();

        locationSpan.textContent = report.location;
        dateSpan.textContent = formatDateTime(report.date);
        statusSpan.textContent = report.status || '-';
        idSpan.textContent = report.report_id ?? '-';
        totalPlusSpan.textContent = `+${report.total_plus}`;
        totalMinusSpan.textContent = report.total_minus;
        categoriesContainer.innerHTML = '';

        if (!report.categories.length) {
            categoriesContainer.innerHTML = '<p style="text-align:center;">По этой ревизии пока нет данных.</p>';
            return;
        }

        report.categories.forEach(cat => {
            const card = document.createElement('div');
            card.className = `category-card status-${cat.status}`;
            let html = `<h3>${cat.name}</h3>`;

            if (cat.status === 'green') {
                html += '<p style="color:#28a745; font-weight:bold;">✅ Расхождений нет</p>';
            } else if (cat.status === 'orange') {
                html += '<p style="color:#fd7e14; font-weight:bold;">⏳ Категория еще проверяется поштучно</p>';
            } else if (cat.problem_items.length > 0) {
                html += '<p style="color:#dc3545; font-weight:bold;">⚠️ Зафиксированы расхождения</p>';
                html += `
                    <table class="admin-table">
                        <tr>
                            <th>Товар</th>
                            <th>План</th>
                            <th>Факт</th>
                            <th>Разница</th>
                            <th>Сотрудник</th>
                        </tr>
                `;
                cat.problem_items.forEach(item => {
                    const diffSign = item.diff > 0 ? '+' : '';
                    const diffColor = item.diff > 0 ? '#28a745' : '#dc3545';
                    html += `
                        <tr>
                            <td>${item.name}</td>
                            <td style="text-align:center;">${item.expected}</td>
                            <td style="text-align:center;">${item.actual}</td>
                            <td style="text-align:center; color:${diffColor}; font-weight:bold;">${diffSign}${item.diff}</td>
                            <td>${item.checked_by || '-'}</td>
                        </tr>
                    `;
                });
                html += '</table>';
            } else {
                html += '<p>По этой категории пока нет завершенных проверок.</p>';
            }

            card.innerHTML = html;
            categoriesContainer.appendChild(card);
        });
    } catch (error) {
        console.error(error);
        categoriesContainer.innerHTML = '<p style="color:red; text-align:center;">Ошибка загрузки данных</p>';
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
    const reportSelect = document.getElementById('admin-report-select');
    const reportId = await loadReportsList(location);
    await loadAdminReport(location, reportId);
    reportSelect.onchange = async () => {
        const selected = reportSelect.value ? Number(reportSelect.value) : null;
        await loadAdminReport(location, selected);
    };
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
    document.getElementById('logout-btn').addEventListener('click', logout);
    document.getElementById('open-users-btn').addEventListener('click', async () => {
        showModal('users-modal');
        await loadUsers();
    });
    document.getElementById('close-users-modal-btn').addEventListener('click', () => hideModal('users-modal'));
    document.getElementById('open-create-user-btn').addEventListener('click', openCreateUserModal);
    document.getElementById('close-user-form-modal-btn').addEventListener('click', () => {
        hideModal('user-form-modal');
        resetUserForm();
    });
    document.getElementById('user-form').addEventListener('submit', submitUserForm);
    document.getElementById('user-form-reset').addEventListener('click', resetUserForm);
    document.getElementById('delete-report-btn').addEventListener('click', deleteSelectedReport);
    document.getElementById('user-role').addEventListener('change', (e) => {
        const isEmployee = e.target.value === 'employee';
        document.getElementById('user-location').required = isEmployee;
    });
    locationSelect.addEventListener('change', async () => {
        await reloadReportsSection(locationSelect.value);
    });

    initModalCloseBehavior();
    await reloadReportsSection(locationSelect.value);
});

async function extractErrorMessage(response) {
    try {
        const data = await response.json();

        if (Array.isArray(data.detail)) {
            return data.detail.map(item => item.msg).join(', ');
        }

        if (typeof data.detail === 'string') {
            return data.detail;
        }

        if (typeof data.message === 'string') {
            return data.message;
        }

        return 'Ошибка сохранения пользователя.';
    } catch {
        return 'Ошибка сохранения пользователя.';
    }
}