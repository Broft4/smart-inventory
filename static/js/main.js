window.currentReportId = null;
window.currentLocation = null;
window.manualCategoryState = {};
window.manualSubcategoryState = {};
window.lastMessages = {};

function getSubcategoryIcon(sub) {
    if (sub.is_completed) return '✅';
    if (sub.status === 'orange') return '⚠️';
    if (sub.is_locked) return '🔒';
    return '📂';
}

function getCategoryIcon(category) {
    if (category.is_completed && category.status === 'green') return '✅';
    if (category.is_completed && category.status === 'red') return '⚠️';
    if (category.status === 'orange') return '🟠';
    if (category.is_locked) return '🔒';
    return '📁';
}

function getDefaultMessage(status, targetType) {
    if (targetType === 'subcategory') {
        if (status === 'green') return 'Подкатегория подтверждена.';
        if (status === 'red') return 'Подкатегория завершена с расхождениями.';
        if (status === 'orange') return 'Откройте товары и проверьте их поштучно.';
        return '';
    }

    if (status === 'green') return 'Товар подтвержден.';
    if (status === 'red') return 'Расхождение по товару зафиксировано.';
    return '';
}

function getMessageColor(status) {
    if (status === 'green') return '#28a745';
    if (status === 'red' || status === 'orange') return '#dc3545';
    return '#333';
}

function getCategoryExpanded(category) {
    if (category.is_locked) return false;
    if (Object.prototype.hasOwnProperty.call(window.manualCategoryState, category.id)) {
        return window.manualCategoryState[category.id];
    }
    return category.is_expanded;
}

function getSubcategoryExpanded(subcategory, itemsVisible) {
    if (subcategory.is_locked) return false;
    if (Object.prototype.hasOwnProperty.call(window.manualSubcategoryState, subcategory.id)) {
        return window.manualSubcategoryState[subcategory.id];
    }
    return subcategory.is_expanded || itemsVisible;
}

function setTransientMessage(targetId, text, color) {
    window.lastMessages[targetId] = { text, color };
}

function renderMessageHtml(targetId, status, targetType) {
    const saved = window.lastMessages[targetId];
    const text = saved?.text ?? getDefaultMessage(status, targetType);
    const color = saved?.color ?? getMessageColor(status);
    return `<div id="msg-${targetId}" class="message" style="color:${color};">${text}</div>`;
}

function resetToStartScreen() {
    document.getElementById('inventory-screen').style.display = 'none';
    document.getElementById('start-screen').style.display = 'block';
    document.getElementById('categories-container').innerHTML = '';
    document.getElementById('current-location-title').textContent = '';
    window.currentReportId = null;
    window.currentLocation = null;
    window.manualCategoryState = {};
    window.manualSubcategoryState = {};
    window.lastMessages = {};
}

function allCategoriesCompleted(categories) {
    return Array.isArray(categories) && categories.length > 0 && categories.every(category => category.is_completed);
}

function updateFinishButtonState(categories) {
    const finishBtn = document.getElementById('finish-btn');
    const finishHint = document.getElementById('finish-hint');
    if (!finishBtn) return;

    const allCompleted = categories.length > 0 && categories.every(cat => cat.is_completed);

    finishBtn.disabled = !allCompleted;

    if (finishHint) {
        finishHint.textContent = allCompleted
            ? 'Все категории пройдены. Ревизию можно завершить.'
            : 'Кнопка станет доступна после прохождения всех категорий.';
    }
}

