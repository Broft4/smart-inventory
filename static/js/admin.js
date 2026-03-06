document.addEventListener('DOMContentLoaded', async () => {
    const locationSpan = document.getElementById('report-location');
    const dateSpan = document.getElementById('report-date');
    const totalPlusSpan = document.getElementById('total-plus');
    const totalMinusSpan = document.getElementById('total-minus');
    const categoriesContainer = document.getElementById('report-categories');

    try {
        // В реальности мы бы передавали локацию через параметры URL или сессию
        const response = await fetch('/api/report?location=Дубна');
        const report = await response.json();

        // Заполняем шапку
        locationSpan.textContent = report.location;
        dateSpan.textContent = report.date;
        totalPlusSpan.textContent = `+${report.total_plus}`;
        totalMinusSpan.textContent = report.total_minus; // Отрицательное число само будет с минусом

        // Очищаем контейнер
        categoriesContainer.innerHTML = '';

        // Отрисовываем категории
        report.categories.forEach(cat => {
            const card = document.createElement('div');
            card.className = `category-card status-${cat.status}`;
            
            let html = `<h3>${cat.name}</h3>`;
            
            // Если статус зеленый - все супер
            if (cat.status === 'green') {
                html += `<p style="color: #28a745; font-weight: bold;">✅ Расхождений нет</p>`;
            } else if (cat.problem_items.length > 0) {
                // Если есть проблемы - рисуем таблицу
                html += `<p style="color: #dc3545; font-weight: bold;">⚠️ Зафиксированы расхождения:</p>`;
                html += `<table class="admin-table">
                            <tr>
                                <th>Товар</th>
                                <th>План</th>
                                <th>Факт</th>
                                <th>Разница</th>
                            </tr>`;
                
                cat.problem_items.forEach(item => {
                    const diffColor = item.diff > 0 ? 'color: #28a745;' : 'color: #dc3545;';
                    const diffSign = item.diff > 0 ? '+' : '';
                    html += `<tr>
                                <td>${item.name}</td>
                                <td style="text-align: center;">${item.expected}</td>
                                <td style="text-align: center;">${item.actual}</td>
                                <td style="text-align: center; font-weight: bold; ${diffColor}">${diffSign}${item.diff}</td>
                            </tr>`;
                });
                html += `</table>`;
            }

            card.innerHTML = html;
            categoriesContainer.appendChild(card);
        });

    } catch (error) {
        console.error("Ошибка загрузки отчета:", error);
        categoriesContainer.innerHTML = `<p style="color: red; text-align: center;">Ошибка загрузки данных</p>`;
    }
});