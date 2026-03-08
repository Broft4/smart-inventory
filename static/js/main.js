let inventoryState = null;
let subcategoryAttempts = {};
let itemAttempts = {};

const employeePageState = {
    filter: 'all',
    searchQuery: '',
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
    if (category.assigned_to_current_user || category.has_my_subcategories) return 'mine';
    if (category.can_take || category.subcategories.some(sub => sub.can_take)) return 'free';
    if (category.is_blocked_by_other || category.has_other_subcategories) return 'busy';
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

function getVisibleSubcategories(category, query) {
    const q = normalizeSearch(query);
    if (!q) return category.subcategories || [];
    const categoryDirectMatch = normalizeSearch(category.name).includes(q) || normalizeSearch(category.assigned_to || '').includes(q);
    if (categoryDirectMatch) return category.subcategories || [];
    return (category.subcategories || []).filter(subcategory => subcategoryMatchesSearch(subcategory, q));
}

function categoryPassesFilter(category) {
    const mode = employeePageState.filter;
    if (mode === 'mine') return category.assigned_to_current_user || category.has_my_subcategories;
    if (mode === 'free') return category.can_take || category.subcategories.some(sub => sub.can_take);
    if (mode === 'busy') return category.is_blocked_by_other || category.has_other_subcategories;
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
    const myCategories = allCategories.filter(cat => cat.assigned_to_current_user || cat.has_my_subcategories);
    const freeCategories = allCategories.filter(cat => cat.can_take || cat.subcategories.some(sub => sub.can_take));
    const occupiedCategories = allCategories.filter(cat => cat.is_blocked_by_other || cat.has_other_subcategories);
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

function categoryMetaText(category) {
    if (category.is_completed && (category.assigned_to_current_user || category.has_my_subcategories)) return 'ваши выборы завершены';
    if (category.assigned_to_current_user) return 'вся категория закреплена за вами';
    if (category.has_my_subcategories && category.has_other_subcategories) return 'подкатегории распределены между сотрудниками';
    if (category.has_my_subcategories) return 'у вас есть закреплённые подкатегории';
    if (category.is_blocked_by_other) return `категория занята: ${category.assigned_to}`;
    if (category.has_other_subcategories) return 'часть подкатегорий занята другими';
    return 'свободна';
}

function renderSubcategoryCard(category, sub, query) {
    const locked = sub.is_locked;
    let icon = '📂';
    if (sub.is_completed) icon = '✅';
    if (locked && !sub.assigned_to_current_user && !sub.taken_as_part_of_category) icon = '🔒';

    const queryActive = Boolean(normalizeSearch(query));
    const subExpanded = queryActive ? true : sub.is_expanded;
    const visibleItems = (() => {
        if (!queryActive) return sub.items || [];
        const q = normalizeSearch(query);
        const subDirectMatch = normalizeSearch(sub.name).includes(q);
        if (subDirectMatch) return sub.items || [];
        return (sub.items || []).filter(item => itemMatchesSearch(item, q));
    })();

    const selectionHtml = sub.can_take
        ? `<div class="subcategory-action-row"><button class="btn secondary btn-inline" onclick="takeSubcategory('${category.id}', '${sub.id}')">Взять подкатегорию</button></div>`
        : sub.is_blocked_by_other && !sub.assigned_to_current_user && !sub.taken_as_part_of_category
            ? `<div class="muted-text">Подкатегория занята: <strong>${escapeHtml(sub.assigned_to || 'другой сотрудник')}</strong>.</div>`
            : sub.assigned_to_current_user
                ? `<div class="muted-text">Подкатегория закреплена за вами.</div>`
                : sub.taken_as_part_of_category
                    ? `<div class="muted-text">Доступна вам в составе выбранной категории.</div>`
                    : '';

    const itemsHtml = visibleItems.map(item => {
        const itemDisabled = sub.status !== 'orange' || item.is_final;
        let itemMessage = '';
        let itemMessageColor = '';
        if (item.status === 'green') {
            itemMessage = 'Товар подтверждён.';
            itemMessageColor = 'green';
        } else if (item.status === 'red') {
            itemMessage = 'Расхождение по товару зафиксировано.';
            itemMessageColor = 'red';
        }
        return `
            <div class="item-card">
                <h4>${highlightMatch(item.name, query)} (${escapeHtml(item.uom)})</h4>
                <div class="input-group">
                    <input type="number" id="input-${item.id}" placeholder="Факт. шт." min="0" step="1" ${itemDisabled ? 'disabled' : ''}>
                    <button class="btn check btn-inline" onclick="verifyItem('${item.id}', '${sub.id}')" ${itemDisabled ? 'disabled' : ''}>Ввод</button>
                </div>
                <div id="msg-${item.id}" class="message" style="color:${itemMessageColor};">${itemMessage}</div>
            </div>
        `;
    }).join('');

    const canCountThisSub = sub.assigned_to_current_user || sub.taken_as_part_of_category;

    return `
        <div class="category-card subcategory-card status-${sub.status}" id="card-${sub.id}">
            <h3 id="title-${sub.id}" onclick="toggleSubcategory('${sub.id}')">${icon} ${highlightMatch(sub.name, query)}</h3>
            <div id="body-${sub.id}" style="display:${subExpanded ? 'block' : 'none'}; ${locked && !canCountThisSub ? 'opacity:.65;' : ''}">
                ${selectionHtml}
                ${canCountThisSub ? `
                    <p class="muted-text">Посчитайте всё вместе.</p>
                    <div class="input-group">
                        <input type="number" id="input-${sub.id}" placeholder="Общее кол-во" min="0" step="1" ${locked || sub.is_completed ? 'disabled' : ''}>
                        <button class="btn check btn-inline" onclick="verifySubcategory('${sub.id}')" ${locked || sub.is_completed ? 'disabled' : ''}>Ввод</button>
                    </div>
                    <div id="msg-${sub.id}" class="message"></div>
                    <div id="items-${sub.id}" class="items-container" style="display:${sub.status === 'orange' ? 'block' : 'none'};">
                        <p class="items-warning">⚠️ Не сошлось. Считаем поштучно:</p>
                        ${itemsHtml || '<div class="employee-empty-state">По этому запросу товары не найдены.</div>'}
                    </div>
                ` : ''}
            </div>
        </div>
    `;
}

function renderCategoryCard(category, query) {
    const blockedClass = category.is_blocked_by_other && !category.has_my_subcategories ? 'blocked-category' : '';
    const icon = category.is_completed ? '✅' : (category.assigned_to_current_user || category.has_my_subcategories ? '📂' : '📁');
    const meta = categoryMetaText(category);
    const visibleSubcategories = getVisibleSubcategories(category, query);
    const queryActive = Boolean(normalizeSearch(query));
    const bodyVisible = queryActive ? true : category.is_open;

    let bodyHtml = '';
    if (category.can_take) {
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
            renderCategorySection('Мои выборы', 'Категории или подкатегории, закреплённые за вами.', mine, query),
            renderCategorySection('Свободные категории и подкатегории', 'Их можно взять в работу.', free, query),
            renderCategorySection('Выборы других сотрудников', 'Эти категории или подкатегории уже заняты.', busy, query),
        ].join('');
    } else {
        const descriptions = {
            mine: 'Категории или подкатегории, закреплённые за вами.',
            free: 'Категории и подкатегории, которые можно взять в работу.',
            busy: 'Категории и подкатегории, закреплённые за другими сотрудниками.',
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
    renderCategories();
};

window.toggleSubcategory = function (subId) {
    const found = findSubcategory(subId);
    if (!found) return;
    found.sub.is_expanded = !found.sub.is_expanded;
    renderCategories();
};

window.takeCategory = async function (categoryId) {
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

async function simulate15Days() {
    if (!confirm('Сбросить текущий 15-дневный выбор для вашей точки?')) return;
    try {
        const response = await fetch('/simulate-15-days', { method: 'POST' });
        const data = await response.json();
        if (!response.ok || !data.success) {
            alert(data.detail || data.message || 'Не удалось обновить цикл.');
            return;
        }
        alert(data.message);
        await loadInventory();
    } catch (error) {
        console.error(error);
        alert('Ошибка сервера при обновлении цикла.');
    }
}

async function loadInventory() {
    const summary = document.getElementById('inventory-summary');
    const container = document.getElementById('categories-container');
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
            return;
        }
        inventoryState = await response.json();
        document.getElementById('current-location-title').textContent = `Точка: ${inventoryState.location}`;
        renderCategories();
    } catch (error) {
        console.error('loadInventory error:', error);
        if (summary) summary.innerHTML = '<span style="color:#dc3545;">Ошибка загрузки ревизии.</span>';
        if (container) container.innerHTML = '<div class="employee-empty-state">Ошибка соединения с сервером.</div>';
    }
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    location.href = '/login';
}

document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('logout-btn')?.addEventListener('click', logout);
    document.getElementById('finish-btn')?.addEventListener('click', loadInventory);
    document.getElementById('simulate-15-days-btn')?.addEventListener('click', simulate15Days);
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
