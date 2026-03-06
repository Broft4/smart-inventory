document.addEventListener('DOMContentLoaded', () => {
    const startBtn = document.getElementById('start-btn');
    const locationSelect = document.getElementById('location-select');
    const startScreen = document.getElementById('start-screen');
    const inventoryScreen = document.getElementById('inventory-screen');
    const categoriesContainer = document.getElementById('categories-container');
    const currentLocationTitle = document.getElementById('current-location-title');

    // --- БЛОК ПАМЯТИ ---
    const savedLocation = localStorage.getItem('currentLocation');
    const savedHtml = localStorage.getItem('inventoryHtml');

    if (savedLocation && savedHtml) {
        startScreen.style.display = 'none';
        inventoryScreen.style.display = 'block';
        currentLocationTitle.textContent = "Точка: " + savedLocation;
        categoriesContainer.innerHTML = savedHtml;

        window.subAttempts = JSON.parse(localStorage.getItem('subAttempts') || '{}');
        window.itemAttempts = JSON.parse(localStorage.getItem('itemAttempts') || '{}');
    } else {
        window.subAttempts = {};
        window.itemAttempts = {};
    }

    // --- КНОПКА "НАЧАТЬ" ---
    startBtn.addEventListener('click', async () => {
        const selectedLocation = locationSelect.value;
        startBtn.disabled = true;
        startBtn.textContent = 'Загрузка...';

        try {
            const response = await fetch(`/get-structure?location=${selectedLocation}`);
            const data = await response.json();

            startScreen.style.display = 'none';
            inventoryScreen.style.display = 'block';
            currentLocationTitle.textContent = `Точка: ${data.location}`;

            renderCategories(data.categories);

            localStorage.setItem('currentLocation', selectedLocation);
            window.saveState();
            
        } catch (error) {
            alert('Ошибка при загрузке данных!');
            console.error(error);
            startBtn.disabled = false;
            startBtn.textContent = 'Начать инвентаризацию';
        }
    });

    // --- НОВАЯ 3-УРОВНЕВАЯ ОТРИСОВКА ---
    function renderCategories(categories) {
        categoriesContainer.innerHTML = ''; 
        
        categories.forEach(cat => {
            // Уровень 1: Категория (просто большой заголовок)
            const catBlock = document.createElement('div');
            catBlock.className = 'main-category-block';
            catBlock.innerHTML = `<h2 style="background: #343a40; color: white; padding: 10px; border-radius: 8px; margin-top: 20px;">${cat.name}</h2>`;
            
            // Уровень 2: Подкатегории
            cat.subcategories.forEach(sub => {
                const subCard = document.createElement('div');
                subCard.className = `category-card`; 
                subCard.style.marginLeft = '10px';
                subCard.style.borderLeft = '4px solid #6c757d';
                
                // Уровень 3: Товары (скрыты по умолчанию)
                let itemsHtml = '';
                sub.items.forEach(item => {
                    itemsHtml += `
                        <div class="item-card" style="margin-top: 10px; padding: 10px; background: #e9ecef; border-radius: 8px;">
                            <h4 style="margin: 0 0 10px 0;">${item.name} (${item.uom})</h4>
                            <div class="input-group">
                                <input type="number" id="input-${item.id}" placeholder="Факт. шт." min="0" step="1">
                                <button class="btn check" onclick="verifyItem('${item.id}')">Ввод</button>
                            </div>
                            <div id="msg-${item.id}" class="message"></div>
                        </div>
                    `;
                });

                subCard.innerHTML = `
                    <h3>📂 ${sub.name}</h3>
                    <p style="font-size: 0.85em; color: #666;">Посчитайте всё вместе:</p>
                    <div class="input-group">
                        <input type="number" id="input-${sub.id}" placeholder="Общее кол-во" min="0" step="1">
                        <button class="btn check" onclick="verifySubcategory('${sub.id}')">Ввод</button>
                    </div>
                    <div id="msg-${sub.id}" class="message"></div>
                    
                    <div id="items-${sub.id}" class="items-container" style="display: none; margin-top: 15px; border-top: 2px dashed #ccc; padding-top: 10px;">
                        <p style="color: #dc3545; font-weight: bold; margin-bottom: 5px;">⚠️ Не сошлось. Считаем поштучно:</p>
                        ${itemsHtml}
                    </div>
                `;
                catBlock.appendChild(subCard);
            });

            categoriesContainer.appendChild(catBlock);
        });
    }
}); 

// ==========================================
// ФУНКЦИИ ПРОВЕРКИ И ПАМЯТИ
// ==========================================

window.saveState = function() {
    const html = document.getElementById('categories-container').innerHTML;
    localStorage.setItem('inventoryHtml', html);
    localStorage.setItem('subAttempts', JSON.stringify(window.subAttempts || {}));
    localStorage.setItem('itemAttempts', JSON.stringify(window.itemAttempts || {}));
};

window.finishInventory = function() {
    if(confirm("Точно завершить ревизию на этой точке?")) {
        localStorage.clear(); 
        location.reload();    
    }
};

window.verifySubcategory = async function(id) {
    const inputElement = document.getElementById(`input-${id}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${id}`);
    const cardElement = inputElement.closest('.category-card');
    
    if (isNaN(inputValue)) return;

    if (!window.subAttempts[id]) window.subAttempts[id] = 1;
    else window.subAttempts[id]++;

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_id: id, target_type: 'subcategory', quantity: inputValue, attempt_number: window.subAttempts[id] })
        });
        const result = await response.json();
        
        if (result.is_correct) {
            msgElement.textContent = result.message;
            msgElement.style.color = "green";
            cardElement.style.borderColor = "green";
            inputElement.setAttribute('value', inputValue);
            inputElement.setAttribute('disabled', 'true');
        } else {
            msgElement.textContent = result.message;
            msgElement.style.color = "red";
            
            if (result.expand_category) {
                cardElement.style.borderColor = "orange";
                inputElement.setAttribute('value', inputValue);
                inputElement.setAttribute('disabled', 'true');
                document.getElementById(`items-${id}`).style.display = 'block';
            } else {
                inputElement.value = ''; 
            }
        }
        window.saveState();
    } catch (error) {
        msgElement.textContent = "Ошибка сервера";
    }
};

window.verifyItem = async function(id) {
    const inputElement = document.getElementById(`input-${id}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${id}`);
    
    if (isNaN(inputValue)) return;

    if (!window.itemAttempts[id]) window.itemAttempts[id] = 1;
    else window.itemAttempts[id]++;

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_id: id, target_type: 'item', quantity: inputValue, attempt_number: window.itemAttempts[id] })
        });
        const result = await response.json();
        
        msgElement.textContent = result.message;
        if (result.is_correct) {
            msgElement.style.color = "green";
            inputElement.setAttribute('value', inputValue);
            inputElement.setAttribute('disabled', 'true');
        } else {
            msgElement.style.color = "red";
            if (result.attempts_left === 0) {
                inputElement.setAttribute('value', inputValue);
                inputElement.setAttribute('disabled', 'true');
            } else {
                inputElement.value = '';
            }
        }
        window.saveState();
    } catch (error) {
        msgElement.textContent = "Ошибка сервера";
    }
};