function renderCategories(categories) {
    const categoriesContainer = document.getElementById('categories-container');
    categoriesContainer.innerHTML = '';

    categories.forEach(category => {
        const catExpanded = getCategoryExpanded(category);
        const catBlock = document.createElement('div');
        catBlock.className = `main-category-block category-shell ${category.is_locked ? 'locked-category-shell' : ''}`;
        catBlock.innerHTML = `
            <button
                type="button"
                class="category-toggle status-${category.status} ${category.is_locked ? 'locked-toggle' : ''}"
                onclick="toggleCategory('${category.id}', ${category.is_locked})"
            >
                <span class="category-toggle-left">${getCategoryIcon(category)} ${category.name}</span>
                <span id="category-arrow-${category.id}" class="category-arrow">${catExpanded ? '▾' : '▸'}</span>
            </button>
            <div id="category-body-${category.id}" class="category-body" style="display:${catExpanded ? 'block' : 'none'};"></div>
        `;

        const categoryBody = catBlock.querySelector(`#category-body-${category.id}`);

        category.subcategories.forEach(sub => {
            const itemsHtml = sub.items.map(item => `
                <div class="item-card status-${item.status}">
                    <h4 style="margin: 0 0 10px 0;">${item.name} (${item.uom})</h4>
                    <div class="input-group">
                        <input type="number" id="input-${item.id}" placeholder="Факт. кол-во" min="0" step="1" ${item.status === 'green' || item.status === 'red' ? 'disabled' : ''} value="${item.entered_quantity ?? ''}">
                        <button class="btn check" onclick="verifyItem('${item.id}', '${sub.id}')" ${sub.status !== 'orange' || item.status === 'green' || item.status === 'red' ? 'disabled' : ''}>Ввод</button>
                    </div>
                    ${renderMessageHtml(item.id, item.status, 'item')}
                </div>
            `).join('');

            const itemsVisible = sub.status === 'orange' || sub.items.some(item => item.status === 'green' || item.status === 'red');
            const subExpanded = getSubcategoryExpanded(sub, itemsVisible);

            const subCard = document.createElement('div');
            subCard.className = `category-card subcategory-card status-${sub.status}`;
            if (sub.is_locked) subCard.classList.add('locked-card');
            subCard.id = `card-${sub.id}`;
            subCard.dataset.id = sub.id;

            subCard.innerHTML = `
                <button type="button" class="subcategory-toggle" onclick="toggleSubcategory('${sub.id}', ${sub.is_locked})">
                    <span>${getSubcategoryIcon(sub)} ${sub.name}</span>
                    <span id="sub-arrow-${sub.id}" class="subcategory-arrow">${subExpanded ? '▾' : '▸'}</span>
                </button>
                <div id="body-${sub.id}" style="display:${subExpanded ? 'block' : 'none'};">
                    <p class="subcategory-hint">Сначала введите общее количество по подкатегории.</p>
                    <div class="input-group">
                        <input type="number" id="input-${sub.id}" placeholder="Общее кол-во" min="0" step="1" ${sub.is_completed || sub.status === 'orange' ? 'disabled' : ''} value="${sub.entered_quantity ?? ''}">
                        <button class="btn check" onclick="verifySubcategory('${sub.id}')" ${sub.is_locked || sub.is_completed || sub.status === 'orange' ? 'disabled' : ''}>Ввод</button>
                    </div>
                    ${renderMessageHtml(sub.id, sub.status, 'subcategory')}
                    <div id="items-${sub.id}" class="items-container" style="display:${itemsVisible ? 'block' : 'none'};">
                        <p class="items-title">Поштучная проверка товаров</p>
                        ${itemsHtml}
                    </div>
                </div>
            `;
            categoryBody.appendChild(subCard);
        });

        categoriesContainer.appendChild(catBlock);
    });
}

async function loadStructure(location) {
    const response = await fetch(`/get-structure?location=${encodeURIComponent(location)}`);
    if (!response.ok) throw new Error('Ошибка загрузки структуры');
    const data = await response.json();
    window.currentReportId = data.report_id;
    window.currentLocation = data.location;
    localStorage.setItem('inventoryLocation', data.location);
    localStorage.setItem('inventoryReportId', String(data.report_id));

    document.getElementById('start-screen').style.display = 'none';
    document.getElementById('inventory-screen').style.display = 'block';
    document.getElementById('current-location-title').textContent = `Точка: ${data.location}`;
    renderCategories(data.categories);
    updateFinishButtonState(data.categories);
}

