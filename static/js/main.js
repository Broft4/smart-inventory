let inventoryState = null;
let subcategoryAttempts = {};
let itemAttempts = {};
let inventoryLoading = false;
const pendingVerificationTargets = new Set();

function getInventoryStatusElement() {
    return document.getElementById('inventory-status');
}

function setInventoryStatus(mode, message, options = {}) {
    const status = getInventoryStatusElement();
    if (!status) return;

    const { retry = false } = options;
    if (!mode) {
        status.className = 'inventory-status hidden';
        status.innerHTML = '';
        return;
    }

    const safeMessage = escapeHtml(message || '');
    const retryButton = retry ? '<button type="button" id="inventory-retry-btn" class="btn secondary btn-inline">Повторить</button>' : '';
    const spinner = mode === 'loading' ? '<span class="inventory-spinner" aria-hidden="true"></span>' : '';
    status.className = `inventory-status ${mode}`;
    status.innerHTML = `
        <div class="inventory-status-row">
            ${spinner}
            <div class="inventory-status-text">${safeMessage}</div>
            ${retryButton}
        </div>
    `;

    const retryElement = document.getElementById('inventory-retry-btn');
    if (retryElement) {
        retryElement.addEventListener('click', () => loadInventory({ forceReload: true }));
    }
}

function setRefreshButtonState(isLoading) {
    const button = document.getElementById('refresh-btn');
    if (!button) return;
    button.disabled = Boolean(isLoading);
    button.textContent = isLoading ? 'Загрузка...' : 'Обновить';
}

const employeePageState = {
    filter: 'mine',
    searchQuery: '',
};

const employeeUiState = {
    openCategories: new Set(),
    openSubcategories: new Set(),
};

let inventoryReloadQueued = false;
const selectionBusyTargets = new Set();

function getRevisionStorageKey(reportDate) {
    const userId = window.currentUser?.id || 'unknown';
    const location = window.currentUser?.location || 'unknown';
    return `inventory_revision_${userId}_${location}_${reportDate}`;
}

function getEmployeeRevisionState(reportDate = inventoryState?.report_date) {
    if (inventoryState?.employee_finished || inventoryState?.report_completed) return 'finished';
    if (inventoryState?.report_id) return inventoryState?.employee_started ? 'started' : 'idle';
    if (!reportDate) return 'idle';
    return localStorage.getItem(getRevisionStorageKey(reportDate)) || 'idle';
}

function setEmployeeRevisionState(state, reportDate = inventoryState?.report_date) {
    if (!reportDate) return;
    localStorage.setItem(getRevisionStorageKey(reportDate), state);
}

function employeeRevisionIsActive() {
    return !employeeRevisionIsFinished() && getEmployeeRevisionState() === 'started';
}

function employeeRevisionIsFinished() {
    return Boolean(inventoryState?.employee_finished || inventoryState?.report_completed || getEmployeeRevisionState() === 'finished');
}

function renderEmployeeRevisionControls() {
    const startBtn = document.getElementById('start-revision-btn');
    const refreshBtn = document.getElementById('refresh-btn');
    const finishBtn = document.getElementById('finish-revision-btn');
    const banner = document.getElementById('employee-revision-banner');
    const tools = document.querySelector('.employee-tools-card');
    const categories = document.getElementById('categories-container');
    const summary = document.getElementById('inventory-summary');
    const hint = document.getElementById('finish-hint');
    if (!startBtn || !refreshBtn || !finishBtn || !banner || !tools || !categories || !summary || !hint) return;

    const finished = Boolean(inventoryState?.employee_finished || inventoryState?.report_completed);
    const state = finished ? 'finished' : getEmployeeRevisionState();
    const active = !finished && state === 'started';
    const startBlockedMessage = inventoryState?.start_block_message || '';
    const canFinishReport = Boolean(inventoryState?.can_finish_report);

    startBtn.classList.toggle('hidden', active || finished);
    startBtn.textContent = 'Начать ревизию';
    startBtn.disabled = Boolean(!active && !finished && startBlockedMessage);
    refreshBtn.classList.toggle('hidden', !active);
    finishBtn.classList.toggle('hidden', !active);
    finishBtn.disabled = active && !canFinishReport;

    tools.classList.toggle('hidden', !active);
    categories.classList.toggle('hidden', !active);
    summary.classList.toggle('hidden', false);

    if (finished) {
        banner.className = 'employee-revision-banner done';
        banner.innerHTML = inventoryState?.report_completed
            ? '<strong>Ревизия по точке завершена.</strong>'
            : '<strong>Ваша ревизия завершена.</strong> Продолжить работу в этот день уже нельзя.';
        hint.textContent = 'Кнопка «Завершить ревизию» фиксирует завершение на сервере и блокирует продолжение работы до следующего дня.';
    } else if (!active) {
        banner.className = 'employee-revision-banner idle';
        if (startBlockedMessage) {
            banner.innerHTML = `<strong>Нельзя начать новую ревизию.</strong> ${escapeHtml(startBlockedMessage)}`;
            hint.textContent = 'Сначала должна быть закрыта ваша предыдущая незавершённая ревизия.';
        } else {
            banner.innerHTML = inventoryState?.report_started
                ? '<strong>Вы ещё не начали свою ревизию.</strong> Нажмите «Начать ревизию», чтобы открыть категории и приступить к работе.'
                : '<strong>Ревизия ещё не начата.</strong> Нажмите «Начать ревизию», чтобы открыть категории и приступить к работе.';
            hint.textContent = 'После завершения ревизии на текущий день продолжить её уже будет нельзя.';
        }
    } else {
        banner.className = 'employee-revision-banner hidden';
        banner.innerHTML = '';
        hint.textContent = inventoryState?.finish_block_message
            ? inventoryState.finish_block_message
            : 'Когда закончите работу, нажмите «Завершить ревизию». После подтверждения продолжить её в этот день уже нельзя.';
    }
}

function ensureRevisionStateForCurrentDay() {
    if (!inventoryState?.report_date) return;
    const key = getRevisionStorageKey(inventoryState.report_date);
    if (inventoryState?.employee_finished || inventoryState?.report_completed) {
        localStorage.setItem(key, 'finished');
        return;
    }
    if (inventoryState?.employee_started) {
        localStorage.setItem(key, 'started');
        return;
    }
    if (!localStorage.getItem(key)) {
        localStorage.setItem(key, 'idle');
    }
}

