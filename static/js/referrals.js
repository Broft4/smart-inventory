(function () {
    'use strict';

    const state = {
        links: [],
        users: [],
        mainManagers: [],
        canCreateManager: false,
        canCreateLinks: false,
        canEditCommission: false,
        defaultMainPercent: 20,
        defaultManagerPercent: 10,
    };

    const currentUser = window.currentReferralUser || {};

    function qs(id) {
        return document.getElementById(id);
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function setMessage(text, ok = false) {
        const message = qs('referrals-message');
        if (!message) return;
        message.textContent = text || '';
        message.style.color = ok ? '#1f9d55' : '#dc3545';
    }

    function roleDisplayName(role) {
        if (role === 'platform_admin') return 'Админ';
        if (role === 'superadmin') return 'Главный управляющий';
        if (role === 'manager') return 'Менеджер';
        if (role === 'admin') return 'Управляющий';
        if (role === 'employee') return 'Сотрудник';
        return role || '—';
    }

    function formatDateTime(value) {
        if (!value) return '—';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return date.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
    }

    function registrationStatusLabel(status) {
        if (status === 'approved') return 'одобрена';
        if (status === 'rejected') return 'отклонена';
        return 'ожидает подтверждения';
    }

    function formatPercent(value, fallback = null) {
        const number = Number(value ?? fallback);
        if (!Number.isFinite(number)) return '—';
        return `${number.toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}%`;
    }

    async function apiJson(url, options = {}) {
        const response = await fetch(url, {
            credentials: 'same-origin',
            ...options,
            headers: {
                ...(options.body ? { 'Content-Type': 'application/json' } : {}),
                ...(options.headers || {}),
            },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || data.message || 'Запрос не выполнен.');
        }
        return data;
    }

    function userDefaultPercent(user) {
        if (user?.role === 'superadmin') return state.defaultMainPercent;
        if (user?.role === 'manager') return state.defaultManagerPercent;
        return null;
    }

    function ownerLinkByUserId(userId) {
        return state.links.find(link => Number(link.owner?.id) === Number(userId));
    }

    function renderScopeNote() {
        const note = qs('referrals-scope-note');
        if (!note) return;
        if (currentUser.role === 'manager') {
            note.textContent = 'Вы видите только свою реферальную ссылку и заявки, которые пришли по ней.';
        } else if (currentUser.role === 'superadmin') {
            note.textContent = 'Вы видите свою ссылку, менеджеров, привязанных к вам, и их рефералов.';
        } else {
            note.textContent = 'Админ видит всех главных управляющих, менеджеров, ссылки, заявки и может менять проценты.';
        }
    }

    function renderKpis() {
        const box = qs('referrals-kpis');
        if (!box) return;
        const registrations = state.links.flatMap(link => Array.isArray(link.registrations) ? link.registrations : []);
        const approved = registrations.filter(item => item.status === 'approved').length;
        const pending = registrations.filter(item => item.status === 'pending').length;
        const managers = state.users.filter(user => user.role === 'manager').length;
        box.innerHTML = `
            <div class="employee-stat-card"><span class="employee-stat-label">Ссылки</span><strong>${state.links.length}</strong></div>
            <div class="employee-stat-card"><span class="employee-stat-label">Рефералы</span><strong>${registrations.length}</strong></div>
            <div class="employee-stat-card"><span class="employee-stat-label">Одобрены</span><strong>${approved}</strong></div>
            <div class="employee-stat-card"><span class="employee-stat-label">Ожидают</span><strong>${pending}</strong></div>
            <div class="employee-stat-card"><span class="employee-stat-label">Менеджеры</span><strong>${managers}</strong></div>
        `;
    }

    function renderOwnerSelect() {
        const select = qs('referral-owner-select');
        const card = qs('referral-create-link-card');
        if (!select) return;
        card?.classList.toggle('hidden', !state.canCreateLinks);
        const activeUsers = state.users.filter(user => user.is_active !== false && ['superadmin', 'manager'].includes(user.role));
        if (!activeUsers.length) {
            select.innerHTML = '<option value="">Нет доступных участников</option>';
            return;
        }
        select.innerHTML = [
            '<option value="">Выберите участника</option>',
            ...activeUsers.map(user => `<option value="${Number(user.id)}">${escapeHtml(user.full_name)} · ${escapeHtml(roleDisplayName(user.role))}${user.manager_parent_user_name ? ` · ${escapeHtml(user.manager_parent_user_name)}` : ''}</option>`),
        ].join('');
        if (currentUser.role === 'manager') {
            select.value = String(currentUser.id);
        }
    }

    function referralSplitLine(owner) {
        if (!owner) return '';
        if (owner.role === 'manager') {
            const parent = state.users.find(user => Number(user.id) === Number(owner.manager_parent_user_id));
            const parentPercent = formatPercent(parent?.referral_commission_percent, state.defaultMainPercent);
            const managerPercent = formatPercent(owner.referral_commission_percent, state.defaultManagerPercent);
            return `Доля: главный управляющий ${owner.manager_parent_user_name ? escapeHtml(owner.manager_parent_user_name) : '—'} — ${escapeHtml(parentPercent)}, менеджер ${escapeHtml(owner.full_name || '—')} — ${escapeHtml(managerPercent)}`;
        }
        if (owner.role === 'superadmin') {
            return `Доля: главный управляющий ${escapeHtml(owner.full_name || '—')} — ${escapeHtml(formatPercent(owner.referral_commission_percent, state.defaultMainPercent))}`;
        }
        return '';
    }

    function renderRegistrations(registrations, owner) {
        if (!registrations.length) {
            return '<div class="muted-text">По этой ссылке пока никто не регистрировался.</div>';
        }
        const splitLine = referralSplitLine(owner);
        return registrations.map(request => `
            <div class="referral-registration-row">
                <strong>${escapeHtml(request.full_name || 'Без имени')}</strong>
                <div class="muted-text">${escapeHtml(request.organization_name || '—')} · ${escapeHtml(request.email || '—')} · ${escapeHtml(request.phone || '—')}</div>
                <div class="muted-text">Пригласил: ${escapeHtml(request.referred_by_user_name || '—')} · ${escapeHtml(formatDateTime(request.created_at))} · ${escapeHtml(registrationStatusLabel(request.status))}</div>
                ${splitLine ? `<div class="muted-text referral-source-text">${splitLine}</div>` : ''}
                ${request.created_user_id ? `<div class="muted-text">Созданный пользователь: #${Number(request.created_user_id)}</div>` : ''}
            </div>
        `).join('');
    }

    function renderLinks() {
        const container = qs('referrals-list');
        if (!container) return;
        if (!state.links.length) {
            container.innerHTML = '<p>Реферальных ссылок пока нет.</p>';
            return;
        }
        container.innerHTML = state.links.map(link => {
            const owner = link.owner || {};
            const registrations = Array.isArray(link.registrations) ? link.registrations : [];
            const fallbackPercent = userDefaultPercent(owner);
            return `
                <div class="user-row referral-link-row referral-page-link-row">
                    <div>
                        <strong>${escapeHtml(owner.full_name || 'Пользователь')}</strong>
                        <div class="muted-text">${escapeHtml(owner.username || '—')} · ${escapeHtml(roleDisplayName(owner.role))}${owner.manager_parent_user_name ? ` · главный: ${escapeHtml(owner.manager_parent_user_name)}` : ''}</div>
                        <div class="muted-text referral-source-text">Доля: ${escapeHtml(formatPercent(owner.referral_commission_percent, fallbackPercent))}</div>
                        <div class="referral-link-copy-row">
                            <input type="text" readonly value="${escapeHtml(link.url || '')}">
                            <button class="btn secondary btn-inline" type="button" data-copy-referral="${escapeHtml(link.url || '')}">Скопировать</button>
                            ${currentUser.role !== 'manager' ? `<button class="btn danger btn-inline" type="button" data-delete-referral="${Number(link.id)}">Удалить</button>` : ''}
                        </div>
                        <details class="referral-details" open>
                            <summary>Зарегистрировались по ссылке: ${registrations.length}</summary>
                            <div class="referral-registrations-list">${renderRegistrations(registrations, owner)}</div>
                        </details>
                    </div>
                </div>
            `;
        }).join('');
    }

    function renderManagerParentOptions(selectedId = '') {
        const select = qs('referral-manager-parent-select');
        if (!select) return;
        const managers = state.mainManagers.length ? state.mainManagers : state.users.filter(user => user.role === 'superadmin');
        select.innerHTML = ['<option value="">Выберите главного управляющего</option>', ...managers.map(user => `<option value="${Number(user.id)}">${escapeHtml(user.full_name)} · ${escapeHtml(user.username)}</option>`)].join('');
        if (selectedId) select.value = String(selectedId);
    }

    function renderUsersList() {
        const card = qs('referral-managers-card');
        const container = qs('referral-users-list');
        if (!card || !container) return;
        card.classList.toggle('hidden', !state.canCreateManager && !state.canEditCommission && currentUser.role === 'manager');
        qs('referral-manager-parent-row')?.classList.toggle('hidden', currentUser.role !== 'platform_admin');
        qs('referral-manager-percent-row')?.classList.toggle('hidden', !state.canEditCommission);
        renderManagerParentOptions();

        const rows = state.users.filter(user => ['superadmin', 'manager'].includes(user.role));
        if (!rows.length) {
            container.innerHTML = '<p>Участников пока нет.</p>';
            return;
        }
        container.innerHTML = rows.map(user => {
            const link = ownerLinkByUserId(user.id);
            const fallbackPercent = userDefaultPercent(user);
            const parentLine = user.role === 'manager'
                ? `<div class="muted-text">Главный управляющий: ${escapeHtml(user.manager_parent_user_name || 'не привязан')}</div>`
                : '';
            const editable = state.canEditCommission;
            const parentSelect = editable && user.role === 'manager'
                ? `<label class="referral-inline-label">Привязка
                    <select data-referral-parent="${Number(user.id)}">
                        ${(state.mainManagers || []).map(parent => `<option value="${Number(parent.id)}" ${Number(parent.id) === Number(user.manager_parent_user_id) ? 'selected' : ''}>${escapeHtml(parent.full_name)}</option>`).join('')}
                    </select>
                </label>`
                : '';
            const commissionControl = editable
                ? `<label class="referral-inline-label">Процент
                    <input type="number" min="0" max="100" step="0.01" data-referral-percent="${Number(user.id)}" value="${escapeHtml(user.referral_commission_percent ?? fallbackPercent ?? '')}">
                </label>`
                : '';
            const activeControl = editable
                ? `<label class="checkbox-row referral-inline-checkbox"><input type="checkbox" data-referral-active="${Number(user.id)}" ${user.is_active !== false ? 'checked' : ''}> Активен</label>`
                : '';
            return `
                <div class="user-row referral-user-row">
                    <div>
                        <strong>${escapeHtml(user.full_name)}</strong>
                        <div class="muted-text">${escapeHtml(user.username)} · ${escapeHtml(roleDisplayName(user.role))} · доля ${escapeHtml(formatPercent(user.referral_commission_percent, fallbackPercent))}</div>
                        ${parentLine}
                        <div class="muted-text">Ссылка: ${link ? 'создана' : 'не создана'}</div>
                    </div>
                    <div class="referral-user-actions">
                        ${parentSelect}
                        ${commissionControl}
                        ${activeControl}
                        ${editable ? `<button class="btn primary btn-inline" type="button" data-save-referral-user="${Number(user.id)}">Сохранить</button>` : ''}
                    </div>
                </div>
            `;
        }).join('');
    }

    function renderAll() {
        renderScopeNote();
        renderKpis();
        renderOwnerSelect();
        renderLinks();
        renderUsersList();
    }

    async function loadReferrals() {
        const list = qs('referrals-list');
        if (list) list.innerHTML = '<p>Загрузка рефералов...</p>';
        setMessage('');
        try {
            const data = await apiJson('/api/referral-links');
            state.links = Array.isArray(data.links) ? data.links : [];
            state.users = Array.isArray(data.users) ? data.users : [];
            state.mainManagers = Array.isArray(data.main_managers) ? data.main_managers : [];
            state.canCreateManager = Boolean(data.can_create_manager);
            state.canCreateLinks = Boolean(data.can_create_links);
            state.canEditCommission = Boolean(data.can_edit_commission);
            state.defaultMainPercent = Number(data.default_main_commission_percent || 20);
            state.defaultManagerPercent = Number(data.default_manager_commission_percent || 10);
            renderAll();
        } catch (error) {
            console.error(error);
            if (list) list.innerHTML = `<p class="empty-text error-text">${escapeHtml(error?.message || 'Не удалось загрузить рефералов.')}</p>`;
            setMessage(error?.message || 'Не удалось загрузить рефералов.');
        }
    }

    async function createReferralLink() {
        const userId = Number(qs('referral-owner-select')?.value || 0);
        if (!userId) {
            setMessage('Выберите участника.');
            return;
        }
        try {
            const data = await apiJson('/api/referral-links', {
                method: 'POST',
                body: JSON.stringify({ user_id: userId }),
            });
            setMessage(data.message || 'Ссылка создана.', true);
            await loadReferrals();
        } catch (error) {
            console.error(error);
            setMessage(error?.message || 'Не удалось создать ссылку.');
        }
    }

    async function deleteReferralLink(linkId) {
        if (!linkId || !confirm('Удалить реферальную ссылку? Заявки перестанут отображаться как реферальные.')) return;
        try {
            const data = await apiJson(`/api/referral-links/${encodeURIComponent(linkId)}`, { method: 'DELETE' });
            setMessage(data.message || 'Ссылка удалена.', true);
            await loadReferrals();
        } catch (error) {
            console.error(error);
            setMessage(error?.message || 'Не удалось удалить ссылку.');
        }
    }

    async function copyReferralLink(url) {
        try {
            await navigator.clipboard.writeText(url);
            setMessage('Ссылка скопирована.', true);
        } catch {
            prompt('Скопируйте ссылку:', url);
        }
    }

    function resetManagerForm() {
        qs('referral-manager-form')?.reset();
        qs('referral-manager-active').checked = true;
        renderManagerParentOptions();
    }

    async function createManager(event) {
        event.preventDefault();
        const payload = {
            full_name: qs('referral-manager-full-name')?.value.trim() || '',
            birth_date: qs('referral-manager-birth-date')?.value || '',
            username: qs('referral-manager-username')?.value.trim() || '',
            email: qs('referral-manager-email')?.value.trim() || null,
            password: qs('referral-manager-password')?.value || '',
            is_active: qs('referral-manager-active')?.checked !== false,
        };
        if (currentUser.role === 'platform_admin') {
            payload.manager_parent_user_id = Number(qs('referral-manager-parent-select')?.value || 0) || null;
        }
        if (state.canEditCommission) {
            const percent = qs('referral-manager-percent')?.value;
            if (percent !== '') payload.referral_commission_percent = Number(percent);
        }
        try {
            await apiJson('/api/referral-managers', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            setMessage('Менеджер создан.', true);
            resetManagerForm();
            await loadReferrals();
        } catch (error) {
            console.error(error);
            setMessage(error?.message || 'Не удалось создать менеджера.');
        }
    }

    async function saveReferralUser(userId) {
        const user = state.users.find(item => Number(item.id) === Number(userId));
        if (!user) return;
        const percentInput = document.querySelector(`[data-referral-percent="${Number(userId)}"]`);
        const parentSelect = document.querySelector(`[data-referral-parent="${Number(userId)}"]`);
        const activeInput = document.querySelector(`[data-referral-active="${Number(userId)}"]`);
        const payload = {
            referral_commission_percent: percentInput?.value === '' ? null : Number(percentInput?.value),
            is_active: activeInput ? activeInput.checked : user.is_active !== false,
        };
        if (user.role === 'manager') {
            payload.manager_parent_user_id = Number(parentSelect?.value || user.manager_parent_user_id || 0) || null;
        }
        try {
            await apiJson(`/api/referral-users/${encodeURIComponent(userId)}`, {
                method: 'PATCH',
                body: JSON.stringify(payload),
            });
            setMessage('Настройки участника сохранены.', true);
            await loadReferrals();
        } catch (error) {
            console.error(error);
            setMessage(error?.message || 'Не удалось сохранить настройки.');
        }
    }

    document.addEventListener('click', (event) => {
        const copyButton = event.target.closest('[data-copy-referral]');
        if (copyButton) {
            copyReferralLink(copyButton.dataset.copyReferral || '');
            return;
        }
        const deleteButton = event.target.closest('[data-delete-referral]');
        if (deleteButton) {
            deleteReferralLink(Number(deleteButton.dataset.deleteReferral || 0));
            return;
        }
        const saveButton = event.target.closest('[data-save-referral-user]');
        if (saveButton) {
            saveReferralUser(Number(saveButton.dataset.saveReferralUser || 0));
        }
    });

    document.addEventListener('DOMContentLoaded', () => {
        qs('refresh-referrals-btn')?.addEventListener('click', loadReferrals);
        qs('create-referral-link-btn')?.addEventListener('click', createReferralLink);
        qs('referral-manager-form')?.addEventListener('submit', createManager);
        qs('referral-manager-reset-btn')?.addEventListener('click', resetManagerForm);
        loadReferrals();
    });
})();
