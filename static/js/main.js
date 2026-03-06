window.currentReportId = null;
window.currentLocation = null;

function getIcon(sub) {
    if (sub.is_completed) return '✅';
    if (sub.status === 'orange') return '⚠️';
    if (sub.is_locked) return '🔒';
    return '📂';
}

function renderCategories(categories) {
    const categoriesContainer = document.getElementById('categories-container');
    categoriesContainer.innerHTML = '';

    categories.forEach(cat => {
        const catBlock = document.createElement('div');
        catBlock.className = 'main-category-block';
        catBlock.innerHTML = `<h2>${cat.name}</h2>`;

        cat.subcategories.forEach(sub => {
            const subCard = document.createElement('div');
            subCard.className = `category-card subcategory-card status-${sub.status}`;
            if (sub.is_locked) subCard.classList.add('locked-card');
            subCard.id = `card-${sub.id}`;
            subCard.dataset.id = sub.id;

            const itemsHtml = sub.items.map(item => `
                <div class="item-card status-${item.status}">
                    <h4 style="margin: 0 0 10px 0;">${item.name} (${item.uom})</h4>
                    <div class="input-group">
                        <input type="number" id="input-${item.id}" placeholder="Факт. кол-во" min="0" step="1" ${item.status === 'green' || item.status === 'red' ? 'disabled' : ''} value="${item.entered_quantity ?? ''}">
                        <button class="btn check" onclick="verifyItem('${item.id}', '${sub.id}')" ${sub.status !== 'orange' || item.status === 'green' || item.status === 'red' ? 'disabled' : ''}>Ввод</button>
                    </div>
                    <div id="msg-${item.id}" class="message">${item.status === 'green' ? 'Товар подтвержден.' : item.status === 'red' ? 'Расхождение зафиксировано.' : ''}</div>
                </div>
            `).join('');

            const showItems = sub.status === 'orange' || sub.items.some(item => item.status === 'green' || item.status === 'red');
            subCard.innerHTML = `
                <h3 id="title-${sub.id}" onclick="toggleCard('${sub.id}')" style="cursor: pointer; margin-top: 0;">${getIcon(sub)} ${sub.name}</h3>
                <div id="body-${sub.id}" style="display: ${sub.is_expanded ? 'block' : 'none'};">
                    <p style="font-size: 0.9em; color: #666;">Сначала введите общее количество по подкатегории.</p>
                    <div class="input-group">
                        <input type="number" id="input-${sub.id}" placeholder="Общее кол-во" min="0" step="1" ${sub.is_completed || sub.status === 'orange' ? 'disabled' : ''} value="${sub.entered_quantity ?? ''}">
                        <button class="btn check" onclick="verifySubcategory('${sub.id}')" ${sub.is_locked || sub.is_completed || sub.status === 'orange' ? 'disabled' : ''}>Ввод</button>
                    </div>
                    <div id="msg-${sub.id}" class="message">${sub.status === 'green' ? 'Подкатегория подтверждена.' : sub.status === 'red' ? 'Подкатегория завершена с расхождениями.' : sub.status === 'orange' ? 'Откройте товары и проверьте их поштучно.' : ''}</div>
                    <div id="items-${sub.id}" class="items-container" style="display: ${showItems ? 'block' : 'none'};">
                        <p style="color: #dc3545; font-weight: bold; margin-bottom: 8px;">Поштучная проверка товаров</p>
                        ${itemsHtml}
                    </div>
                </div>
            `;
            catBlock.appendChild(subCard);
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

window.toggleCard = function(id) {
    const body = document.getElementById(`body-${id}`);
    if (!body) return;
    body.style.display = body.style.display === 'none' ? 'block' : 'none';
};

window.verifySubcategory = async function(id) {
    const inputElement = document.getElementById(`input-${id}`);
    const msgElement = document.getElementById(`msg-${id}`);
    const inputValue = parseFloat(inputElement.value);
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
        const message = result.message;
        const color = result.is_correct ? '#28a745' : '#dc3545';
        await loadStructure(window.currentLocation);
        const freshMessage = document.getElementById(`msg-${id}`);
        if (freshMessage) {
            freshMessage.textContent = message;
            freshMessage.style.color = color;
        }
    } catch (error) {
        msgElement.textContent = 'Ошибка сервера';
        msgElement.style.color = '#dc3545';
    }
};

window.verifyItem = async function(id, subId) {
    const inputElement = document.getElementById(`input-${id}`);
    const msgElement = document.getElementById(`msg-${id}`);
    const inputValue = parseFloat(inputElement.value);
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
        const message = result.message;
        const color = result.is_correct ? '#28a745' : '#dc3545';
        await loadStructure(window.currentLocation);
        const freshMessage = document.getElementById(`msg-${id}`);
        if (freshMessage) {
            freshMessage.textContent = message;
            freshMessage.style.color = color;
        }
        const body = document.getElementById(`body-${subId}`);
        if (body) body.style.display = 'block';
    } catch (error) {
        msgElement.textContent = 'Ошибка сервера';
        msgElement.style.color = '#dc3545';
    }
};

window.finishInventory = async function() {
    if (!window.currentReportId) {
        localStorage.removeItem('inventoryLocation');
        localStorage.removeItem('inventoryReportId');
        location.reload();
        return;
    }
    const confirmed = confirm('Завершить ревизию на этой точке?');
    if (!confirmed) return;

    try {
        await fetch('/finish-report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report_id: window.currentReportId })
        });
    } catch (error) {
        console.error(error);
    } finally {
        localStorage.removeItem('inventoryLocation');
        localStorage.removeItem('inventoryReportId');
        location.reload();
    }
};