function getCategory(categoryId) {
    return inventoryState?.categories?.find(category => category.id === categoryId) || null;
}

function findSubcategory(subId) {
    for (const category of inventoryState.categories) {
        for (const sub of category.subcategories) {
            if (sub.id === subId) return { category, sub };
        }
    }
    return null;
}

function normalizeSearch(value) {
    return String(value ?? '').trim().toLowerCase();
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

function highlightMatch(text, query) {
    const safe = escapeHtml(text);
    const normalizedQuery = normalizeSearch(query);
    if (!normalizedQuery) return safe;
    const regex = new RegExp(`(${escapeRegExp(query)})`, 'ig');
    return safe.replace(regex, '<span class="search-highlight">$1</span>');
}

function categoryHasFreeWork(category) {
    if (!category) return false;
    if (category.can_take) return true;
    if ((category.subcategories || []).some(sub => sub.can_take)) return true;
    return categoryHasFreeDiagnosticItems(category);
}

function categoryHasBusyWork(category) {
    if (!category) return false;
    return Boolean(category.is_blocked_by_other || category.has_other_subcategories || category.has_other_items);
}

function getCategoryBucket(category) {
    if (categoryHasPendingMineWork(category)) return 'mine';
    if (categoryHasFreeWork(category)) return 'free';
    if (categoryHasCompletedMineWork(category)) return 'completed';
    if (categoryHasBusyWork(category)) return 'busy';
    return 'other';
}


function itemMatchesSearch(item, query) {
    const q = normalizeSearch(query);
    if (!q) return true;
    return normalizeSearch(item.name).includes(q);
}

function subcategoryMatchesSearch(subcategory, query) {
    const q = normalizeSearch(query);
    if (!q) return true;
    if (normalizeSearch(subcategory.name).includes(q)) return true;
    return (subcategory.items || []).some(item => itemMatchesSearch(item, q));
}

function categoryMatchesSearch(category, query) {
    const q = normalizeSearch(query);
    if (!q) return true;
    if (normalizeSearch(category.name).includes(q)) return true;
    if (normalizeSearch(category.assigned_to || '').includes(q)) return true;
    return (category.subcategories || []).some(subcategory => subcategoryMatchesSearch(subcategory, q));
}

function categoryHasProblems(category) {
    return (category.subcategories || []).some(subcategory => {
        if (subcategory.status === 'red') return true;
        return (subcategory.items || []).some(item => item.status === 'red');
    });
}

function subcategoryBelongsToCurrentUser(category, subcategory) {
    if (!subcategory) return false;
    if (subcategory.assigned_to_current_user || subcategory.taken_as_part_of_category || subcategory.has_my_items) return true;
    return (subcategory.items || []).some(item => item.assigned_to_current_user);
}

function subcategoryHasPendingMineWork(category, subcategory) {
    if (!subcategoryBelongsToCurrentUser(category, subcategory)) return false;

    const hasPendingWholeSubcategory = (subcategory.assigned_to_current_user || subcategory.taken_as_part_of_category) && !subcategory.is_completed;
    const hasPendingDiagnosticItems = (subcategory.items || []).some(item => item.assigned_to_current_user && !item.is_final);

    return hasPendingWholeSubcategory || hasPendingDiagnosticItems;
}

function categoryHasPendingMineWork(category) {
    if (category.assigned_to_current_user && !category.is_completed) return true;
    return (category.subcategories || []).some(subcategory => subcategoryHasPendingMineWork(category, subcategory));
}

function subcategoryHasCompletedMineWork(category, subcategory) {
    if (!subcategoryBelongsToCurrentUser(category, subcategory)) return false;

    const hasCompletedWholeSubcategory = (subcategory.assigned_to_current_user || subcategory.taken_as_part_of_category) && subcategory.is_completed;
    const myItems = (subcategory.items || []).filter(item => item.assigned_to_current_user);
    const hasCompletedDiagnosticItems = myItems.length > 0 && myItems.every(item => item.is_final);

    return hasCompletedWholeSubcategory || hasCompletedDiagnosticItems;
}

function categoryHasCompletedMineWork(category) {
    if (category.assigned_to_current_user && category.is_completed) return true;
    return (category.subcategories || []).some(subcategory => subcategoryHasCompletedMineWork(category, subcategory));
}

function captureEmployeeUiState() {
    employeeUiState.openCategories = new Set((inventoryState?.categories || []).filter(category => category.is_open).map(category => category.id));
    employeeUiState.openSubcategories = new Set(
        (inventoryState?.categories || []).flatMap(category => (category.subcategories || []).filter(sub => sub.is_expanded).map(sub => sub.id))
    );
}

function applyEmployeeUiState() {
    if (!inventoryState?.categories) return;

    for (const category of inventoryState.categories) {
        if (employeeUiState.openCategories.has(category.id)) {
            category.is_open = true;
        }

        for (const sub of category.subcategories || []) {
            if (employeeUiState.openSubcategories.has(sub.id)) {
                sub.is_expanded = true;
            }
        }
    }
}

function getVisibleSubcategories(category, query, modeOverride = null) {
    const q = normalizeSearch(query);
    const allSubcategories = category.subcategories || [];
    const mode = modeOverride || employeePageState.filter;
    let scopedSubcategories = allSubcategories;

    if (mode === 'mine') {
        scopedSubcategories = allSubcategories.filter(subcategory => subcategoryHasPendingMineWork(category, subcategory));
    } else if (mode === 'completed') {
        scopedSubcategories = allSubcategories.filter(subcategory => subcategoryHasCompletedMineWork(category, subcategory));
    } else if (mode === 'free') {
        if (category.can_take) {
            scopedSubcategories = allSubcategories;
        } else {
            scopedSubcategories = allSubcategories.filter(subcategory => subcategory.can_take || (subcategory.items || []).some(item => item.can_take));
        }
    }

    if (!q) return scopedSubcategories;

    const categoryDirectMatch = normalizeSearch(category.name).includes(q) || normalizeSearch(category.assigned_to || '').includes(q);
    if (categoryDirectMatch) return scopedSubcategories;
    return scopedSubcategories.filter(subcategory => subcategoryMatchesSearch(subcategory, q));
}

function categoryPassesFilter(category) {
    const mode = employeePageState.filter;
    if (mode === 'mine') return categoryHasPendingMineWork(category);
    if (mode === 'completed') return categoryHasCompletedMineWork(category);
    if (mode === 'free') return categoryHasFreeWork(category);
    if (mode === 'busy') return categoryHasBusyWork(category);
    if (mode === 'problem') return categoryHasProblems(category);
    return true;
}


function getFilteredCategories() {
    if (!inventoryState?.categories) return [];
    return inventoryState.categories.filter(category => categoryPassesFilter(category) && categoryMatchesSearch(category, employeePageState.searchQuery));
}

function sortCategories(categories) {
    return [...categories].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
}

function renderSummary() {
    const summary = document.getElementById('inventory-summary');
    const dateLine = document.getElementById('report-date-line');
    const cycleLine = document.getElementById('cycle-line');
    const allCategories = inventoryState?.categories || [];
    const myCategories = allCategories.filter(categoryHasPendingMineWork);
    const completedCategories = allCategories.filter(categoryHasCompletedMineWork);
    const freeCategories = allCategories.filter(categoryHasFreeWork);
    const occupiedCategories = allCategories.filter(categoryHasBusyWork);
    const problemCategories = allCategories.filter(categoryHasProblems);

    if (dateLine) dateLine.textContent = `Общая ревизия за ${inventoryState.report_date}`;
    if (cycleLine) cycleLine.textContent = `Текущий цикл выбора: с ${inventoryState.cycle_started_at}. Осталось дней: ${inventoryState.cycle_days_left}.`;

    const statMy = document.getElementById('stat-my');
    const statFree = document.getElementById('stat-free');
    const statBusy = document.getElementById('stat-busy');
    const statCompleted = document.getElementById('stat-completed');
    const statProblem = document.getElementById('stat-problem');

    if (statMy) statMy.textContent = String(myCategories.length);
    if (statFree) statFree.textContent = String(freeCategories.length);
    if (statBusy) statBusy.textContent = String(occupiedCategories.length);
    if (statCompleted) statCompleted.textContent = String(completedCategories.length);
    if (statProblem) statProblem.textContent = String(problemCategories.length);

    if (summary) {
        summary.innerHTML = `
            <strong>Мои выборы:</strong> ${myCategories.length}.
            <strong>Завершённые:</strong> ${completedCategories.length}.
            <strong>Свободные:</strong> ${freeCategories.length}.
            <strong>У других сотрудников:</strong> ${occupiedCategories.length}.
            <strong>С расхождениями:</strong> ${problemCategories.length}.
        `;
    }

    renderEmployeeRevisionControls();
}


function buildSectionHeader(title, description, count) {
    return `
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin:18px 0 10px;">
            <div>
                <h3 class="section-title" style="margin:0;">${escapeHtml(title)}</h3>
                <p class="muted-text" style="margin:6px 0 0;">${escapeHtml(description)}</p>
            </div>
            <span class="assigned-badge">${count}</span>
        </div>
    `;
}

function isDiagnosticCategory(category) {
    return normalizeSearch(category?.name) === normalizeSearch('Без категории');
}

function isDiagnosticSubcategory(category, sub) {
    return isDiagnosticCategory(category) || normalizeSearch(sub?.name) === normalizeSearch('Без подкатегории');
}

function categoryHasFreeDiagnosticItems(category) {
    return (category?.subcategories || []).some(sub => (sub?.items || []).some(item => item.can_take));
}

function getSelectionBusyKey(kind, categoryId, subcategoryId = null, itemId = null) {
    return [kind, categoryId || '', subcategoryId || '', itemId || ''].join('::');
}

function setSelectionButtonsBusyState(categoryId, subcategoryId, itemId, isBusy) {
    const selectors = [];
    if (categoryId && !subcategoryId && !itemId) {
        selectors.push(`button[onclick="takeCategory('${categoryId}')"]`);
    }
    if (categoryId && subcategoryId && !itemId) {
        selectors.push(`button[onclick="takeSubcategory('${categoryId}', '${subcategoryId}')"]`);
    }
    if (categoryId && subcategoryId && itemId) {
        selectors.push(`button[onclick="takeItem('${categoryId}', '${subcategoryId}', '${itemId}')"]`);
    }
    for (const selector of selectors) {
        document.querySelectorAll(selector).forEach(button => {
            button.disabled = Boolean(isBusy);
            button.dataset.originalText = button.dataset.originalText || button.textContent;
            button.textContent = isBusy ? '...' : button.dataset.originalText;
        });
    }
}

function beginSelectionRequest(kind, categoryId, subcategoryId = null, itemId = null) {
    const key = getSelectionBusyKey(kind, categoryId, subcategoryId, itemId);
    if (selectionBusyTargets.has(key)) return false;
    selectionBusyTargets.add(key);
    setSelectionButtonsBusyState(categoryId, subcategoryId, itemId, true);
    return true;
}

function finishSelectionRequest(kind, categoryId, subcategoryId = null, itemId = null) {
    const key = getSelectionBusyKey(kind, categoryId, subcategoryId, itemId);
    selectionBusyTargets.delete(key);
    setSelectionButtonsBusyState(categoryId, subcategoryId, itemId, false);
}

function buildSelectionConfirmMessage(kind, label) {
    const safeLabel = label || 'эту позицию';
    const entityLabel = kind === 'category' ? 'категорию' : kind === 'subcategory' ? 'подкатегорию' : 'товар';
    return `Подтвердите выбор: ${entityLabel} «${safeLabel}».

После закрепления отменить действие нельзя до начала следующей ревизии.`;
}


function categoryMetaText(category) {
    if (category.is_diagnostic && category.has_my_items) {
        return 'служебная ветка: не входит в общую ревизию, у вас есть закреплённые товары';
    }
    if (category.is_diagnostic && category.has_other_items) {
        return 'служебная ветка: не входит в общую ревизию, внутри есть товары, взятые другими сотрудниками';
    }
    if (category.is_diagnostic && categoryHasFreeDiagnosticItems(category)) {
        return 'служебная ветка: не входит в общую ревизию, здесь выбираются отдельные товары';
    }
    if (category.is_diagnostic) {
        return 'служебная ветка: не входит в общую ревизию';
    }

    if (category.is_completed && (category.assigned_to_current_user || category.has_my_subcategories || category.has_my_items)) return 'ваши выборы завершены';
    if (category.assigned_to_current_user) return 'вся категория закреплена за вами';
    if (category.has_my_subcategories && category.has_other_subcategories) return 'подкатегории распределены между сотрудниками';
    if (category.has_my_subcategories) return 'у вас есть закреплённые подкатегории';
    if (category.has_my_items) return 'у вас есть закреплённые товары';
    if (!category.selected_whole_category && (category.selected_subcategory_names || []).length) {
        return 'администратор выбрал только часть категории';
    }
    if (category.is_blocked_by_other) return `категория занята: ${category.assigned_to}`;
    if (category.has_other_subcategories || category.has_other_items) return 'часть ветки занята другими';
    return 'свободна';
}


function renderDirectItemsBlock(category, sub, query) {
    const visibleItems = (() => {
        const q = normalizeSearch(query);
        if (!q) return sub.items || [];
        return (sub.items || []).filter(item => itemMatchesSearch(item, q));
    })();
    const itemsHtml = visibleItems.map(item => {
        const canVerify = category.assigned_to_current_user && !item.is_final;
        let itemMessage = item.status === 'green' ? 'Товар подтверждён.' : (item.status === 'red' ? 'Расхождение по товару зафиксировано.' : 'Товар входит в категорию без подкатегории.');
        let itemMessageColor = item.status === 'green' ? 'green' : (item.status === 'red' ? 'red' : '');
        return `
            <div class="item-card">
                <h4>${highlightMatch(item.name, query)} (${escapeHtml(item.uom)})</h4>
                <div class="input-group">
                    <input type="number" id="input-${item.id}" placeholder="Факт. шт." min="0" step="1" ${canVerify ? '' : 'disabled'}>
                    <button class="btn check btn-inline" onclick="verifyItem('${item.id}')" ${canVerify ? '' : 'disabled'}>Ввод</button>
                </div>
                <div id="msg-${item.id}" class="message" style="color:${itemMessageColor};">${itemMessage}</div>
            </div>
        `;
    }).join('');
    return `
        <div class="category-card subcategory-card status-${sub.status}">
            <h3>📦 ${escapeHtml(category.direct_items_label || 'Товары без подкатегории')}</h3>
            <div class="muted-text" style="margin-bottom:10px;">Промежуточная ветка скрыта: товары без подкатегории показаны сразу внутри категории.</div>
            <div class="items-container" style="display:block; border-top:none; padding-top:0; margin-top:0;">${itemsHtml || '<div class="employee-empty-state">По этому запросу товары не найдены.</div>'}</div>
        </div>
    `;
}

function renderSubcategoryCard(category, sub, query, modeOverride = null) {
    if (sub.flatten_mode === 'category_direct') {
        return renderDirectItemsBlock(category, sub, query);
    }
    const effectiveMode = modeOverride || employeePageState.filter;
    const completedReadonly = effectiveMode === 'completed';
    const locked = sub.is_locked;
    const diagnosticBucket = isDiagnosticSubcategory(category, sub) || sub.is_diagnostic;
    let icon = diagnosticBucket ? '🧭' : '📂';
    if (sub.is_completed && !diagnosticBucket) icon = '✅';
    if (locked && !sub.assigned_to_current_user && !sub.taken_as_part_of_category && !diagnosticBucket) icon = '🔒';

    const queryActive = Boolean(normalizeSearch(query));
    const subExpanded = queryActive ? true : Boolean(sub.is_expanded);
    const visibleItems = (() => {
        if (!queryActive) return sub.items || [];
        const q = normalizeSearch(query);
        const subDirectMatch = normalizeSearch(sub.name).includes(q);
        if (subDirectMatch) return sub.items || [];
        return (sub.items || []).filter(item => itemMatchesSearch(item, q));
    })();

    let selectionHtml = '';
    const quickActionHtml = (!completedReadonly && !diagnosticBucket && sub.can_take)
        ? `<div class="subcategory-action-row" style="margin:8px 0 6px;"><button class="btn secondary btn-inline" onclick="takeSubcategory('${category.id}', '${sub.id}')">Взять подкатегорию</button></div>`
        : '';
    if (diagnosticBucket) {
        selectionHtml = '<div class="muted-text diagnostic-help">Эта служебная ветка не считает общий итог. Здесь можно закреплять только отдельные товары.</div>';
    } else if (sub.can_take) {
        selectionHtml = '<div class="muted-text">Подкатегория доступна для выбора.</div>';
    } else if (sub.is_blocked_by_other && !sub.assigned_to_current_user && !sub.taken_as_part_of_category) {
        selectionHtml = `<div class="muted-text">Подкатегория занята: <strong>${escapeHtml(sub.assigned_to || 'другой сотрудник')}</strong>.</div>`;
    } else if (sub.assigned_to_current_user) {
        selectionHtml = '<div class="muted-text">Подкатегория закреплена за вами.</div>';
    } else if (sub.taken_as_part_of_category) {
        selectionHtml = '<div class="muted-text">Доступна вам в составе выбранной категории.</div>';
    } else if (sub.has_my_items) {
        selectionHtml = '<div class="muted-text">У вас есть закреплённые товары внутри этой ветки.</div>';
    } else if (sub.has_other_items) {
        selectionHtml = '<div class="muted-text">Часть товаров уже закреплена другими сотрудниками.</div>';
    }

    const itemsHtml = visibleItems.map(item => {
        const diagnosticItem = diagnosticBucket || item.is_diagnostic;
        const canVerifyDiagnosticItem = diagnosticItem && item.assigned_to_current_user && !item.is_final;
        const canVerifyRegularItem = !diagnosticItem && sub.status === 'orange' && !item.is_final;
        const itemDisabled = !(canVerifyDiagnosticItem || canVerifyRegularItem);

        let itemMessage = '';
        let itemMessageColor = '';
        if (item.status === 'green') {
            itemMessage = 'Товар подтверждён.';
            itemMessageColor = 'green';
        } else if (item.status === 'red') {
            itemMessage = 'Расхождение по товару зафиксировано.';
            itemMessageColor = 'red';
        } else if (diagnosticItem && item.assigned_to_current_user) {
            itemMessage = 'Товар закреплён за вами. Можно ввести факт.';
        } else if (diagnosticItem && item.is_blocked_by_other) {
            itemMessage = `Товар закреплён за сотрудником ${item.assigned_to || 'другой сотрудник'}.`;
        } else if (diagnosticItem && item.can_take) {
            itemMessage = 'Товар ещё не выбран на сегодня.';
        } else if (diagnosticItem) {
            itemMessage = 'Служебный товар без активного закрепления.';
        }

        const diagnosticActions = diagnosticItem
            ? `<div class="diagnostic-item-actions">${item.can_take ? `<button class="btn secondary btn-inline" onclick="takeItem('${category.id}', '${sub.id}', '${item.id}')">Взять товар</button>` : ''}${item.assigned_to_current_user ? '<span class="assigned-badge">Закреплён за вами</span>' : ''}</div>`
            : '';

        return `
            <div class="item-card ${diagnosticItem ? 'diagnostic-item-card' : ''}">
                <h4>${highlightMatch(item.name, query)} (${escapeHtml(item.uom)})</h4>
                ${diagnosticActions}
                <div class="input-group">
                    <input type="number" id="input-${item.id}" placeholder="Факт. шт." min="0" step="1" ${itemDisabled ? 'disabled' : ''}>
                    <button class="btn check btn-inline" onclick="verifyItem('${item.id}', '${sub.id}')" ${itemDisabled ? 'disabled' : ''}>Ввод</button>
                </div>
                <div id="msg-${item.id}" class="message" style="color:${itemMessageColor};">${itemMessage}</div>
            </div>
        `;
    }).join('');

    const canCountThisSub = !completedReadonly && !diagnosticBucket && (sub.assigned_to_current_user || sub.taken_as_part_of_category);
    const showItemsBlock = !completedReadonly && (diagnosticBucket || sub.status === 'orange');
    const itemsTitle = diagnosticBucket
        ? '<p class="items-warning diagnostic-warning">⚠️ Служебная ветка. Общий ввод отключён: выбирайте и проверяйте только отдельные товары.</p>'
        : '<p class="items-warning">⚠️ Не сошлось. Считаем поштучно:</p>';

    return `
        <div class="category-card subcategory-card status-${sub.status}" id="card-${sub.id}">
            <h3 id="title-${sub.id}" ${completedReadonly ? '' : `onclick="toggleSubcategory('${sub.id}')"`}>${icon} ${highlightMatch(sub.name, query)}</h3>
            ${quickActionHtml}
            <div id="body-${sub.id}" style="display:${completedReadonly ? 'none' : (subExpanded ? 'block' : 'none')}; ${locked && !canCountThisSub && !diagnosticBucket ? 'opacity:.65;' : ''}">
                ${selectionHtml}
                ${canCountThisSub ? `
                    <p class="muted-text">Посчитайте всё вместе.</p>
                    <div class="input-group">
                        <input type="number" id="input-${sub.id}" placeholder="Общее кол-во" min="0" step="1" ${locked || sub.is_completed ? 'disabled' : ''}>
                        <button class="btn check btn-inline" onclick="verifySubcategory('${sub.id}')" ${locked || sub.is_completed ? 'disabled' : ''}>Ввод</button>
                    </div>
                    <div id="msg-${sub.id}" class="message"></div>
                ` : ''}
                ${showItemsBlock ? `
                    <div id="items-${sub.id}" class="items-container" style="display:block;">
                        ${itemsTitle}
                        ${itemsHtml || '<div class="employee-empty-state">По этому запросу товары не найдены.</div>'}
                    </div>
                ` : ''}
            </div>
        </div>
    `;
}


function renderCategoryCard(category, query, modeOverride = null) {
    const blockedClass = category.is_blocked_by_other && !category.has_my_subcategories && !category.has_my_items ? 'blocked-category' : '';
    const icon = category.is_diagnostic
        ? '🧭'
        : (category.is_completed
            ? '✅'
            : (category.assigned_to_current_user || category.has_my_subcategories || category.has_my_items ? '📂' : '📁'));

    const meta = categoryMetaText(category);
    const visibleSubcategories = getVisibleSubcategories(category, query, modeOverride);
    const queryActive = Boolean(normalizeSearch(query));
    const bodyVisible = queryActive ? true : Boolean(category.is_open);

    let bodyHtml = '';
    if (!category.is_diagnostic && category.can_take) {
        bodyHtml += `
            <div class="category-card">
                <p class="muted-text">Категория целиком выбрана администратором и пока никем не взята в работу.</p>
                <button class="btn primary btn-inline" onclick="takeCategory('${category.id}')">Взять всю категорию</button>
            </div>
        `;
    } else if (!category.is_diagnostic && !category.selected_whole_category && (category.selected_subcategory_names || []).length) {
        const selectedNames = (category.selected_subcategory_names || []).map(name => `«${escapeHtml(name)}»`).join(', ');
        bodyHtml += `
            <div class="category-card">
                <p class="muted-text">Администратор выбрал в этой категории только отдельные подкатегории. Целиком взять категорию нельзя — выберите одну из доступных подкатегорий ниже.</p>
                <div class="muted-text">Выбрано: ${selectedNames}</div>
            </div>
        `;
    }

    if (!visibleSubcategories.length) {
        bodyHtml += '<div class="employee-empty-state">По этому запросу в категории ничего не найдено.</div>';
    } else {
        bodyHtml += visibleSubcategories.map(sub => renderSubcategoryCard(category, sub, query, modeOverride)).join('');
    }

    return `
        <div class="main-category-block ${blockedClass}">
            <div class="category-header" onclick="toggleCategory('${category.id}')">
                <span>${icon} ${highlightMatch(category.name, query)}</span>
                <span class="category-meta">${escapeHtml(meta)}</span>
            </div>
            <div id="cat-body-${category.id}" class="category-body" style="display:${bodyVisible ? 'block' : 'none'}">
                ${bodyHtml}
            </div>
        </div>
    `;
}


function renderCategorySection(title, description, categories, query, modeOverride = null) {
    if (!categories.length) {
        return `${buildSectionHeader(title, description, 0)}<div class="employee-empty-state">В этом разделе ничего не найдено.</div>`;
    }
    return `${buildSectionHeader(title, description, categories.length)}${categories.map(category => renderCategoryCard(category, query, modeOverride)).join('')}`;
}

function updateFilterButtons() {
    document.querySelectorAll('[data-employee-filter]').forEach(button => {
        button.classList.toggle('active', button.dataset.employeeFilter === employeePageState.filter);
    });
}

function renderCategories() {
    const container = document.getElementById('categories-container');
    if (!container || !inventoryState) return;
    const query = employeePageState.searchQuery;
    const filtered = sortCategories(getFilteredCategories());

    if (!filtered.length) {
        container.innerHTML = '<div class="employee-empty-state">По текущим фильтрам ничего не найдено.</div>';
        renderSummary();
        updateFilterButtons();
        renderEmployeeRevisionControls();
        return;
    }

    if (employeePageState.filter === 'all') {
        const mine = filtered.filter(categoryHasPendingMineWork);
        const free = filtered.filter(categoryHasFreeWork);
        const completed = filtered.filter(categoryHasCompletedMineWork);
        const busy = filtered.filter(categoryHasBusyWork);
        container.innerHTML = [
            renderCategorySection('Мои выборы', 'Категории, подкатегории или отдельные товары, закреплённые за вами.', mine, query, 'mine'),
            renderCategorySection('Свободные категории и подкатегории', 'Их можно взять в работу. В служебных ветках выбираются отдельные товары.', free, query, 'free'),
            renderCategorySection('Завершённые подкатегории', 'То, что вы уже полностью завершили на текущий день или сохранилось в истории ревизии.', completed, query, 'completed'),
            renderCategorySection('Выборы других сотрудников', 'Эти категории, подкатегории или товары уже заняты.', busy, query),
        ].join('');
    } else {
        const descriptions = {
            mine: 'Категории и подкатегории, по которым у вас ещё осталась незавершённая работа.',
            completed: 'Подкатегории и выбранные ветки, которые вы уже полностью завершили.',
            free: 'Категории, подкатегории и товары, которые можно взять в работу.',
            busy: 'Категории, подкатегории или товары, закреплённые за другими сотрудниками.',
            problem: 'Категории, в которых уже есть расхождения.',
        };
        const titles = {
            mine: 'Мои выборы',
            completed: 'Завершённые подкатегории',
            free: 'Свободные категории и подкатегории',
            busy: 'Занятые категории и подкатегории',
            problem: 'Категории с расхождениями',
        };
        container.innerHTML = renderCategorySection(titles[employeePageState.filter] || 'Категории', descriptions[employeePageState.filter] || 'Результат текущего фильтра.', filtered, query);
    }

    renderSummary();
    updateFilterButtons();
    renderEmployeeRevisionControls();
}

window.toggleCategory = function (categoryId) {
    const category = getCategory(categoryId);
    if (!category) return;
    category.is_open = !category.is_open;
    if (category.is_open) {
        employeeUiState.openCategories.add(categoryId);
    } else {
        employeeUiState.openCategories.delete(categoryId);
    }
    renderCategories();
};

window.toggleSubcategory = function (subId) {
    const found = findSubcategory(subId);
    if (!found) return;
    found.sub.is_expanded = !found.sub.is_expanded;
    if (found.sub.is_expanded) {
        employeeUiState.openSubcategories.add(subId);
    } else {
        employeeUiState.openSubcategories.delete(subId);
    }
    renderCategories();
};

window.takeCategory = async function (categoryId) {
    if (!employeeRevisionIsActive()) {
        alert('Сначала нажмите «Начать ревизию».');
        return;
    }
    const category = getCategory(categoryId);
    if (!window.confirm(buildSelectionConfirmMessage('category', category?.name))) return;
    if (!beginSelectionRequest('category', categoryId)) return;
    try {
        const response = await fetch('/assign-selection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: inventoryState.report_id, category_id: categoryId, target_type: 'category', subcategory_id: null }),
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            alert(result.detail || result.message || 'Не удалось взять категорию.');
            return;
        }
        await loadInventory({ forceReload: true });
    } catch (error) {
        console.error(error);
        alert(describeSelectionTransportError('категории', error));
    } finally {
        finishSelectionRequest('category', categoryId);
    }
};

window.takeSubcategory = async function (categoryId, subcategoryId) {
    if (!employeeRevisionIsActive()) {
        alert('Сначала нажмите «Начать ревизию».');
        return;
    }
    const found = findSubcategory(subcategoryId);
    if (!window.confirm(buildSelectionConfirmMessage('subcategory', found?.sub?.name))) return;
    if (!beginSelectionRequest('subcategory', categoryId, subcategoryId)) return;
    try {
        const response = await fetch('/assign-selection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: inventoryState.report_id, category_id: categoryId, target_type: 'subcategory', subcategory_id: subcategoryId }),
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            alert(result.detail || result.message || 'Не удалось взять подкатегорию.');
            return;
        }
        await loadInventory({ forceReload: true });
    } catch (error) {
        console.error(error);
        alert(describeSelectionTransportError('подкатегории', error));
    } finally {
        finishSelectionRequest('subcategory', categoryId, subcategoryId);
    }
};



window.takeItem = async function (categoryId, subcategoryId, itemId) {
    if (!employeeRevisionIsActive()) {
        alert('Сначала нажмите «Начать ревизию».');
        return;
    }
    const found = findSubcategory(subcategoryId);
    const item = found?.sub?.items?.find(row => row.id === itemId);
    if (!window.confirm(buildSelectionConfirmMessage('item', item?.name))) return;
    if (!beginSelectionRequest('item', categoryId, subcategoryId, itemId)) return;
    try {
        const response = await fetch('/assign-selection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: inventoryState.report_id, category_id: categoryId, target_type: 'item', subcategory_id: subcategoryId, item_id: itemId }),
        });
        const result = await response.json();
        if (!response.ok || !result.success) {
            alert(result.detail || result.message || 'Не удалось взять товар.');
            return;
        }
        await loadInventory({ forceReload: true });
    } catch (error) {
        console.error(error);
        alert(describeSelectionTransportError('товара', error));
    } finally {
        finishSelectionRequest('item', categoryId, subcategoryId, itemId);
    }
};



