let inventoryState = null;
let subcategoryAttempts = {};
let itemAttempts = {};
let inventoryLoading = false;

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
    const button = document.getElementById('finish-btn');
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

function getCategoryBucket(category) {
    if (category.assigned_to_current_user || category.has_my_subcategories || category.has_my_items) return 'mine';
    if (category.can_take || category.subcategories.some(sub => sub.can_take) || categoryHasFreeDiagnosticItems(category)) return 'free';
    if (category.is_blocked_by_other || category.has_other_subcategories || category.has_other_items) return 'busy';
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
    return (category.subcategories || []).some(subcategory => subcategoryHasPendingMineWork(category, subcategory));
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
        if (employeeUiState.openCategories.has(category.id) || categoryHasPendingMineWork(category)) {
            category.is_open = true;
        }

        for (const sub of category.subcategories || []) {
            const diagnosticBucket = isDiagnosticSubcategory(category, sub) || sub.is_diagnostic;
            const hasPendingMineItems = (sub.items || []).some(item => item.assigned_to_current_user && !item.is_final);
            if (employeeUiState.openSubcategories.has(sub.id) || sub.status === 'orange' || (diagnosticBucket && hasPendingMineItems)) {
                sub.is_expanded = true;
            }
        }
    }
}

function getVisibleSubcategories(category, query) {
    const q = normalizeSearch(query);
    const allSubcategories = category.subcategories || [];
    const scopedSubcategories = employeePageState.filter === 'mine'
        ? allSubcategories.filter(subcategory => subcategoryBelongsToCurrentUser(category, subcategory))
        : allSubcategories;

    if (!q) return scopedSubcategories;

    const categoryDirectMatch = normalizeSearch(category.name).includes(q) || normalizeSearch(category.assigned_to || '').includes(q);
    if (categoryDirectMatch) return scopedSubcategories;
    return scopedSubcategories.filter(subcategory => subcategoryMatchesSearch(subcategory, q));
}