document.addEventListener('DOMContentLoaded', async () => {
    const startBtn = document.getElementById('start-btn');
    const locationSelect = document.getElementById('location-select');
    const savedLocation = localStorage.getItem('inventoryLocation');

    if (savedLocation) {
        locationSelect.value = savedLocation;
        try {
            await loadStructure(savedLocation);
        } catch (error) {
            console.error(error);
        }
    }

    startBtn.addEventListener('click', async () => {
        const selectedLocation = locationSelect.value;
        startBtn.disabled = true;
        startBtn.textContent = 'Загрузка...';
        try {
            window.manualCategoryState = {};
            window.manualSubcategoryState = {};
            window.lastMessages = {};
            await loadStructure(selectedLocation);
        } catch (error) {
            alert('Ошибка при загрузке данных');
            console.error(error);
        } finally {
            startBtn.disabled = false;
            startBtn.textContent = 'Начать ревизию';
        }
    });
});

window.toggleCategory = function(id, isLocked) {
    if (isLocked) return;
    const body = document.getElementById(`category-body-${id}`);
    const arrow = document.getElementById(`category-arrow-${id}`);
    if (!body || !arrow) return;

    const shouldOpen = body.style.display === 'none';
    body.style.display = shouldOpen ? 'block' : 'none';
    arrow.textContent = shouldOpen ? '▾' : '▸';
    window.manualCategoryState[id] = shouldOpen;
};

window.toggleSubcategory = function(id, isLocked) {
    if (isLocked) return;
    const body = document.getElementById(`body-${id}`);
    const arrow = document.getElementById(`sub-arrow-${id}`);
    if (!body || !arrow) return;

    const shouldOpen = body.style.display === 'none';
    body.style.display = shouldOpen ? 'block' : 'none';
    arrow.textContent = shouldOpen ? '▾' : '▸';
    window.manualSubcategoryState[id] = shouldOpen;
};

window.verifySubcategory = async function(id) {
    const inputElement = document.getElementById(`input-${id}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${id}`);
    if (Number.isNaN(inputValue)) {
        msgElement.textContent = 'Введите количество.';
        msgElement.style.color = '#dc3545';
        return;
    }

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_id: window.currentReportId,
                target_id: id,
                target_type: 'subcategory',
                quantity: inputValue
            })
        });
        const result = await response.json();
        const color = result.is_correct ? '#28a745' : '#dc3545';
        setTransientMessage(id, result.message, color);
        window.manualSubcategoryState[id] = true;
        await loadStructure(window.currentLocation);
    } catch (error) {
        msgElement.textContent = 'Ошибка сервера';
        msgElement.style.color = '#dc3545';
    }
};

window.verifyItem = async function(id, subId) {
    const inputElement = document.getElementById(`input-${id}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${id}`);
    if (Number.isNaN(inputValue)) {
        msgElement.textContent = 'Введите количество.';
        msgElement.style.color = '#dc3545';
        return;
    }

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                report_id: window.currentReportId,
                target_id: id,
                target_type: 'item',
                quantity: inputValue
            })
        });
        const result = await response.json();
        const color = result.is_correct ? '#28a745' : '#dc3545';
        setTransientMessage(id, result.message, color);
        window.manualSubcategoryState[subId] = true;
        await loadStructure(window.currentLocation);
    } catch (error) {
        msgElement.textContent = 'Ошибка сервера';
        msgElement.style.color = '#dc3545';
    }
};

window.finishInventory = async function() {
    if (!window.currentReportId) {
        alert('Не найден активный отчет.');
        return;
    }

    const finishBtn = document.getElementById('finish-btn');
    if (finishBtn && finishBtn.disabled) {
        alert('Нельзя завершить ревизию, пока не пройдены все категории.');
        return;
    }

    const confirmed = confirm('Завершить ревизию на этой точке?');
    if (!confirmed) return;

    try {
        const response = await fetch('/finish-report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: Number(window.currentReportId) })
        });

        const data = await response.json();

        if (!response.ok || !data.success) {
            alert(data.message || 'Не удалось завершить ревизию.');
            return;
        }

        localStorage.removeItem('inventoryLocation');
        localStorage.removeItem('inventoryReportId');
        resetToStartScreen();
    } catch (error) {
        console.error(error);
        alert('Ошибка при завершении ревизии.');
    }
};
