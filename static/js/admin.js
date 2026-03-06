async function loadAdminReport(location) {
    const locationSpan = document.getElementById('report-location');
    const dateSpan = document.getElementById('report-date');
    const totalPlusSpan = document.getElementById('total-plus');
    const totalMinusSpan = document.getElementById('total-minus');
    const categoriesContainer = document.getElementById('report-categories');

    try {
        const response = await fetch(`/api/report?location=${encodeURIComponent(location)}`);
        if (!response.ok) throw new Error('Ошибка загрузки отчета');
        const report = await response.json();

        locationSpan.textContent = report.location;
        dateSpan.textContent = report.date;
        totalPlusSpan.textContent = `+${report.total_plus}`;
        totalMinusSpan.textContent = report.total_minus;
        categoriesContainer.innerHTML = '';

        if (!report.categories.length) {
            categoriesContainer.innerHTML = '<p style="text-align:center;">По этой точке пока нет данных.</p>';
            return;
        }

        report.categories.forEach(cat => {
            const card = document.createElement('div');
            card.className = `category-card status-${cat.status}`;
            let html = `<h3>${cat.name}</h3>`;

            if (cat.status === 'green') {
                html += '<p style="color:#28a745; font-weight:bold;">✅ Расхождений нет</p>';
            } else if (cat.status === 'orange') {
                html += '<p style="color:#fd7e14; font-weight:bold;">⏳ Подкатегория еще в поштучной проверке</p>';
            } else if (cat.problem_items.length > 0) {
                html += '<p style="color:#dc3545; font-weight:bold;">⚠️ Зафиксированы расхождения</p>';
                html += `
                    <table class="admin-table">
                        <tr>
                            <th>Товар</th>
                            <th>План</th>
                            <th>Факт</th>
                            <th>Разница</th>
                        </tr>
                `;
                cat.problem_items.forEach(item => {
                    const diffSign = item.diff > 0 ? '+' : '';
                    const diffColor = item.diff > 0 ? '#28a745' : '#dc3545';
                    html += `
                        <tr>
                            <td>${item.name}</td>
                            <td style="text-align:center;">${item.expected}</td>
                            <td style="text-align:center;">${item.actual}</td>
                            <td style="text-align:center; color:${diffColor}; font-weight:bold;">${diffSign}${item.diff}</td>
                        </tr>
                    `;
                });
                html += '</table>';
            } else {
                html += '<p>Пока нет завершенных проверок по этой категории.</p>';
            }

            card.innerHTML = html;
            categoriesContainer.appendChild(card);
        });
    } catch (error) {
        console.error(error);
        categoriesContainer.innerHTML = '<p style="color:red; text-align:center;">Ошибка загрузки данных</p>';
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    const select = document.getElementById('admin-location-select');
    select.addEventListener('change', () => loadAdminReport(select.value));
    await loadAdminReport(select.value);
});
