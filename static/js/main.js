let inventoryState = null;
let subcategoryAttempts = {};
let itemAttempts = {};

function updateFinishButtonState() {
    const finishBtn = document.getElementById('finish-btn');
    const finishHint = document.getElementById('finish-hint');
    if (!finishBtn || !inventoryState) return;

    const allCompleted = inventoryState.categories.length > 0 && inventoryState.categories.every(cat => cat.is_completed);
    finishBtn.disabled = !allCompleted;
    if (finishHint) {
        finishHint.textContent = allCompleted
            ? 'Все категории пройдены. Ревизию можно завершить.'
            : 'Кнопка станет доступна после прохождения всех категорий.';
    }
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

function renderCategories() {
    const container = document.getElementById('categories-container');
    container.innerHTML = '';

    inventoryState.categories.forEach((cat) => {
        const catBlock = document.createElement('div');
        catBlock.className = `main-category-block ${cat.is_available ? '' : 'blocked-category'}`;
        const categoryHeaderIcon = cat.is_completed ? '✅' : (cat.is_available ? (cat.is_open ? '📂' : '📁') : '🔒');
        catBlock.innerHTML = `
            <div class="category-header" onclick="toggleCategory('${cat.id}')">
                <span>${categoryHeaderIcon} ${cat.name}</span>
                <span class="category-meta">${cat.is_completed ? 'завершена' : (cat.is_available ? 'в работе' : 'ожидает')}</span>
            </div>
            <div id="cat-body-${cat.id}" class="category-body" style="display:${cat.is_open ? 'block' : 'none'}"></div>
        `;

        const body = catBlock.querySelector(`#cat-body-${cat.id}`);

        cat.subcategories.forEach((sub) => {
            const subCard = document.createElement('div');
            subCard.className = `category-card subcategory-card status-${sub.status}`;
            subCard.id = `card-${sub.id}`;
            subCard.dataset.id = sub.id;

            const locked = !cat.is_available || sub.is_locked;
            const icon = locked ? '🔒' : (sub.is_completed ? '✅' : '📂');
            const itemsHtml = sub.items.map(item => `
                <div class="item-card">
                    <h4>${item.name} (${item.uom})</h4>
                    <p class="muted-text">По системе: ${item.expected_qty}</p>
                    <div class="input-group">
                        <input type="number" id="input-${item.id}" placeholder="Факт. шт." min="0" step="1" ${sub.status !== 'orange' ? 'disabled' : ''}>
                        <button class="btn check btn-inline" onclick="verifyItem('${item.id}', '${sub.id}')" ${sub.status !== 'orange' ? 'disabled' : ''}>Ввод</button>
                    </div>
                    <div id="msg-${item.id}" class="message"></div>
                </div>
            `).join('');

            subCard.innerHTML = `
                <h3 id="title-${sub.id}" onclick="toggleSubcategory('${sub.id}')">${icon} ${sub.name}</h3>
                <div id="body-${sub.id}" style="display:${sub.is_expanded ? 'block' : 'none'}; ${locked ? 'opacity:.65;' : ''}">
                    <p class="muted-text">Посчитайте всё вместе. По системе должно быть: <strong>${sub.expected_total}</strong></p>
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

        container.appendChild(catBlock);
    });

    updateFinishButtonState();
}

function markSubcategoryComplete(subId) {
    const found = findSubcategory(subId);
    if (!found) return;
    const { category, sub } = found;
    sub.is_completed = true;
    sub.is_expanded = false;
    sub.is_locked = false;
    if (sub.status === 'grey' || sub.status === 'orange') {
        sub.status = 'green';
    }

    const currentSubIndex = category.subcategories.findIndex(item => item.id === subId);
    const nextSub = category.subcategories[currentSubIndex + 1];
    if (nextSub) {
        nextSub.is_locked = false;
        nextSub.is_expanded = true;
    } else {
        category.is_completed = true;
        category.is_open = false;
        const currentCategoryIndex = inventoryState.categories.findIndex(item => item.id === category.id);
        const nextCategory = inventoryState.categories[currentCategoryIndex + 1];
        if (nextCategory) {
            nextCategory.is_available = true;
            nextCategory.is_open = true;
            if (nextCategory.subcategories[0]) {
                nextCategory.subcategories[0].is_locked = false;
                nextCategory.subcategories[0].is_expanded = true;
            }
        }
    }

    renderCategories();
}

function checkAllItemsCompleted(subId) {
    const found = findSubcategory(subId);
    if (!found) return;
    const { sub } = found;
    const allProcessed = sub.items.every(item => {
        const attempts = itemAttempts[item.id] || 0;
        const input = document.getElementById(`input-${item.id}`);
        return attempts >= 3 || (input && input.disabled);
    });
    if (allProcessed) {
        markSubcategoryComplete(subId);
    }
}

window.toggleCategory = function (categoryId) {
    const category = inventoryState.categories.find(item => item.id === categoryId);
    if (!category || !category.is_available) return;
    category.is_open = !category.is_open;
    renderCategories();
};

window.toggleSubcategory = function (subId) {
    const found = findSubcategory(subId);
    if (!found || found.sub.is_locked) return;
    found.sub.is_expanded = !found.sub.is_expanded;
    renderCategories();
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
        if (result.is_correct) {
            msgElement.style.color = 'green';
            inputElement.disabled = true;
            markSubcategoryComplete(subId);
            return;
        }

        msgElement.style.color = 'red';
        if (result.expand_category) {
            found.sub.status = 'orange';
            found.sub.is_expanded = true;
            inputElement.disabled = true;
            renderCategories();
        } else {
            inputElement.value = '';
        }
    } catch (error) {
        console.error(error);
        msgElement.textContent = 'Ошибка сервера';
    }
};

window.verifyItem = async function (itemId, subId) {
    const found = findItem(itemId);
    if (!found) return;
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
            inputElement.disabled = true;
        } else {
            inputElement.value = '';
        }

        checkAllItemsCompleted(subId);
    } catch (error) {
        console.error(error);
        msgElement.textContent = 'Ошибка сервера';
    }
};

window.finishInventory = async function () {
    if (!inventoryState?.report_id) return;
    if (!confirm('Завершить ревизию на этой точке?')) return;

    try {
        const response = await fetch('/finish-report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: Number(inventoryState.report_id) }),
        });
        const data = await response.json();
        if (!response.ok || !data.success) {
            alert(data.message || 'Не удалось завершить ревизию.');
            return;
        }
        location.reload();
    } catch (error) {
        console.error('finishInventory error:', error);
        alert('Ошибка при завершении ревизии.');
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
    renderCategories();
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    location.href = '/login';
}

document.addEventListener('DOMContentLoaded', async () => {
    document.getElementById('logout-btn').addEventListener('click', logout);
    await loadInventory();
});
