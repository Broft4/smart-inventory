let inventoryState = null;
let subcategoryAttempts = {};
let itemAttempts = {};

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

function renderSummary() {
    const summary = document.getElementById('inventory-summary');
    const dateLine = document.getElementById('report-date-line');
    const myCategories = inventoryState.categories.filter(cat => cat.assigned_to_current_user);
    const freeCategories = inventoryState.categories.filter(cat => cat.can_take);
    const occupiedCategories = inventoryState.categories.filter(cat => cat.is_blocked_by_other);

    if (dateLine) {
        dateLine.textContent = `Общая ревизия за ${inventoryState.report_date}`;
    }

    summary.innerHTML = `
        <strong>Мои категории:</strong> ${myCategories.length}.
        <strong>Свободные:</strong> ${freeCategories.length}.
        <strong>У других сотрудников:</strong> ${occupiedCategories.length}.
    `;
}

function renderCategories() {
    const container = document.getElementById('categories-container');
    container.innerHTML = '';

    inventoryState.categories.forEach((cat) => {
        const catBlock = document.createElement('div');
        const blockedClass = cat.is_blocked_by_other ? 'blocked-category' : '';
        catBlock.className = `main-category-block ${blockedClass}`;

        let icon = '📁';
        let meta = 'свободна';
        if (cat.is_completed && cat.assigned_to_current_user) {
            icon = '✅';
            meta = 'ваша категория завершена';
        } else if (cat.assigned_to_current_user) {
            icon = cat.is_open ? '📂' : '📁';
            meta = 'закреплена за вами';
        } else if (cat.is_blocked_by_other) {
            icon = '👤';
            meta = `занята: ${cat.assigned_to}`;
        }

        catBlock.innerHTML = `
            <div class="category-header" onclick="toggleCategory('${cat.id}')">
                <span>${icon} ${cat.name}</span>
                <span class="category-meta">${meta}</span>
            </div>
            <div id="cat-body-${cat.id}" class="category-body" style="display:${cat.is_open ? 'block' : 'none'}"></div>
        `;

        const body = catBlock.querySelector(`#cat-body-${cat.id}`);

        if (cat.can_take) {
            body.innerHTML = `
                <div class="category-card">
                    <p class="muted-text">Категория пока никем не взята в работу.</p>
                    <button class="btn primary btn-inline" onclick="takeCategory('${cat.id}')">Взять категорию</button>
                </div>
            `;
        } else if (cat.is_blocked_by_other) {
            body.innerHTML = `
                <div class="category-card">
                    <p class="muted-text">Категория уже закреплена за сотрудником <strong>${cat.assigned_to}</strong>.</p>
                </div>
            `;
        } else if (cat.assigned_to_current_user) {
            cat.subcategories.forEach((sub) => {
                const subCard = document.createElement('div');
                subCard.className = `category-card subcategory-card status-${sub.status}`;
                subCard.id = `card-${sub.id}`;

                const locked = sub.is_locked;
                const icon = locked ? '🔒' : (sub.is_completed ? '✅' : '📂');
                const itemsHtml = sub.items.map(item => {
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
                            <h4>${item.name} (${item.uom})</h4>
                            <p class="muted-text">Посчитайте всё вместе.</p>
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

                subCard.innerHTML = `
                    <h3 id="title-${sub.id}" onclick="toggleSubcategory('${sub.id}')">${icon} ${sub.name}</h3>
                    <div id="body-${sub.id}" style="display:${sub.is_expanded ? 'block' : 'none'}; ${locked ? 'opacity:.65;' : ''}">
                        <p class="muted-text">Посчитайте всё вместе.</p>
                        <div class="input-group">
                            <input type="number" id="input-${sub.id}" placeholder="Общее кол-во" min="0" step="1" ${locked || sub.is_completed ? 'disabled' : ''}>
                            <button class="btn check btn-inline" onclick="verifySubcategory('${sub.id}')" ${locked || sub.is_completed ? 'disabled' : ''}>Ввод</button>
                        </div>
                        <div id="msg-${sub.id}" class="message"></div>
                        <div id="items-${sub.id}" class="items-container" style="display:${sub.status === 'orange' ? 'block' : 'none'};">
                            <p class="items-warning">⚠️ Не сошлось. Считаем поштучно:</p>
                            ${itemsHtml}
                        </div>
                    </div>
                `;
                body.appendChild(subCard);
            });
        }

        container.appendChild(catBlock);
    });

    renderSummary();
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
    const response = await fetch('/get-structure');
    if (response.status === 401) {
        location.href = '/login';
        return;
    }
    const data = await response.json();
    inventoryState = data;
    document.getElementById('current-location-title').textContent = `Точка: ${data.location}`;
    renderCategories();
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    location.href = '/login';
}

document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('logout-btn').addEventListener('click', logout);
    document.getElementById('finish-btn').addEventListener('click', loadInventory);
    await loadInventory();
});