function setVerificationBusyState(targetId, isBusy) {
    const inputElement = document.getElementById(`input-${targetId}`);
    const actionButton = inputElement?.closest('.input-group')?.querySelector('button');
    if (inputElement) {
        inputElement.disabled = Boolean(isBusy);
    }
    if (actionButton) {
        actionButton.disabled = Boolean(isBusy);
        actionButton.dataset.originalText = actionButton.dataset.originalText || actionButton.textContent;
        actionButton.textContent = isBusy ? '...' : actionButton.dataset.originalText;
    }
}

function normalizeServerError(result, fallbackMessage) {
    return result?.detail || result?.message || fallbackMessage;
}

function extractRequestId(response) {
    return response?.headers?.get?.('X-Request-ID') || null;
}

function appendRequestId(message, requestId) {
    if (!requestId) return message;
    return `${message} Код запроса: ${requestId}.`;
}

function describeVerifyHttpError(status, result, requestId) {
    const serverMessage = normalizeServerError(result, '');
    if (status === 401) {
        return appendRequestId('Сессия истекла. Войдите заново.', requestId);
    }
    if (status === 403 || status === 409) {
        return appendRequestId(serverMessage || 'Данные ревизии уже изменились. Обновляем список и попробуйте ещё раз.', requestId);
    }
    if (status === 422) {
        return appendRequestId(serverMessage || 'Не удалось обработать введённое количество. Проверьте число и повторите.', requestId);
    }
    if (status === 429) {
        return appendRequestId('Слишком много запросов подряд. Подождите несколько секунд и повторите.', requestId);
    }
    if (status >= 500) {
        return appendRequestId('Сервер временно недоступен. Повторите через несколько секунд.', requestId);
    }
    return appendRequestId(serverMessage || 'Не удалось сохранить результат проверки.', requestId);
}