function categoryPassesFilter(category) {
    const mode = employeePageState.filter;
    if (mode === 'mine') return category.assigned_to_current_user || category.has_my_subcategories || category.has_my_items;
    if (mode === 'free') return category.can_take || category.subcategories.some(sub => sub.can_take) || categoryHasFreeDiagnosticItems(category);
    if (mode === 'busy') return category.is_blocked_by_other || category.has_other_subcategories || category.has_other_items;
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
    const myCategories = allCategories.filter(cat => cat.assigned_to_current_user || cat.has_my_subcategories || cat.has_my_items);
    const freeCategories = allCategories.filter(cat => cat.can_take || cat.subcategories.some(sub => sub.can_take) || categoryHasFreeDiagnosticItems(cat));
    const occupiedCategories = allCategories.filter(cat => cat.is_blocked_by_other || cat.has_other_subcategories || cat.has_other_items);
    const problemCategories = allCategories.filter(categoryHasProblems);

    if (dateLine) dateLine.textContent = `Общая ревизия за ${inventoryState.report_date}`;
    if (cycleLine) cycleLine.textContent = `Текущий цикл выбора: с ${inventoryState.cycle_started_at}. Осталось дней: ${inventoryState.cycle_days_left}.`;

    document.getElementById('stat-my').textContent = String(myCategories.length);
    document.getElementById('stat-free').textContent = String(freeCategories.length);
    document.getElementById('stat-busy').textContent = String(occupiedCategories.length);
    document.getElementById('stat-problem').textContent = String(problemCategories.length);

    if (summary) {
        summary.innerHTML = `
            <strong>Мои выборы:</strong> ${myCategories.length}.
            <strong>Свободные:</strong> ${freeCategories.length}.
            <strong>У других сотрудников:</strong> ${occupiedCategories.length}.
            <strong>С расхождениями:</strong> ${problemCategories.length}.
        `;
    }
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

function buildSelectionConfirmMessage(kind, label) {
    const safeLabel = label || 'эту позицию';
    const entityLabel = kind === 'category' ? 'категорию' : kind === 'subcategory' ? 'подкатегорию' : 'товар';
    return `Подтвердите выбор: ${entityLabel} «${safeLabel}».

После закрепления отменить действие нельзя до начала нового 15-дневного цикла.`;
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
    if (category.is_blocked_by_other) return `категория занята: ${category.assigned_to}`;
    if (category.has_other_subcategories || category.has_other_items) return 'часть ветки занята другими';
    return 'свободна';
}


function renderSubcategoryCard(category, sub, query) {
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
    if (diagnosticBucket) {
        selectionHtml = '<div class="muted-text diagnostic-help">Эта служебная ветка не считает общий итог. Здесь можно закреплять только отдельные товары.</div>';
    } else if (sub.can_take) {
        selectionHtml = `<div class="subcategory-action-row"><button class="btn secondary btn-inline" onclick="takeSubcategory('${category.id}', '${sub.id}')">Взять подкатегорию</button></div>`;
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
            itemMessage = 'Товар ещё не выбран на текущий 15-дневный цикл.';
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

    const canCountThisSub = !diagnosticBucket && (sub.assigned_to_current_user || sub.taken_as_part_of_category);
    const showItemsBlock = diagnosticBucket || sub.status === 'orange';
    const itemsTitle = diagnosticBucket
        ? '<p class="items-warning diagnostic-warning">⚠️ Служебная ветка. Общий ввод отключён: выбирайте и проверяйте только отдельные товары.</p>'
        : '<p class="items-warning">⚠️ Не сошлось. Считаем поштучно:</p>';

    return `
        <div class="category-card subcategory-card status-${sub.status}" id="card-${sub.id}">
            <h3 id="title-${sub.id}" onclick="toggleSubcategory('${sub.id}')">${icon} ${highlightMatch(sub.name, query)}</h3>
            <div id="body-${sub.id}" style="display:${subExpanded ? 'block' : 'none'}; ${locked && !canCountThisSub && !diagnosticBucket ? 'opacity:.65;' : ''}">
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


function renderCategoryCard(category, query) {
    const blockedClass = category.is_blocked_by_other && !category.has_my_subcategories && !category.has_my_items ? 'blocked-category' : '';
    const icon = category.is_diagnostic
        ? '🧭'
        : (category.is_completed
            ? '✅'
            : (category.assigned_to_current_user || category.has_my_subcategories || category.has_my_items ? '📂' : '📁'));

    const meta = categoryMetaText(category);
    const visibleSubcategories = getVisibleSubcategories(category, query);
    const queryActive = Boolean(normalizeSearch(query));
    const bodyVisible = queryActive ? true : (Boolean(category.is_open) || categoryHasPendingMineWork(category));

    let bodyHtml = '';
    if (!category.is_diagnostic && category.can_take) {
        bodyHtml += `
            <div class="category-card">
                <p class="muted-text">Категория пока никем не взята в работу.</p>
                <button class="btn primary btn-inline" onclick="takeCategory('${category.id}')">Взять всю категорию</button>
            </div>
        `;
    }

    if (!visibleSubcategories.length) {
        bodyHtml += '<div class="employee-empty-state">По этому запросу в категории ничего не найдено.</div>';
    } else {
        bodyHtml += visibleSubcategories.map(sub => renderSubcategoryCard(category, sub, query)).join('');
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


function renderCategorySection(title, description, categories, query) {
    if (!categories.length) {
        return `${buildSectionHeader(title, description, 0)}<div class="employee-empty-state">В этом разделе ничего не найдено.</div>`;
    }
    return `${buildSectionHeader(title, description, categories.length)}${categories.map(category => renderCategoryCard(category, query)).join('')}`;
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
        return;
    }

    if (employeePageState.filter === 'all') {
        const mine = filtered.filter(cat => getCategoryBucket(cat) === 'mine');
        const free = filtered.filter(cat => getCategoryBucket(cat) === 'free');
        const busy = filtered.filter(cat => getCategoryBucket(cat) === 'busy');
        container.innerHTML = [
            renderCategorySection('Мои выборы', 'Категории, подкатегории или отдельные товары, закреплённые за вами.', mine, query),
            renderCategorySection('Свободные категории и подкатегории', 'Их можно взять в работу. В служебных ветках выбираются отдельные товары.', free, query),
            renderCategorySection('Выборы других сотрудников', 'Эти категории, подкатегории или товары уже заняты.', busy, query),
        ].join('');
    } else {
        const descriptions = {
            mine: 'Категории, подкатегории или отдельные товары, закреплённые за вами.',
            free: 'Категории, подкатегории и товары, которые можно взять в работу.',
            busy: 'Категории, подкатегории или товары, закреплённые за другими сотрудниками.',
            problem: 'Категории, в которых уже есть расхождения.',
        };
        const titles = {
            mine: 'Мои выборы',
            free: 'Свободные категории и подкатегории',
            busy: 'Занятые категории и подкатегории',
            problem: 'Категории с расхождениями',
        };
        container.innerHTML = renderCategorySection(titles[employeePageState.filter] || 'Категории', descriptions[employeePageState.filter] || 'Результат текущего фильтра.', filtered, query);
    }

    renderSummary();
    updateFilterButtons();
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
    const category = getCategory(categoryId);
    if (!window.confirm(buildSelectionConfirmMessage('category', category?.name))) return;
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
        await loadInventory();
    } catch (error) {
        console.error(error);
        alert('Ошибка сервера при закреплении категории.');
    }
};

window.takeSubcategory = async function (categoryId, subcategoryId) {
    const found = findSubcategory(subcategoryId);
    if (!window.confirm(buildSelectionConfirmMessage('subcategory', found?.sub?.name))) return;
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
        await loadInventory();
    } catch (error) {
        console.error(error);
        alert('Ошибка сервера при закреплении подкатегории.');
    }
};



window.takeItem = async function (categoryId, subcategoryId, itemId) {
    const found = findSubcategory(subcategoryId);
    const item = found?.sub?.items?.find(row => row.id === itemId);
    if (!window.confirm(buildSelectionConfirmMessage('item', item?.name))) return;
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
        await loadInventory();
    } catch (error) {
        console.error(error);
        alert('Ошибка сервера при закреплении товара.');
    }
};


window.verifySubcategory = async function (subId) {
    const found = findSubcategory(subId);
    if (!found) return;
    const inputElement = document.getElementById(`input-${subId}`);
    const msgElement = document.getElementById(`msg-${subId}`);
    const inputValue = parseFloat(inputElement.value);
    if (Number.isNaN(inputValue)) return;
    subcategoryAttempts[subId] = (subcategoryAttempts[subId] || 0) + 1;

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: inventoryState.report_id, target_id: subId, is_category: true, quantity: inputValue, attempt_number: subcategoryAttempts[subId] }),
        });
        const result = await response.json();
        msgElement.textContent = result.message;
        msgElement.style.color = result.is_correct ? 'green' : 'red';
        if (result.expand_category || result.is_correct) {
            await loadInventory();
        } else {
            inputElement.value = '';
        }
    } catch (error) {
        console.error(error);
        msgElement.textContent = 'Ошибка сервера';
    }
};

window.verifyItem = async function (itemId) {
    const inputElement = document.getElementById(`input-${itemId}`);
    const msgElement = document.getElementById(`msg-${itemId}`);
    const inputValue = parseFloat(inputElement.value);
    if (Number.isNaN(inputValue)) return;
    itemAttempts[itemId] = (itemAttempts[itemId] || 0) + 1;
    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: inventoryState.report_id, target_id: itemId, is_category: false, quantity: inputValue, attempt_number: itemAttempts[itemId] }),
        });
        const result = await response.json();
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
        msgElement.textContent = 'Ошибка сервера';
    }
};


async function loadInventory(options = {}) {
    if (inventoryLoading) return;

    captureEmployeeUiState();

    const { forceReload = false } = options;
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
            console.error('GET /get-structure failed:', response.status, text);
            if (summary) summary.innerHTML = '<span style="color:#dc3545;">Ошибка загрузки ревизии.</span>';
            if (container) container.innerHTML = '<div class="employee-empty-state">Не удалось загрузить данные с сервера.</div>';
            setInventoryStatus('error', 'Не удалось получить данные ревизии. Попробуйте ещё раз.', { retry: true });
            return;
        }
        inventoryState = await response.json();
        applyEmployeeUiState();
        document.getElementById('current-location-title').textContent = `Точка: ${inventoryState.location}`;
        renderCategories();
        setInventoryStatus(null);
    } catch (error) {
        console.error('loadInventory error:', error);
        if (summary) summary.innerHTML = '<span style="color:#dc3545;">Ошибка загрузки ревизии.</span>';
        if (container) container.innerHTML = '<div class="employee-empty-state">Ошибка соединения с сервером.</div>';
        setInventoryStatus('error', 'Сервер не ответил или соединение прервалось. Повторите загрузку.', { retry: true });
    } finally {
        inventoryLoading = false;
        setRefreshButtonState(false);
    }
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    location.href = '/login';
}

document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('logout-btn')?.addEventListener('click', logout);
    document.getElementById('finish-btn')?.addEventListener('click', loadInventory);
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
    await loadInventory();
});
