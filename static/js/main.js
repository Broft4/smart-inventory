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

function findItem(itemId) {
    for (const category of inventoryState.categories) {
        for (const sub of category.subcategories) {
            for (const item of sub.items) {
                if (item.id === itemId) return { category, sub, item };
            }
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
    if (category.assigned_to_current_user) return 'mine';
    if (category.can_take) return 'free';
    if (category.is_blocked_by_other) return 'busy';
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

    if (normalizeSearch(subcategory.name).includes(q)) {
        return true;
    }

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

    const categoryDirectMatch =
        normalizeSearch(category.name).includes(q) ||
        normalizeSearch(category.assigned_to || '').includes(q);

    if (categoryDirectMatch) {
        return category.subcategories || [];
    }

    return (category.subcategories || []).filter(subcategory => subcategoryMatchesSearch(subcategory, q));
}

function categoryPassesFilter(category) {
    const mode = employeePageState.filter;

    if (mode === 'mine') return category.assigned_to_current_user;
    if (mode === 'free') return category.can_take;
    if (mode === 'busy') return category.is_blocked_by_other;
    if (mode === 'problem') return categoryHasProblems(category);

    return true;
}

function getFilteredCategories() {
    if (!inventoryState?.categories) return [];

    return inventoryState.categories.filter(category => {
        return categoryPassesFilter(category) && categoryMatchesSearch(category, employeePageState.searchQuery);
    });
}

function sortCategories(categories) {
    return [...categories].sort((a, b) => a.name.localeCompare(b.name, 'ru'));
}

function renderSummary() {
    const summary = document.getElementById('inventory-summary');
    const dateLine = document.getElementById('report-date-line');

    const allCategories = inventoryState?.categories || [];
    const myCategories = allCategories.filter(cat => cat.assigned_to_current_user);
    const freeCategories = allCategories.filter(cat => cat.can_take);
    const occupiedCategories = allCategories.filter(cat => cat.is_blocked_by_other);
    const problemCategories = allCategories.filter(categoryHasProblems);

    if (dateLine) {
        dateLine.textContent = `Общая ревизия за ${inventoryState.report_date}`;
    }

    const statMy = document.getElementById('stat-my');
    const statFree = document.getElementById('stat-free');
    const statBusy = document.getElementById('stat-busy');
    const statProblem = document.getElementById('stat-problem');

    if (statMy) statMy.textContent = String(myCategories.length);
    if (statFree) statFree.textContent = String(freeCategories.length);
    if (statBusy) statBusy.textContent = String(occupiedCategories.length);
    if (statProblem) statProblem.textContent = String(problemCategories.length);

    if (summary) {
        summary.innerHTML = `
            <strong>Мои категории:</strong> ${myCategories.length}.
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

function renderCategoryCard(category, query) {
    const blockedClass = category.is_blocked_by_other ? 'blocked-category' : '';

    let icon = '📁';
    let meta = 'свободна';

    if (category.is_completed && category.assigned_to_current_user) {
        icon = '✅';
        meta = 'ваша категория завершена';
    } else if (category.assigned_to_current_user) {
        icon = category.is_open ? '📂' : '📁';
        meta = 'закреплена за вами';
    } else if (category.is_blocked_by_other) {
        icon = '👤';
        meta = `занята: ${category.assigned_to}`;
    }

    const visibleSubcategories = getVisibleSubcategories(category, query);
    const queryActive = Boolean(normalizeSearch(query));
    const bodyVisible = queryActive ? true : category.is_open;

    let bodyHtml = '';

    if (category.can_take) {
        bodyHtml = `
            <div class="category-card">
                <p class="muted-text">Категория пока никем не взята в работу.</p>
                <button class="btn primary btn-inline" onclick="takeCategory('${category.id}')">Взять категорию</button>
            </div>
        `;
    } else if (category.is_blocked_by_other) {
        bodyHtml = `
            <div class="category-card">
                <p class="muted-text">Категория уже закреплена за сотрудником <strong>${escapeHtml(category.assigned_to)}</strong>.</p>
            </div>
        `;
    } else if (category.assigned_to_current_user) {
        if (!visibleSubcategories.length) {
            bodyHtml = `<div class="employee-empty-state">По этому запросу в категории ничего не найдено.</div>`;
        } else {
            bodyHtml = visibleSubcategories.map(sub => {
                const locked = sub.is_locked;
                const icon = locked ? '🔒' : (sub.is_completed ? '✅' : '📂');
                const subExpanded = queryActive ? true : sub.is_expanded;

                const visibleItems = (() => {
                    if (!queryActive) return sub.items || [];
                    const q = normalizeSearch(query);
                    const subDirectMatch = normalizeSearch(sub.name).includes(q);
                    if (subDirectMatch) return sub.items || [];
                    return (sub.items || []).filter(item => itemMatchesSearch(item, q));
                })();

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
                                <input type="number"
                                    id="input-${item.id}"
                                    placeholder="Факт. шт."
                                    min="0"
                                    step="1"
                                    ${itemDisabled ? 'disabled' : ''}>
                                <button class="btn check btn-inline"
                                        onclick="verifyItem('${item.id}', '${sub.id}')"
                                        ${itemDisabled ? 'disabled' : ''}>
                                    Ввод
                                </button>
                            </div>
                            <div id="msg-${item.id}" class="message" style="color:${itemMessageColor};">${itemMessage}</div>
                        </div>
                    `;
                }).join('');

                return `
                    <div class="category-card subcategory-card status-${sub.status}" id="card-${sub.id}">
                        <h3 id="title-${sub.id}" onclick="toggleSubcategory('${sub.id}')">${icon} ${highlightMatch(sub.name, query)}</h3>
                        <div id="body-${sub.id}" style="display:${subExpanded ? 'block' : 'none'}; ${locked ? 'opacity:.65;' : ''}">
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
                        </div>
                    </div>
                `;
            }).join('');
        }
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
        return `
            ${buildSectionHeader(title, description, 0)}
            <div class="employee-empty-state">В этом разделе ничего не найдено.</div>
        `;
    }

    return `
        ${buildSectionHeader(title, description, categories.length)}
        ${categories.map(category => renderCategoryCard(category, query)).join('')}
    `;
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
            renderCategorySection('Мои категории', 'Категории, закреплённые за вами.', mine, query),
            renderCategorySection('Свободные категории', 'Их можно взять в работу.', free, query),
            renderCategorySection('Категории других сотрудников', 'Эти категории уже заняты.', busy, query),
        ].join('');
    } else {
        const descriptions = {
            mine: 'Категории, закреплённые за вами.',
            free: 'Категории, которые можно взять в работу.',
            busy: 'Категории, закреплённые за другими сотрудниками.',
            problem: 'Категории, в которых уже есть расхождения.',
        };

        const titles = {
            mine: 'Мои категории',
            free: 'Свободные категории',
            busy: 'Занятые категории',
            problem: 'Категории с расхождениями',
        };

        container.innerHTML = renderCategorySection(
            titles[employeePageState.filter] || 'Категории',
            descriptions[employeePageState.filter] || 'Результат текущего фильтра.',
            filtered,
            query
        );
    }

    renderSummary();
    updateFilterButtons();
}