function describeVerifyTransportError(error) {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        return 'Похоже, пропало интернет-соединение. Проверьте сеть и повторите.';
    }
    const message = String(error?.message || '').toLowerCase();
    if (message.includes('timeout') || message.includes('timed out') || message.includes('abort')) {
        return 'Сервер отвечает слишком долго. Подождите пару секунд и попробуйте снова.';
    }
    return 'Нет ответа от сервера. Обновите страницу и попробуйте ещё раз.';
}

function describeInventoryHttpError(status, requestId) {
    if (status === 401) {
        return 'Сессия истекла. Перенаправляем на вход.';
    }
    if (status === 403) {
        return appendRequestId('У вас нет доступа к этой ревизии.', requestId);
    }
    if (status === 429) {
        return 'Сервер занят. Подождите несколько секунд и повторите загрузку.';
    }
    if (status >= 500) {
        return appendRequestId('Сервер временно недоступен. Попробуйте ещё раз через несколько секунд.', requestId);
    }
    return appendRequestId('Не удалось получить данные ревизии. Попробуйте ещё раз.', requestId);
}

function describeInventoryTransportError(error) {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
        return 'Похоже, пропало интернет-соединение. Проверьте сеть и повторите загрузку.';
    }
    const message = String(error?.message || '').toLowerCase();
    if (message.includes('timeout') || message.includes('timed out') || message.includes('abort')) {
        return 'Загрузка ревизии заняла слишком много времени. Попробуйте ещё раз.';
    }
    return 'Сервер не ответил или соединение прервалось. Повторите загрузку.';
}

