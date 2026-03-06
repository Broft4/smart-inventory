async function loadReportsList(location) {
    const select = document.getElementById('admin-report-select');
    select.disabled = true;
    select.innerHTML = '<option>Загрузка...</option>';

    const response = await fetch(`/api/reports?location=${encodeURIComponent(location)}`);
    if (!response.ok) throw new Error('Ошибка загрузки списка ревизий');
    const data = await response.json();

    if (!data.reports.length) {
        select.innerHTML = '<option value="">Нет сохраненных ревизий</option>';
        select.disabled = true;
        return null;
    }

    select.innerHTML = data.reports.map(report => (
        `<option value="${report.report_id}">${report.label}</option>`
    )).join('');
    select.disabled = false;
    return Number(select.value);
}

async function loadAdminReport(location, reportId) {
    const locationSpan = document.getElementById('report-location');
    const dateSpan = document.getElementById('report-date');
    const statusSpan = document.getElementById('report-status');
    const idSpan = document.getElementById('report-id');
    const totalPlusSpan = document.getElementById('total-plus');
    const totalMinusSpan = document.getElementById('total-minus');
    const categoriesContainer = document.getElementById('report-categories');

    try {
        const params = new URLSearchParams({ location });
        if (reportId) params.set('report_id', String(reportId));

        const response = await fetch(`/api/report?${params.toString()}`);
        if (!response.ok) throw new Error('Ошибка загрузки отчета');
        const report = await response.json();

        locationSpan.textContent = report.location;
        dateSpan.textContent = report.date;
        statusSpan.textContent = report.status || '-';
        idSpan.textContent = report.report_id ?? '-';
        totalPlusSpan.textContent = `+${report.total_plus}`;
        totalMinusSpan.textContent = report.total_minus;
        categoriesContainer.innerHTML = '';

        if (!report.categories.length) {
            categoriesContainer.innerHTML = '<p style="text-align:center;">По этой ревизии пока нет данных.</p>';
            return;
        }

        report.categories.forEach(cat => {
            const card = document.createElement('div');
            card.className = `category-card status-${cat.status}`;
            let html = `<h3>${cat.name}</h3>`;

            if (cat.status === 'green') {
                html += '<p style="color:#28a745; font-weight:bold;">✅ Расхождений нет</p>';
            } else if (cat.status === 'orange') {
                html += '<p style="color:#fd7e14; font-weight:bold;">⏳ Категория еще проверяется поштучно</p>';
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
                html += '<p>По этой категории пока нет завершенных проверок.</p>';
            }

            card.innerHTML = html;
            categoriesContainer.appendChild(card);
        });
    } catch (error) {
        console.error(error);
        categoriesContainer.innerHTML = '<p style="color:red; text-align:center;">Ошибка загрузки данных</p>';
    }
}

async function reloadAdminPage() {
    const locationSelect = document.getElementById('admin-location-select');
    const reportSelect = document.getElementById('admin-report-select');
    const reportId = await loadReportsList(locationSelect.value);
    await loadAdminReport(locationSelect.value, reportId);

    reportSelect.onchange = async () => {
        const selected = reportSelect.value ? Number(reportSelect.value) : null;
        await loadAdminReport(locationSelect.value, selected);
    };
}

document.addEventListener('DOMContentLoaded', async () => {
    const locationSelect = document.getElementById('admin-location-select');

    locationSelect.addEventListener('change', async () => {
        await reloadAdminPage();
    });

    await reloadAdminPage();
});
