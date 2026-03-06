document.addEventListener('DOMContentLoaded', () => {
    const startBtn = document.getElementById('start-btn');
    const locationSelect = document.getElementById('location-select');
    const startScreen = document.getElementById('start-screen');
    const inventoryScreen = document.getElementById('inventory-screen');
    const categoriesContainer = document.getElementById('categories-container');
    const currentLocationTitle = document.getElementById('current-location-title');


// --- БЛОК ПАМЯТИ (LOCAL STORAGE) ---
document.addEventListener('DOMContentLoaded', () => {
    // При загрузке страницы проверяем, есть ли сохраненные данные
    const savedLocation = localStorage.getItem('currentLocation');
    const savedHtml = localStorage.getItem('inventoryHtml');

    if (savedLocation && savedHtml) {
        // Если есть - восстанавливаем экран без выбора города
        document.getElementById('location-screen').style.display = 'none';
        document.getElementById('scanner-screen').style.display = 'block';
        document.getElementById('current-location').textContent = savedLocation;
        document.getElementById('categories-container').innerHTML = savedHtml;

        // Восстанавливаем попытки
        window.categoryAttempts = JSON.parse(localStorage.getItem('catAttempts') || '{}');
        window.itemAttempts = JSON.parse(localStorage.getItem('itemAttempts') || '{}');
    }
});

// Функция, которая делает "снимок" экрана и сохраняет его
window.saveState = function() {
    const html = document.getElementById('categories-container').innerHTML;
    localStorage.setItem('inventoryHtml', html);
    localStorage.setItem('catAttempts', JSON.stringify(window.categoryAttempts || {}));
    localStorage.setItem('itemAttempts', JSON.stringify(window.itemAttempts || {}));
};

// Функция завершения (сбрасывает память)
window.finishInventory = function() {
    if(confirm("Точно завершить ревизию на этой точке?")) {
        localStorage.clear(); // Очищаем память
        location.reload();    // Перезагружаем страницу (вернет на выбор города)
    }
};
// --- КОНЕЦ БЛОКА ПАМЯТИ ---



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

            // Вызываем функцию отрисовки
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

// Функция для создания карточек
    function renderCategories(categories) {
        categoriesContainer.innerHTML = ''; 
        
        categories.forEach(cat => {
            const card = document.createElement('div');
            card.className = `category-card status-${cat.status}`; 
            
            // --- НОВОЕ: Генерируем HTML для товаров внутри категории ---
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
            // ------------------------------------------------------------

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
});

// Объект для хранения счетчика попыток для каждой карточки
window.categoryAttempts = {};

window.verifyCategory = async function(id) {
    const inputElement = document.getElementById(`input-${id}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${id}`);
    const cardElement = inputElement.closest('.category-card');
    
    if (isNaN(inputValue)) {
        msgElement.textContent = "Пожалуйста, введите число!";
        return;
    }

    // Увеличиваем счетчик попыток для этой категории
    if (!window.categoryAttempts[id]) {
        window.categoryAttempts[id] = 1;
    } else {
        window.categoryAttempts[id]++;
    }

    try {
        // Отправляем данные на бэкенд
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
            // Успех! Красим в зеленый
            msgElement.textContent = result.message;
            msgElement.style.color = "green";
            cardElement.className = 'category-card status-green';
            
            // Блокируем поле ввода
            inputElement.setAttribute('disabled', 'true');
        } else {
            // Ошибка! Красим текст в красный
            msgElement.textContent = result.message;
            msgElement.style.color = "red";
            
            if (result.expand_category) {
                // Если 3 попытки исчерпаны
                msgElement.textContent += " Переходим к поштучной проверке...";
                cardElement.className = 'category-card status-orange';
                inputElement.disabled = true;
                
                // Раскрываем блок с товарами (пока он пустой)
                document.getElementById(`items-${id}`).style.display = 'block';
            } else {
                // Очищаем поле для новой попытки
                inputElement.value = '';
            }

            
        }
        window.saveState(); // <--- ДОБАВИТЬ ЭТО В КОНЕЦ БЛОКА TRY
    } catch (error) {
        console.error("Ошибка:", error);
        msgElement.textContent = "Ошибка связи с сервером";
    }

};

// Объект для хранения счетчика попыток для товаров
window.itemAttempts = {};

window.verifyItem = async function(itemId, categoryId) {
    const inputElement = document.getElementById(`input-${itemId}`);
    const inputValue = parseFloat(inputElement.value);
    const msgElement = document.getElementById(`msg-${itemId}`);
    
    if (isNaN(inputValue)) {
        msgElement.textContent = "Пожалуйста, введите число!";
        return;
    }

    if (!window.itemAttempts[itemId]) {
        window.itemAttempts[itemId] = 1;
    } else {
        window.itemAttempts[itemId]++;
    }

    try {
        const response = await fetch('/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                target_id: itemId,
                is_category: false, // Указываем, что это товар
                quantity: inputValue,
                attempt_number: window.itemAttempts[itemId]
            })
        });
        
        const result = await response.json();
        
        if (result.is_correct) {
            msgElement.textContent = result.message;
            msgElement.style.color = "green";
            inputElement.setAttribute('disabled', 'true');
        } else {
            msgElement.textContent = result.message;
            msgElement.style.color = "red";
            
            // Если 3 попытки кончились - фиксируем ошибку намертво
            if (result.attempts_left === 0) {
                inputElement.setAttribute('disabled', 'true');
                // Тут в будущем мы будем отправлять данные в БД FastAPI о недостаче
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