function describeSelectionTransportError(targetLabel, error) {
    const base = describeVerifyTransportError(error);
    return `${base} Закрепление ${targetLabel} не выполнено.`;
}

async function parseJsonSafe(response) {
    try {
        return await response.json();
    } catch {
        return null;
    }
}

window.verifySubcategory = async function (subId) {
    if (!employeeRevisionIsActive()) {
        alert('Сначала нажмите «Начать ревизию».');
        return;
    }
    if (inventoryLoading || pendingVerificationTargets.has(subId)) return;

    const found = findSubcategory(subId);
    if (!found) return;
    const inputElement = document.getElementById(`input-${subId}`);
    const msgElement = document.getElementById(`msg-${subId}`);
    if (!inputElement || !msgElement) return;

    const inputValue = parseFloat(inputElement.value);
    if (Number.isNaN(inputValue)) return;
    subcategoryAttempts[subId] = (subcategoryAttempts[subId] || 0) + 1;
    pendingVerificationTargets.add(subId);
    setVerificationBusyState(subId, true);

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: inventoryState.report_id, target_id: subId, is_category: true, quantity: inputValue, attempt_number: subcategoryAttempts[subId] }),
        });
        const result = await parseJsonSafe(response);
        if (!response.ok) {
            const requestId = extractRequestId(response);
            console.error('POST /verify failed', { status: response.status, requestId, result, targetId: subId });
            msgElement.textContent = describeVerifyHttpError(response.status, result, requestId);
            msgElement.style.color = 'red';
            if ([401, 403, 409].includes(response.status)) {
                await loadInventory({ forceReload: true });
            }
            return;
        }

        msgElement.textContent = result.message;
        msgElement.style.color = result.is_correct ? 'green' : 'red';
        if (result.expand_category || result.is_correct) {
            await loadInventory();
        } else {
            inputElement.value = '';
        }
    } catch (error) {
        console.error(error);
        msgElement.textContent = describeVerifyTransportError(error);
        msgElement.style.color = 'red';
    } finally {
        pendingVerificationTargets.delete(subId);
        if (!inventoryLoading) {
            setVerificationBusyState(subId, false);
        }
    }
};

