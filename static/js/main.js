document.addEventListener('DOMContentLoaded', () => {
    const startBtn = document.getElementById('start-btn');
    const locationSelect = document.getElementById('location-select');
    const startScreen = document.getElementById('start-screen');
    const inventoryScreen = document.getElementById('inventory-screen');
    const categoriesContainer = document.getElementById('categories-container');
    const currentLocationTitle = document.getElementById('current-location-title');

    // --- БЛОК ПАМЯТИ (LOCAL STORAGE) ПРИ ЗАГРУЗКЕ ---
    const savedLocation = localStorage.getItem('currentLocation');
    const savedHtml = localStorage.getItem('inventoryHtml');

    if (savedLocation && savedHtml) {
        startScreen.style.display = 'none';
        inventoryScreen.style.display = 'block';
        currentLocationTitle.textContent = "Точка: " + savedLocation;
        categoriesContainer.innerHTML = savedHtml;

        window.categoryAttempts = JSON.parse(localStorage.getItem('catAttempts') || '{}');
        window.itemAttempts = JSON.parse(localStorage.getItem('itemAttempts') || '{}');
    } else {
        window.categoryAttempts = {};
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

    // --- ОТРИСОВКА КАРТОЧЕК ---
    function renderCategories(categories) {
        categoriesContainer.innerHTML = ''; 
        
        categories.forEach(cat => {
            const card = document.createElement('div');
            card.className = `category-card status-${cat.status}`; 
            
            let itemsHtml = '';
            cat.items.forEach(item => {
                itemsHtml += `
                    <div class="item-card" style="margin-top: 15px; padding: 10px; background: #f1f3f5; border-radius: 8px;">
                        <h4 style="margin: 0 0 10px 0;">${item.name} (${item.uom})</h4>
                        <div class="input-group">
                            <input type="number" id="input-${item.id}" placeholder="Факт. кол-во" min="0" step="1">
                            <button class="btn check" onclick="verifyItem('${item.id}', '${cat.id}')">Ввод</button>
                        </div>
                        <div id="msg-${item.id}" class="message"></div>
                    </div>
                `;
            });

            card.innerHTML = `
                <h3>${cat.name}</h3>
                <div class="input-group">
                    <input type="number" id="input-${cat.id}" placeholder="Общее кол-во" min="0" step="1">
                    <button class="btn check" onclick="verifyCategory('${cat.id}')">Ввод</button>
                </div>
                <div id="msg-${cat.id}" class="message"></div>
                
                <div id="items-${cat.id}" class="items-container" style="display: none; margin-top: 15px; border-top: 2px dashed #ccc; padding-top: 10px;">
                    <p style="color: #fd7e14; font-weight: bold; margin-bottom: 5px;">Поштучный пересчет:</p>
                    ${itemsHtml}
                </div>
            `;
            categoriesContainer.appendChild(card);
        });
    }
}); // <-- ЗАКРЫВАЕМ БЛОК ИНИЦИАЛИЗАЦИИ

// ==========================================
// ГЛОБАЛЬНЫЕ ФУНКЦИИ (ДЛЯ КНОПОК И ПАМЯТИ)
// ==========================================

window.saveState = function() {
    const html = document.getElementById('categories-container').innerHTML;
    localStorage.setItem('inventoryHtml', html);
    localStorage.setItem('catAttempts', JSON.stringify(window.categoryAttempts || {}));
    localStorage.setItem('itemAttempts', JSON.stringify(window.itemAttempts || {}));
};

window.finishInventory = function() {
    if(confirm("Точно завершить ревизию на этой точке?")) {
        localStorage.clear(); 
        location.reload();    
    }
};

window.verifyCategory = async function(id) {
    const inputElement = document.getElementById(`input-${id}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${id}`);
    const cardElement = inputElement.closest('.category-card');
    
    if (isNaN(inputValue)) {
        msgElement.textContent = "Пожалуйста, введите число!";
        return;
    }

    if (!window.categoryAttempts[id]) window.categoryAttempts[id] = 1;
    else window.categoryAttempts[id]++;

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target_id: id,
                is_category: true,
                quantity: inputValue,
                attempt_number: window.categoryAttempts[id]
            })
        });
        
        const result = await response.json();
        
        if (result.is_correct) {
            msgElement.textContent = result.message;
            msgElement.style.color = "green";
            cardElement.className = 'category-card status-green';
            
            // Фиксируем введенное значение и блокируем поле!
            inputElement.setAttribute('value', inputValue);
            inputElement.setAttribute('disabled', 'true');
        } else {
            msgElement.textContent = result.message;
            msgElement.style.color = "red";
            
            if (result.expand_category) {
                msgElement.textContent += " Переходим к поштучной проверке...";
                cardElement.className = 'category-card status-orange';
                
                // Фиксируем введенное значение и блокируем поле!
                inputElement.setAttribute('value', inputValue);
                inputElement.setAttribute('disabled', 'true');
                
                document.getElementById(`items-${id}`).style.display = 'block';
            } else {
                inputElement.value = ''; // Очищаем для новой попытки
            }
        }
        window.saveState(); // Сохраняем "снимок"
    } catch (error) {
        console.error("Ошибка:", error);
        msgElement.textContent = "Ошибка связи с сервером";
    }
};

window.verifyItem = async function(itemId, categoryId) {
    const inputElement = document.getElementById(`input-${itemId}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${itemId}`);
    
    if (isNaN(inputValue)) {
        msgElement.textContent = "Пожалуйста, введите число!";
        return;
    }

    if (!window.itemAttempts[itemId]) window.itemAttempts[itemId] = 1;
    else window.itemAttempts[itemId]++;

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target_id: itemId,
                is_category: false,
                quantity: inputValue,
                attempt_number: window.itemAttempts[itemId]
            })
        });
        
        const result = await response.json();
        
        if (result.is_correct) {
            msgElement.textContent = result.message;
            msgElement.style.color = "green";
            
            inputElement.setAttribute('value', inputValue);
            inputElement.setAttribute('disabled', 'true');
        } else {
            msgElement.textContent = result.message;
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
        console.error("Ошибка:", error);
        msgElement.textContent = "Ошибка связи с сервером";
    }
};