window.toggleCategory = function (categoryId) {
    const category = getCategory(categoryId);
    if (!category || category.is_blocked_by_other) return;
    category.is_open = !category.is_open;
    renderCategories();
};

window.toggleSubcategory = function (subId) {
    const found = findSubcategory(subId);
    if (!found || found.sub.is_locked) return;
    found.sub.is_expanded = !found.sub.is_expanded;
    renderCategories();
};

window.takeCategory = async function (categoryId) {
    try {
        const response = await fetch('/assign-category', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_id: inventoryState.report_id,
                category_id: categoryId,
            }),
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
            body: JSON.stringify({
                report_id: inventoryState.report_id,
                target_id: subId,
                is_category: true,
                quantity: inputValue,
                attempt_number: subcategoryAttempts[subId],
            }),
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

window.verifyItem = async function (itemId, subId) {
    const inputElement = document.getElementById(`input-${itemId}`);
    const msgElement = document.getElementById(`msg-${itemId}`);
    const inputValue = parseFloat(inputElement.value);
    if (Number.isNaN(inputValue)) return;

    itemAttempts[itemId] = (itemAttempts[itemId] || 0) + 1;

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_id: inventoryState.report_id,
                target_id: itemId,
                is_category: false,
                quantity: inputValue,
                attempt_number: itemAttempts[itemId],
            }),
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
            if (summary) {
                summary.innerHTML = '<span style="color:#dc3545;">Ошибка загрузки ревизии.</span>';
            }
            if (container) {
                container.innerHTML = '<div class="employee-empty-state">Не удалось загрузить данные с сервера.</div>';
            }
            return;
        }

        const data = await response.json();
        inventoryState = data;
        document.getElementById('current-location-title').textContent = `Точка: ${data.location}`;
        renderCategories();
    } catch (error) {
        console.error('loadInventory error:', error);
        if (summary) {
            summary.innerHTML = '<span style="color:#dc3545;">Ошибка загрузки ревизии.</span>';
        }
        if (container) {
            container.innerHTML = '<div class="employee-empty-state">Ошибка соединения с сервером.</div>';
        }
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