window.verifyItem = async function (itemId) {
    if (!employeeRevisionIsActive()) {
        alert('Сначала нажмите «Начать ревизию».');
        return;
    }
    if (inventoryLoading || pendingVerificationTargets.has(itemId)) return;

    const inputElement = document.getElementById(`input-${itemId}`);
    const msgElement = document.getElementById(`msg-${itemId}`);
    if (!inputElement || !msgElement) return;

    const inputValue = parseFloat(inputElement.value);
    if (Number.isNaN(inputValue)) return;
    itemAttempts[itemId] = (itemAttempts[itemId] || 0) + 1;
    pendingVerificationTargets.add(itemId);
    setVerificationBusyState(itemId, true);
    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: inventoryState.report_id, target_id: itemId, is_category: false, quantity: inputValue, attempt_number: itemAttempts[itemId] }),
        });
        const result = await parseJsonSafe(response);
        if (!response.ok) {
            const requestId = extractRequestId(response);
            console.error('POST /verify failed', { status: response.status, requestId, result, targetId: itemId });
            msgElement.textContent = describeVerifyHttpError(response.status, result, requestId);
            msgElement.style.color = 'red';
            if ([401, 403, 409].includes(response.status)) {
                await loadInventory({ forceReload: true });
            }
            return;
        }

        msgElement.textContent = result.message;
        msgElement.style.color = result.is_correct ? 'green' : 'red';
        if (result.is_correct || result.attempts_left === 0) {
            itemAttempts[itemId] = 0;
            await loadInventory();
        } else {
            inputElement.value = '';
        }
    } catch (error) {
        console.error(error);
        msgElement.textContent = describeVerifyTransportError(error);
        msgElement.style.color = 'red';
    } finally {
        pendingVerificationTargets.delete(itemId);
        if (!inventoryLoading) {
            setVerificationBusyState(itemId, false);
        }
    }
};


async function loadInventory(options = {}) {
    const { forceReload = false } = options;
    if (inventoryLoading) {
        inventoryReloadQueued = true;
        return;
    }

    inventoryReloadQueued = false;
    captureEmployeeUiState();

    const summary = document.getElementById('inventory-summary');
    const container = document.getElementById('categories-container');
    inventoryLoading = true;
    setRefreshButtonState(true);
    setInventoryStatus('loading', forceReload ? 'Пожалуйста, подождите: обновляем категории и остатки из CRM.' : 'Пожалуйста, подождите: идёт выгрузка категорий и остатков из CRM (МойСклад).');
    if (container && !inventoryState) {
        container.innerHTML = '<div class="employee-empty-state">Пожалуйста, подождите: идёт выгрузка из CRM и построение дерева категорий.</div>';
    }

    try {
        const response = await fetch('/get-structure');
        if (response.status === 401) {
            location.href = '/login';
            return;
        }
        if (!response.ok) {
            const text = await response.text();
            const requestId = extractRequestId(response);
            console.error('GET /get-structure failed:', response.status, text, { requestId });
            if (summary) summary.innerHTML = '<span style="color:#dc3545;">Ошибка загрузки ревизии.</span>';
            if (container) container.innerHTML = `<div class="employee-empty-state">${escapeHtml(describeInventoryHttpError(response.status, requestId))}</div>`;
            setInventoryStatus('error', describeInventoryHttpError(response.status, requestId), { retry: true });
            return;
        }
        inventoryState = await response.json();
        ensureRevisionStateForCurrentDay();
        applyEmployeeUiState();
        document.getElementById('current-location-title').textContent = `Точка: ${inventoryState.location}`;
        renderCategories();
        setInventoryStatus(null);
    } catch (error) {
        console.error('loadInventory error:', error);
        const friendlyMessage = describeInventoryTransportError(error);
        if (summary) summary.innerHTML = '<span style="color:#dc3545;">Ошибка загрузки ревизии.</span>';
        if (container) container.innerHTML = `<div class="employee-empty-state">${escapeHtml(friendlyMessage)}</div>`;
        setInventoryStatus('error', friendlyMessage, { retry: true });
    } finally {
        inventoryLoading = false;
        setRefreshButtonState(false);
    }

    if (inventoryReloadQueued) {
        inventoryReloadQueued = false;
        await loadInventory({ forceReload: true });
    }
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    location.href = '/login';
}

document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('logout-btn')?.addEventListener('click', logout);
    document.getElementById('refresh-btn')?.addEventListener('click', loadInventory);
    document.getElementById('start-revision-btn')?.addEventListener('click', async () => {
        if (!inventoryState?.report_id || inventoryState?.employee_finished || inventoryState?.report_completed) return;
        try {
            const response = await fetch('/start-report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ report_id: inventoryState.report_id }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) {
                throw new Error(data.detail || data.message || 'Не удалось начать ревизию.');
            }
            inventoryState.employee_started = true;
            inventoryState.report_started = true;
            setEmployeeRevisionState('started');
            renderEmployeeRevisionControls();
            await loadInventory({ forceReload: true });
        } catch (error) {
            alert(error.message || 'Не удалось начать ревизию.');
        }
    });
    document.getElementById('finish-revision-btn')?.addEventListener('click', async () => {
        if (!inventoryState?.report_id) return;
        if (!window.confirm('Завершить ревизию на текущий день? После подтверждения продолжить работу уже будет нельзя.')) return;
        try {
            const response = await fetch('/finish-report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ report_id: inventoryState.report_id }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.success === false) {
                throw new Error(data.detail || data.message || 'Не удалось завершить ревизию.');
            }
            alert(data.message || 'Ревизия завершена.');
            setEmployeeRevisionState('finished');
            await loadInventory({ forceReload: true });
        } catch (error) {
            alert(error.message || 'Не удалось завершить ревизию.');
        }
    });
    document.querySelectorAll('[data-employee-filter]').forEach(button => {
        button.addEventListener('click', () => {
            employeePageState.filter = button.dataset.employeeFilter || 'all';
            renderCategories();
        });
    });
    document.getElementById('employee-search-input')?.addEventListener('input', (event) => {
        employeePageState.searchQuery = event.target.value.trim();
        renderCategories();
    });
    document.getElementById('categories-container')?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        const input = event.target;
        if (!(input instanceof HTMLInputElement) || !input.id.startsWith('input-')) return;
        event.preventDefault();
        const targetId = input.id.slice('input-'.length);
        const found = findSubcategory(targetId);
        if (found) {
            verifySubcategory(targetId);
            return;
        }
        verifyItem(targetId);
    });
    await loadInventory();
});
