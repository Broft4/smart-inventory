(function () {
    'use strict';

    const SESSION_KEY = 'uchetka:tutorial:session';
    const STARTED_FLAG_KEY = 'uchetka:tutorial:startedAt';
    const USER_ROLE = window.currentUser && window.currentUser.role ? String(window.currentUser.role) : 'employee';
    const IS_MANAGER = USER_ROLE === 'admin' || USER_ROLE === 'superadmin';
    const MOBILE_WIDTH = 760;

    const state = {
        active: false,
        scenarioKey: null,
        pageIndex: 0,
        steps: [],
        index: 0,
        previousTarget: null,
        previousTargetStyle: null,
        actionTarget: null,
        actionHandler: null,
        cleanup: [],
        forcedVisible: [],
        changedFields: []
    };

    const SCENARIOS = {
        employee: {
            startTitle: 'Начать интерактивное обучение?',
            startText: 'Пройдём весь путь сотрудника: ревизия, зарплата и смены. Во время обучения клики безопасны: реальные данные, ревизии и МойСклад не меняются.',
            pages: ['employeeRevision', 'payrollEmployee', 'shiftsEmployee']
        },
        manager: {
            startTitle: 'Начать интерактивное обучение?',
            startText: 'Пройдём сервис управляющего: ревизии, модальные окна, сотрудники, бухгалтерия, расходы, премии, штрафы и смены. Учебные действия не сохраняются в базу.',
            pages: ['managerRevision', 'payrollManager', 'shiftsManager']
        }
    };

    const PAGE_DEFINITIONS = {
        employeeRevision: {
            route: '/',
            steps: [
                {
                    selector: '.page-header',
                    title: 'Страница ревизий сотрудника',
                    text: 'Это основной экран сотрудника. Здесь видны имя, точка и переходы к зарплате и сменам.'
                },
                {
                    selector: '.inventory-topbar',
                    title: 'Текущая ревизия',
                    text: 'В этом блоке сотрудник начинает ревизию, обновляет список задач и завершает работу, когда все выбранные категории проверены.'
                },
                {
                    selector: '#start-revision-btn',
                    title: 'Начать ревизию',
                    text: 'Обычно с этой кнопки начинается работа за день. В обучении этот шаг демонстрируется без создания ревизии и без изменения реальных остатков.',
                    actionSelector: '#start-revision-btn',
                },
                {
                    selector: '.employee-tools-card',
                    title: 'Поиск и быстрые счётчики',
                    text: 'Поиск помогает найти категорию, подкатегорию или товар. Счётчики показывают, сколько задач уже у вас, сколько свободно, занято и завершено.'
                },
                {
                    selector: '#employee-view-switch',
                    title: 'Фильтры задач',
                    text: 'Фильтры помогают быстро переключаться между своими, свободными, занятыми и завершёнными задачами.',
                    actionSelector: '#employee-view-switch .employee-view-btn[data-employee-filter="free"]',
                },
                {
                    selector: '#categories-container',
                    title: 'Карточки категорий и товаров',
                    text: 'Здесь появляются категории, подкатегории и товары для проверки. Сотрудник берёт свободную задачу, вводит фактический остаток и отправляет результат.'
                },
                {
                    selector: '.header-actions a[href="/payroll"]',
                    title: 'Переход в зарплату',
                    text: 'Теперь перейдём на страницу зарплаты и посмотрим, как сотрудник проверяет начисления.',
                    actionSelector: '.header-actions a[href="/payroll"]',
                    navigateTo: '/payroll'
                }
            ]
        },
        managerRevision: {
            route: '/admin',
            steps: [
                {
                    selector: '.page-header',
                    title: 'Страница ревизий управляющего',
                    text: 'Это стартовая страница управляющего: отсюда контролируются ревизии, категории цикла, сотрудники, смены и бухгалтерия.'
                },
                {
                    selector: '.header-actions',
                    title: 'Главные кнопки',
                    text: 'Сначала пройдём кнопки, которые открывают модальные окна, а потом перейдём на другие страницы сервиса.'
                },
                {
                    selector: '#open-cycle-targets-btn',
                    title: 'Категории цикла',
                    text: 'Здесь управляющий выбирает, какие категории и подкатегории должны попасть в текущий цикл ревизии.',
                    actionSelector: '#open-cycle-targets-btn',
                    onAction: 'openCycleTargets'
                },
                {
                    selector: '#cycle-targets-modal .modal-card',
                    title: 'Выбор категорий ревизии',
                    text: 'В этом окне выбирают дату цикла, дату ревизии и отмечают нужные категории. Кнопка «Добавить прошлый выбор» помогает быстро повторить прошлый набор.',
                    onEnter: 'openCycleTargets'
                },
                {
                    selector: '#cycle-targets-search',
                    title: 'Поиск внутри категорий',
                    text: 'Если категорий много, используйте поиск. Он помогает быстро найти нужную категорию или подкатегорию перед сохранением выбора.',
                    onEnter: 'openCycleTargets'
                },
                {
                    selector: '#save-cycle-targets-btn',
                    title: 'Сохранение выбора',
                    text: 'В обычной работе эта кнопка сохраняет набор категорий. В обучении нажатие безопасно и ничего не запишет.',
                    actionSelector: '#save-cycle-targets-btn',
                    onEnter: 'openCycleTargets'
                },
                {
                    selector: '#open-users-btn',
                    title: 'Сотрудники',
                    text: 'Через эту кнопку открывается список сотрудников: можно добавить нового, изменить роль, точку и активность.',
                    actionSelector: '#open-users-btn',
                    onAction: 'openUsers'
                },
                {
                    selector: '#users-modal .modal-card',
                    title: 'Список сотрудников',
                    text: 'Здесь управляющий видит сотрудников, их роли и доступы. В обучении мы добавим сотрудника в демонстрационном режиме: он не попадёт в основную базу.',
                    onEnter: 'openUsers'
                },
                {
                    selector: '#open-create-user-btn',
                    title: 'Добавление сотрудника',
                    text: 'Эта кнопка открывает форму создания сотрудника. В обучении форма заполнится примером, но сохранения в базе не будет.',
                    actionSelector: '#open-create-user-btn',
                    onEnter: 'openUsers',
                    onAction: 'openDemoUserForm'
                },
                {
                    selector: '#user-form-modal .modal-card',
                    title: 'Учебная карточка сотрудника',
                    text: 'Поля заполнены примером. В реальной работе здесь указывают ФИО, логин, email, пароль, роль и точку. После обучения учебный пример будет убран.',
                    onEnter: 'openDemoUserForm'
                },
                {
                    selector: '#user-form button[type="submit"]',
                    title: 'Безопасная проба сохранения',
                    text: 'В режиме обучения сохранение демонстрируется безопасно: запрос не уйдёт на сервер, а в списке появится только временная учебная строка.',
                    actionSelector: '#user-form button[type="submit"]',
                    onEnter: 'openDemoUserForm',
                    onAction: 'fakeSaveEmployee'
                },
                {
                    selector: '#open-create-location-btn',
                    title: 'Точки',
                    text: 'У суперадмина здесь открывается управление точками: название, токен МойСклад и склад. В обучении токены не отправляются и склады не загружаются.',
                    optional: true,
                    actionSelector: '#open-create-location-btn',
                    onAction: 'openLocation'
                },
                {
                    selector: '#location-modal .modal-card',
                    title: 'Настройки точки и МойСклад',
                    text: 'В этом окне важно не ошибиться с токеном и складом. Эти данные отвечают за выгрузку остатков и корректную работу ревизий.',
                    optional: true,
                    onEnter: 'openLocation'
                },
                {
                    selector: '.admin-hero-card',
                    title: 'Выбор точки, месяца и ревизии',
                    text: 'Здесь выбирают точку, год, месяц и конкретную ревизию. Кнопки выгрузки Excel и проблемных товаров используются для анализа результатов.',
                    onEnter: 'closeAllModals'
                },
                {
                    selector: '.admin-period-toolbar',
                    title: 'Отчёт за период',
                    text: 'Можно смотреть не только одну ревизию, но и произвольный период. Это удобно для анализа расхождений за несколько дней.'
                },
                {
                    selector: '.report-summary-v2',
                    title: 'Сводка ревизии',
                    text: 'Здесь собраны главные итоги: статус, излишки, недостачи, сотрудники, завершённые категории и оценка потерь.'
                },
                {
                    selector: '.selected-cycle-card',
                    title: 'Выбранные категории цикла',
                    text: 'Блок показывает, что именно вошло в текущую ревизию и какие категории уже взяты или завершены.'
                },
                {
                    selector: '#report-employees-section',
                    title: 'Сотрудники в ревизии',
                    text: 'Здесь видно, кто участвовал в ревизии, какие задачи выполнял и где возникли расхождения.'
                },
                {
                    selector: '.detail-header-block',
                    title: 'Детализация по товарам',
                    text: 'Поиск, фильтр расхождений и переключение вида помогают быстро найти проблемные позиции по категориям или по сотрудникам.'
                },
                {
                    selector: '.header-actions a[href="/payroll"]',
                    title: 'Переход в бухгалтерию',
                    text: 'Теперь перейдём в бухгалтерию и подробно разберём все её разделы.',
                    actionSelector: '.header-actions a[href="/payroll"]',
                    navigateTo: '/payroll'
                }
            ]
        },
        payrollEmployee: {
            route: '/payroll',
            steps: [
                {
                    selector: '.payroll-filters-card',
                    title: 'Выбор периода зарплаты',
                    text: 'Сотрудник выбирает даты периода и нажимает «Показать». Загрузка начинается только по кнопке, а не при каждом изменении даты.'
                },
                {
                    selector: '#payroll-load-btn',
                    title: 'Показать зарплату',
                    text: 'Эта кнопка загружает расчёт за выбранный период. В обучении запрос не отправляется.',
                    actionSelector: '#payroll-load-btn',
                },
                {
                    selector: '#payroll-summary-card',
                    title: 'Итог за период',
                    text: 'Здесь сотрудник видит смены, выход, бонус, категории, мотивацию, премии, штрафы и сумму к выплате.',
                    onEnter: 'expandTargetDetails'
                },
                {
                    selector: '#employee-shift-details-card',
                    title: 'Детализация моих смен',
                    text: 'В этом разделе можно раскрыть каждую закрытую смену и посмотреть, из чего сложилась сумма.',
                    onEnter: 'showEmployeeShiftDetails'
                },
                {
                    selector: '#hot-products-card',
                    title: 'Горящие товары',
                    text: 'Этот блок показывает товары с повышенной мотивацией. Если товар продан, начисление попадает в зарплату.',
                    onEnter: 'expandTargetDetails'
                },
                {
                    selector: '.header-actions a[href="/shifts"]',
                    title: 'Переход в смены',
                    text: 'Теперь посмотрим календарь смен.',
                    actionSelector: '.header-actions a[href="/shifts"]',
                    navigateTo: '/shifts'
                }
            ]
        },
        payrollManager: {
            route: '/payroll',
            steps: [
                {
                    selector: '.payroll-filters-card',
                    title: 'Фильтры бухгалтерии',
                    text: 'Здесь выбирают точку, сотрудника и период. Расчёт загружается только после нажатия «Показать», чтобы случайная смена даты не запускала лишние запросы.'
                },
                {
                    selector: '#payroll-load-btn',
                    title: 'Кнопка «Показать»',
                    text: 'Эта кнопка запускает загрузку по выбранному периоду. В обучении запрос не отправляется.',
                    actionSelector: '#payroll-load-btn',
                },
                {
                    selector: '#manager-summary-card',
                    title: 'Зарплата управляющего и прибыль точки',
                    text: 'Здесь собраны выручка, возвраты, себестоимость, зарплаты сотрудников, расходы точки, прибыль, зарплата управляющего и чистая прибыль.',
                    onEnter: 'showManagerSummary'
                },
                {
                    selector: '#payroll-summary-card',
                    title: 'Итог за период',
                    text: 'Главная сводка по начислениям: смены, выход, бонус, категории, мотивации, премии, штрафы и сумма к выплате.',
                    onEnter: 'expandTargetDetails'
                },
                {
                    selector: '.payroll-category-filters',
                    title: 'Фильтры категорий в зарплате',
                    text: 'Здесь можно искать категории, показывать только категории с начислением или продажами и менять сортировку.'
                },
                {
                    selector: '#payroll-period-shifts-card',
                    title: 'Детализация смен за период',
                    text: 'Этот раздел показывает закрытые смены за период и помогает проверить начисления по каждой смене отдельно.',
                    onEnter: 'showPeriodShifts'
                },
                {
                    selector: '#admin-settings-card',
                    title: 'Настройки зарплаты точки',
                    text: 'Здесь задаются правила расчёта: выход, порог бонуса, сумма бонуса, процент «Все остальное» и ответственный управляющий. Правила версионные: новые настройки не меняют прошлые закрытые смены.',
                    onEnter: 'showAdminSettings'
                },
                {
                    selector: '#settings-rates-card',
                    title: 'Проценты по категориям',
                    text: 'Для каждой верхней категории можно задать процент продаж и указать, участвует ли категория в расчёте бонуса к выходу.',
                    onEnter: 'showAdminSettings'
                },
                {
                    selector: '#settings-manager-brackets-card',
                    title: 'Пороги зарплаты управляющего',
                    text: 'Для главного управляющего можно задать пороги по чистой прибыли. Порог считается после расходов точки и зарплат сотрудников.',
                    onEnter: 'showManagerBrackets'
                },
                {
                    selector: '#hot-products-card',
                    title: 'Горящие товары',
                    text: 'Здесь видны активные товары с повышенной мотивацией. Сотрудник видит их как подсказку, что стоит предлагать покупателю.',
                    onEnter: 'expandTargetDetails'
                },
                {
                    selector: '#sales-motivations-card',
                    title: 'Мотивации продавцов',
                    text: 'Раздел создаёт отдельные модели мотиваций: процент от продаж или фиксированная сумма за позицию. Модели работают параллельно и суммируются.',
                    onEnter: 'showSalesMotivations'
                },
                {
                    selector: '#sales-motivation-product-query',
                    title: 'Подбор товаров для мотивации',
                    text: 'Можно искать товары вручную или использовать правило “без продаж N дней”. В обучении подбор не обращается к МойСклад.',
                    onEnter: 'showSalesMotivations'
                },
                {
                    selector: '#expenses-card',
                    title: 'Расходы точки',
                    text: 'Здесь ведутся шаблоны расходов и фактические суммы за месяц. Месяц подставляется автоматически из выбранного периода.',
                    onEnter: 'showExpenses'
                },
                {
                    selector: '#expense-entry-tbody',
                    title: 'Расходы за месяц',
                    text: 'В этом списке редактируются фактические расходы: можно растянуть сумму на месяц или списать одним днём. В расчёт попадают оплаченные расходы.',
                    onEnter: 'showExpensesEntries'
                },
                {
                    selector: '#expense-template-tbody',
                    title: 'Шаблоны расходов',
                    text: 'Шаблоны переходят из месяца в месяц. Это удобно для аренды, интернета, уборки и других регулярных трат.',
                    onEnter: 'showExpenses'
                },
                {
                    selector: '#manual-expense-name',
                    title: 'Свободный расход',
                    text: 'Разовый расход добавляется без шаблона: ремонт, курьер, уборка. Его можно привязать к сотруднику и вычесть из зарплаты.',
                    onEnter: 'prepareManualExpense'
                },
                {
                    selector: '#create-manual-expense-btn',
                    title: 'Учебное добавление расхода',
                    text: 'В обучении добавление расхода демонстрируется без записи в базу, а поля будут очищены после завершения.',
                    actionSelector: '#create-manual-expense-btn',
                    onEnter: 'prepareManualExpense'
                },
                {
                    selector: '#employee-bonuses-card',
                    title: 'Премии',
                    text: 'Премия назначается конкретному сотруднику на дату. Она добавляется к зарплате отдельной строкой и учитывается в выбранном периоде.',
                    onEnter: 'showBonuses'
                },
                {
                    selector: '#employee-bonus-amount',
                    title: 'Учебная премия',
                    text: 'В форме можно указать месяц, сотрудника, сумму, дату и комментарий. В обучении пример не сохраняется.',
                    onEnter: 'prepareBonus'
                },
                {
                    selector: '#create-employee-bonus-btn',
                    title: 'Безопасное добавление премии',
                    text: 'Нажатие в обучении не создаёт премию в базе, а только показывает, где выполняется действие.',
                    actionSelector: '#create-employee-bonus-btn',
                    onEnter: 'prepareBonus'
                },
                {
                    selector: '#employee-penalties-card',
                    title: 'Штрафы',
                    text: 'Штраф назначается сотруднику на дату и вычитается из суммы к выплате. Месяц также подставляется из выбранного периода.',
                    onEnter: 'showPenalties'
                },
                {
                    selector: '#employee-penalty-amount',
                    title: 'Учебный штраф',
                    text: 'Заполняются сотрудник, сумма, дата и комментарий. В обучении запись не сохраняется.',
                    onEnter: 'preparePenalty'
                },
                {
                    selector: '#create-employee-penalty-btn',
                    title: 'Безопасное добавление штрафа',
                    text: 'В обучении штраф не создаётся и не влияет на зарплату.',
                    actionSelector: '#create-employee-penalty-btn',
                    onEnter: 'preparePenalty'
                },
                {
                    selector: '#audit-card',
                    title: 'Журнал изменений',
                    text: 'Здесь можно посмотреть, кто и когда менял настройки, расходы, премии, штрафы и другие данные бухгалтерии.',
                    onEnter: 'showAudit'
                },
                {
                    selector: '#closed-shifts-recalc-card',
                    title: 'Пересчёт закрытых смен',
                    text: 'Этот служебный блок нужен, если правила или мотивации нужно пересчитать для уже закрытых смен. Использовать его стоит аккуратно.',
                    onEnter: 'showRecalc'
                },
                {
                    selector: '.header-actions a[href="/shifts"]',
                    title: 'Переход в смены',
                    text: 'Теперь перейдём к календарю смен и назначению смен.',
                    actionSelector: '.header-actions a[href="/shifts"]',
                    navigateTo: '/shifts'
                }
            ]
        },
        shiftsEmployee: {
            route: '/shifts',
            steps: [
                {
                    selector: '.payroll-filters-card',
                    title: 'Выбор месяца смен',
                    text: 'Здесь выбирается месяц календаря. Сотрудник видит свои назначенные, открытые и закрытые смены.'
                },
                {
                    selector: '#shift-load-btn',
                    title: 'Показать месяц',
                    text: 'Кнопка обновляет календарь за выбранный месяц. В обучении запрос не отправляется.',
                    actionSelector: '#shift-load-btn',
                },
                {
                    selector: '#shift-hot-products-card',
                    title: 'Горящие товары на сменах',
                    text: 'Здесь можно посмотреть товары с повышенной мотивацией по выбранной точке.',
                    onEnter: 'expandTargetDetails'
                },
                {
                    selector: '.shift-calendar-scroller',
                    title: 'Календарь смен',
                    text: 'Календарь показывает даты смен и их состояние. На телефоне таблицу можно прокручивать по горизонтали.'
                },
                {
                    selector: '#shift-payroll-section',
                    title: 'Детализация по сменам',
                    text: 'Ниже отображается подробная информация по закрытым сменам и начислениям.',
                    optional: true,
                    onEnter: 'showShiftPayroll'
                }
            ]
        },
        shiftsManager: {
            route: '/shifts',
            steps: [
                {
                    selector: '.payroll-filters-card',
                    title: 'Фильтры календаря смен',
                    text: 'Управляющий выбирает точку, год и месяц, а затем загружает календарь.'
                },
                {
                    selector: '#shift-load-btn',
                    title: 'Показать месяц',
                    text: 'Эта кнопка обновляет календарь смен за выбранный месяц. В обучении запрос не отправляется.',
                    actionSelector: '#shift-load-btn',
                },
                {
                    selector: '#shift-hot-products-card',
                    title: 'Горящие товары',
                    text: 'Управляющий видит активные товары с повышенной мотивацией и может контролировать, что сотрудники их продают.',
                    onEnter: 'expandTargetDetails'
                },
                {
                    selector: '.shift-calendar-scroller',
                    title: 'Календарь смен',
                    text: 'В календаре видно, кто назначен на смену, какие смены открыты, закрыты или требуют внимания.'
                },
                {
                    selector: '#shift-floating-add-btn',
                    title: 'Добавление смены',
                    text: 'Плавающая кнопка открывает модальное окно назначения смены. В обучении смена не будет сохранена.',
                    optional: true,
                    actionSelector: '#shift-floating-add-btn',
                    onAction: 'openShiftModal'
                },
                {
                    selector: '#shift-modal .modal-card',
                    title: 'Учебное назначение смены',
                    text: 'Здесь выбирают дату и сотрудника. В обучении кнопка назначения не создаёт запись в календаре.',
                    optional: true,
                    onEnter: 'openShiftModal'
                },
                {
                    selector: '#shift-modal-save-btn',
                    title: 'Безопасное назначение смены',
                    text: 'В обучении показывается механика сохранения, но запрос на сервер не уйдёт.',
                    optional: true,
                    actionSelector: '#shift-modal-save-btn',
                    onEnter: 'openShiftModal'
                },
                {
                    selector: '#shift-payroll-section',
                    title: 'Детализация зарплаты по сменам',
                    text: 'Ниже можно сверить начисления по закрытым сменам: выход, бонусы, категории, мотивации и итог.',
                    optional: true,
                    onEnter: 'showShiftPayroll'
                }
            ]
        }
    };

    const ACTIONS = {
        openCycleTargets() {
            openModal('#cycle-targets-modal');
            fillValue('#cycle-targets-search', 'пример: жидкости');
        },
        openUsers() {
            closeModal('#cycle-targets-modal');
            openModal('#users-modal');
        },
        openDemoUserForm() {
            closeModal('#cycle-targets-modal');
            openModal('#users-modal');
            openModal('#user-form-modal');
            fillValue('#user-full-name', 'Учебный сотрудник');
            fillValue('#user-birth-date', '2000-01-01');
            fillValue('#user-username', 'demo_employee');
            fillValue('#user-email', 'demo@example.com');
            fillValue('#user-password', 'demo12345');
            setSelectValue('#user-role', 'employee');
            setFirstSelectValue('#user-location');
            setChecked('#user-active', true);
            addDemoNotice('#user-form-modal .modal-card', 'Это учебный сотрудник. Он не сохраняется в основную базу и исчезнет после завершения обучения.');
        },
        fakeSaveEmployee() {
            ACTIONS.openDemoUserForm();
            const list = document.querySelector('#users-list');
            if (list && !list.querySelector('[data-tutorial-demo-user]')) {
                const card = document.createElement('div');
                card.className = 'user-card tutorial-fake-record';
                card.setAttribute('data-tutorial-demo-user', '1');
                card.innerHTML = '<div><strong>Учебный сотрудник</strong><p class="muted-text">Временная запись обучения · не сохранена в БД</p></div>';
                list.prepend(card);
                state.cleanup.push(() => card.remove());
            }
        },
        openLocation() {
            closeModal('#users-modal');
            closeModal('#user-form-modal');
            openModal('#location-modal');
            fillValue('#location-name', 'Учебная точка');
            fillValue('#location-token', 'demo-token-not-saved');
            addDemoNotice('#location-modal .modal-card', 'Учебный режим: токен и склад не отправляются в МойСклад.');
        },
        closeAllModals() {
            closeTutorialModals();
        },
        expandTargetDetails(step) {
            ensureVisible(step && step.selector);
            openClosestDetails(step && step.selector);
        },
        showEmployeeShiftDetails(step) {
            ensureVisible('#employee-shift-details-card');
            openClosestDetails('#employee-shift-details-card');
            addPlaceholder('#employee-shift-details-container', 'После загрузки периода здесь будут закрытые смены сотрудника и подробные начисления.');
        },
        showManagerSummary() {
            ensureVisible('#manager-summary-card');
            openClosestDetails('#manager-summary-card');
        },
        showPeriodShifts() {
            ensureVisible('#payroll-period-shifts-card');
            openClosestDetails('#payroll-period-shifts-card');
            addPlaceholder('#payroll-period-shifts-container', 'После нажатия «Показать» здесь появятся закрытые смены за выбранный период.');
        },
        showAdminSettings() {
            ensureVisible('#admin-settings-card');
            openClosestDetails('#admin-settings-card');
        },
        showManagerBrackets() {
            ensureVisible('#admin-settings-card');
            ensureVisible('#settings-manager-brackets-card');
            openClosestDetails('#admin-settings-card');
            openClosestDetails('#settings-manager-brackets-card');
        },
        showSalesMotivations() {
            ensureVisible('#sales-motivations-card');
            openClosestDetails('#sales-motivations-card');
            fillValue('#sales-motivation-name', 'Учебная мотивация');
            fillValue('#sales-motivation-reward-value', '10');
        },
        showExpenses() {
            ensureVisible('#expenses-card');
            openClosestDetails('#expenses-card');
            fillValue('#expenses-month-input', getCurrentMonthValue());
        },
        showExpensesEntries() {
            ACTIONS.showExpenses();
            ensureVisible('#expense-entry-tbody');
            addPlaceholder('#expense-entry-tbody', 'Здесь появляются фактические расходы за выбранный месяц. Учебные строки не добавляются в базу.');
        },
        prepareManualExpense() {
            ACTIONS.showExpenses();
            fillValue('#manual-expense-name', 'Учебный расход');
            fillValue('#manual-expense-amount', '500');
            fillValue('#manual-expense-date', getTodayIso());
            setChecked('#manual-expense-paid', true);
            addDemoNotice('#expenses-card', 'Учебный расход заполнен только на экране и не будет сохранён.');
        },
        showBonuses() {
            ensureVisible('#employee-bonuses-card');
            openClosestDetails('#employee-bonuses-card');
            fillValue('#employee-bonuses-month-input', getCurrentMonthValue());
        },
        prepareBonus() {
            ACTIONS.showBonuses();
            setFirstSelectValue('#employee-bonus-employee');
            fillValue('#employee-bonus-amount', '500');
            fillValue('#employee-bonus-date', getTodayIso());
            fillValue('#employee-bonus-comment', 'Учебная премия');
            addDemoNotice('#employee-bonuses-card', 'Учебная премия не будет сохранена в базе.');
        },
        showPenalties() {
            ensureVisible('#employee-penalties-card');
            openClosestDetails('#employee-penalties-card');
            fillValue('#employee-penalties-month-input', getCurrentMonthValue());
        },
        preparePenalty() {
            ACTIONS.showPenalties();
            setFirstSelectValue('#employee-penalty-employee');
            fillValue('#employee-penalty-amount', '300');
            fillValue('#employee-penalty-date', getTodayIso());
            fillValue('#employee-penalty-comment', 'Учебный штраф');
            addDemoNotice('#employee-penalties-card', 'Учебный штраф не будет сохранён в базе.');
        },
        showAudit() {
            ensureVisible('#audit-card');
            addPlaceholder('#audit-log-list', 'После изменений здесь отображается журнал действий по датам и сотрудникам.');
        },
        showRecalc() {
            ensureVisible('#closed-shifts-recalc-card');
            fillValue('#closed-shifts-recalc-date-from', getTodayIso());
            fillValue('#closed-shifts-recalc-date-to', getTodayIso());
        },
        openShiftModal() {
            openModal('#shift-modal');
            fillValue('#shift-modal-date-input', getTodayIso());
            setFirstSelectValue('#shift-modal-employee-select');
            addDemoNotice('#shift-modal .modal-card', 'Учебная смена не будет назначена и не попадёт в календарь.');
        },
        showShiftPayroll() {
            ensureVisible('#shift-payroll-section');
        }
    };

    function detectPageKey() {
        const path = window.location.pathname || '/';
        if (document.querySelector('.employee-container')) return 'employeeRevision';
        if (path.indexOf('/admin') === 0 || document.querySelector('.admin-v2')) return 'managerRevision';
        if (path.indexOf('/payroll') === 0) return IS_MANAGER ? 'payrollManager' : 'payrollEmployee';
        if (path.indexOf('/shifts') === 0) return IS_MANAGER ? 'shiftsManager' : 'shiftsEmployee';
        return null;
    }

    function scenarioForCurrentPage() {
        const pageKey = detectPageKey();
        if (pageKey === 'managerRevision') return 'manager';
        if (pageKey === 'employeeRevision') return 'employee';
        return null;
    }

    function canShowLauncher() {
        const pageKey = detectPageKey();
        return pageKey === 'managerRevision' || pageKey === 'employeeRevision';
    }

    function safeParse(value) {
        try {
            return value ? JSON.parse(value) : null;
        } catch (error) {
            return null;
        }
    }

    function readSession() {
        const session = safeParse(localStorage.getItem(SESSION_KEY));
        if (!session || !SCENARIOS[session.scenarioKey]) return null;
        const startedAt = Number(session.startedAt || 0);
        const maxAgeMs = 6 * 60 * 60 * 1000;
        if (!Number.isFinite(startedAt) || startedAt <= 0 || Date.now() - startedAt > maxAgeMs) {
            clearSession();
            return null;
        }
        return session;
    }

    function writeSession(session) {
        localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    }

    function clearSession() {
        localStorage.removeItem(SESSION_KEY);
    }

    function createLauncher() {
        if (!canShowLauncher() || document.getElementById('tutorial-launcher')) return;
        const button = document.createElement('button');
        button.id = 'tutorial-launcher';
        button.type = 'button';
        button.className = 'tutorial-launcher';
        button.setAttribute('aria-label', 'Интерактивное обучение');
        button.title = 'Интерактивное обучение';
        button.setAttribute('data-tooltip', 'Интерактивное обучение');
        button.innerHTML = '<span aria-hidden="true">?</span>';
        button.addEventListener('click', openStartModal);
        document.body.appendChild(button);
    }

    function ensureStartModal() {
        let overlay = document.getElementById('tutorial-start-modal');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'tutorial-start-modal';
        overlay.className = 'modal-overlay tutorial-start-modal hidden';
        overlay.innerHTML = [
            '<div class="modal-card tutorial-start-card" role="dialog" aria-modal="true" aria-labelledby="tutorial-start-title">',
            '  <div class="modal-header">',
            '    <div>',
            '      <h3 id="tutorial-start-title"></h3>',
            '      <p id="tutorial-start-text" class="muted-text"></p>',
            '    </div>',
            '    <button type="button" class="modal-close-btn tutorial-modal-close" aria-label="Закрыть">×</button>',
            '  </div>',
            '  <div class="tutorial-start-note">Обучение можно закрыть в любой момент. Действия внутри него перехватываются и не изменяют реальные данные.</div>',
            '  <div class="modal-actions tutorial-start-actions">',
            '    <button type="button" class="btn secondary btn-inline" data-tutorial-cancel>Не сейчас</button>',
            '    <button type="button" class="btn primary btn-inline" data-tutorial-start>Начать обучение</button>',
            '  </div>',
            '</div>'
        ].join('');
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay || event.target.closest('[data-tutorial-cancel]') || event.target.closest('.tutorial-modal-close')) {
                closeStartModal();
                return;
            }
            if (event.target.closest('[data-tutorial-start]')) {
                const scenarioKey = scenarioForCurrentPage();
                closeStartModal();
                if (scenarioKey) startScenario(scenarioKey, 0);
            }
        });
        document.body.appendChild(overlay);
        return overlay;
    }

    function openStartModal() {
        const scenarioKey = scenarioForCurrentPage();
        if (!scenarioKey) return;
        const scenario = SCENARIOS[scenarioKey];
        const overlay = ensureStartModal();
        overlay.querySelector('#tutorial-start-title').textContent = scenario.startTitle;
        overlay.querySelector('#tutorial-start-text').textContent = scenario.startText;
        overlay.classList.remove('hidden');
    }

    function closeStartModal() {
        const overlay = document.getElementById('tutorial-start-modal');
        if (overlay) overlay.classList.add('hidden');
    }

    function ensureTourDom() {
        if (document.getElementById('tutorial-spotlight')) return;
        const spotlight = document.createElement('div');
        spotlight.id = 'tutorial-spotlight';
        spotlight.className = 'tutorial-spotlight hidden';
        spotlight.setAttribute('aria-hidden', 'true');

        const popover = document.createElement('div');
        popover.id = 'tutorial-popover';
        popover.className = 'tutorial-popover hidden';
        popover.innerHTML = [
            '<div class="tutorial-popover-head">',
            '  <span class="tutorial-step-counter"></span>',
            '  <button type="button" class="tutorial-close-btn" aria-label="Закрыть обучение">×</button>',
            '</div>',
            '<h3 class="tutorial-title"></h3>',
            '<p class="tutorial-text"></p>',
            '<div class="tutorial-actions">',
            '  <button type="button" class="btn secondary btn-inline" data-tutorial-skip>Закрыть</button>',
            '  <button type="button" class="btn primary btn-inline" data-tutorial-next>Далее</button>',
            '</div>'
        ].join('');
        popover.addEventListener('click', (event) => {
            if (event.target.closest('.tutorial-close-btn') || event.target.closest('[data-tutorial-skip]')) finishTour(true);
            if (event.target.closest('[data-tutorial-next]')) nextStep();
        });
        popover.addEventListener('wheel', (event) => event.stopPropagation(), { passive: true });
        popover.addEventListener('touchmove', (event) => event.stopPropagation(), { passive: true });

        document.body.appendChild(spotlight);
        document.body.appendChild(popover);
        window.addEventListener('resize', () => state.active && renderStep(false));
        window.addEventListener('scroll', () => state.active && positionTour(), true);
        document.addEventListener('keydown', (event) => {
            if (!state.active) return;
            if (event.key === 'Escape') finishTour(true);
            if (event.key === 'ArrowRight') nextStep();
        });
        document.addEventListener('submit', preventUnsafeSubmit, true);
        document.addEventListener('click', preventUnsafeClick, true);
    }

    function startScenario(scenarioKey, pageIndex) {
        const scenario = SCENARIOS[scenarioKey];
        if (!scenario) return;
        localStorage.setItem(STARTED_FLAG_KEY, String(Date.now()));
        writeSession({ scenarioKey, pageIndex, startedAt: Date.now() });
        startCurrentPage({ scenarioKey, pageIndex });
    }

    function startCurrentPage(session) {
        const scenario = SCENARIOS[session.scenarioKey];
        const expectedPageKey = scenario && scenario.pages[session.pageIndex];
        const currentPageKey = detectPageKey();
        if (!scenario || !expectedPageKey) {
            finishTour(true);
            return;
        }
        if (expectedPageKey !== currentPageKey) {
            clearSession();
            return;
        }
        const pageDefinition = PAGE_DEFINITIONS[expectedPageKey];
        ensureTourDom();
        state.active = true;
        state.scenarioKey = session.scenarioKey;
        state.pageIndex = session.pageIndex;
        state.index = 0;
        state.steps = buildVisibleSteps(pageDefinition.steps);
        state.previousTarget = null;
        state.actionTarget = null;
        state.actionHandler = null;
        state.cleanup = [];
        state.forcedVisible = [];
        state.changedFields = [];
        document.body.classList.add('tutorial-active');
        renderStep(true);
    }

    function buildVisibleSteps(steps) {
        return steps.filter((step) => {
            if (!step.optional) return true;
            return !!document.querySelector(step.selector) || !!(step.actionSelector && document.querySelector(step.actionSelector));
        });
    }

    function runStepAction(name, step) {
        if (!name || !ACTIONS[name]) return;
        ACTIONS[name](step);
    }

    function findTarget(step, allowPrepare) {
        if (!step || !step.selector) return null;
        if (allowPrepare && step.onEnter) runStepAction(step.onEnter, step);
        let target = document.querySelector(step.selector);
        if (target && isVisible(target)) return target;
        if (allowPrepare) {
            ensureVisible(step.selector);
            openClosestDetails(step.selector);
            target = document.querySelector(step.selector);
            if (target && isVisible(target)) return target;
        }
        return target && step.optional ? null : target;
    }

    function isVisible(el) {
        if (!el) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    }

    function cleanupTarget() {
        if (state.actionTarget && state.actionHandler) {
            state.actionTarget.removeEventListener('click', state.actionHandler, true);
            state.actionTarget = null;
            state.actionHandler = null;
        }
        if (state.previousTarget) {
            state.previousTarget.classList.remove('tutorial-current-target');
            if (state.previousTargetStyle) {
                state.previousTarget.style.position = state.previousTargetStyle.position;
                state.previousTarget.style.zIndex = state.previousTargetStyle.zIndex;
            }
            state.previousTarget = null;
            state.previousTargetStyle = null;
        }
    }

    function renderStep(shouldScroll) {
        if (!state.active) return;
        cleanupTarget();
        closeTutorialModals();
        const step = state.steps[state.index];
        if (!step) {
            finishTour(false);
            return;
        }
        const target = findTarget(step, true);
        if (!target || !isVisible(target)) {
            nextStep();
            return;
        }
        step.target = target;
        if (shouldScroll !== false) scrollTargetIntoView(target);
        setTimeout(() => {
            if (!state.active) return;
            markTarget(target);
            renderPopover(step);
            positionTour();
        }, shouldScroll === false ? 20 : 240);
    }

    function scrollTargetIntoView(target) {
        const mobile = window.innerWidth <= MOBILE_WIDTH;
        target.scrollIntoView({ behavior: 'smooth', block: mobile ? 'center' : 'center', inline: 'center' });
    }

    function markTarget(target) {
        state.previousTarget = target;
        state.previousTargetStyle = {
            position: target.style.position,
            zIndex: target.style.zIndex
        };
        const computedPosition = window.getComputedStyle(target).position;
        if (computedPosition === 'static') target.style.position = 'relative';
        target.style.zIndex = '10008';
        target.classList.add('tutorial-current-target');
        document.getElementById('tutorial-spotlight').classList.remove('hidden');
        document.getElementById('tutorial-popover').classList.remove('hidden');
    }

    function renderPopover(step) {
        const popover = document.getElementById('tutorial-popover');
        const pageProgress = getPageProgressText();
        popover.querySelector('.tutorial-step-counter').textContent = `${pageProgress} · шаг ${state.index + 1} из ${state.steps.length}`;
        popover.querySelector('.tutorial-title').textContent = step.title;
        popover.querySelector('.tutorial-text').textContent = step.text;
        const nextBtn = popover.querySelector('[data-tutorial-next]');
        if (nextBtn) nextBtn.textContent = isLastStepOfScenario() ? 'Готово' : (step.navigateTo ? 'Переход в раздел' : 'Далее');
    }

    function getPageProgressText() {
        const scenario = SCENARIOS[state.scenarioKey];
        if (!scenario) return 'Обучение';
        return `Экран ${state.pageIndex + 1} из ${scenario.pages.length}`;
    }

    function isLastStepOfScenario() {
        const scenario = SCENARIOS[state.scenarioKey];
        return scenario && state.pageIndex >= scenario.pages.length - 1 && state.index >= state.steps.length - 1;
    }

    function attachActionHandler(step) {
        if (!step.actionSelector) return;
        const actionTarget = document.querySelector(step.actionSelector);
        if (!isVisible(actionTarget)) return;
        state.actionTarget = actionTarget;
        state.actionHandler = function (event) {
            event.preventDefault();
            event.stopPropagation();
            actionTarget.classList.add('tutorial-click-feedback');
            setTimeout(() => actionTarget.classList.remove('tutorial-click-feedback'), 260);
            if (step.onAction) runStepAction(step.onAction, step);
            setTimeout(() => {
                if (step.navigateTo) {
                    goToNextPage(step.navigateTo);
                } else {
                    nextStep();
                }
            }, 180);
        };
        actionTarget.addEventListener('click', state.actionHandler, true);
    }

    function positionTour() {
        if (!state.active) return;
        const step = state.steps[state.index];
        const target = step && step.target;
        const spotlight = document.getElementById('tutorial-spotlight');
        const popover = document.getElementById('tutorial-popover');
        if (!target || !spotlight || !popover) return;

        const rect = target.getBoundingClientRect();
        const padding = window.innerWidth <= MOBILE_WIDTH ? 7 : 10;
        const top = Math.max(8, rect.top - padding);
        const left = Math.max(8, rect.left - padding);
        const width = Math.min(window.innerWidth - 16, Math.max(44, rect.width + padding * 2));
        const height = Math.min(window.innerHeight - 16, Math.max(40, rect.height + padding * 2));
        spotlight.style.top = `${top}px`;
        spotlight.style.left = `${left}px`;
        spotlight.style.width = `${width}px`;
        spotlight.style.height = `${height}px`;

        if (window.innerWidth <= MOBILE_WIDTH) {
            positionPopoverMobile(rect, popover);
        } else {
            positionPopoverDesktop(rect, popover);
        }
    }

    function positionPopoverDesktop(rect, popover) {
        const gap = 16;
        const popoverRect = popover.getBoundingClientRect();
        const spaceBelow = window.innerHeight - rect.bottom;
        const spaceAbove = rect.top;
        let top;
        if (spaceBelow >= popoverRect.height + gap || spaceBelow >= spaceAbove) {
            top = Math.min(window.innerHeight - popoverRect.height - 12, rect.bottom + gap);
        } else {
            top = Math.max(12, rect.top - popoverRect.height - gap);
        }
        let left = rect.left;
        if (left + popoverRect.width > window.innerWidth - 12) left = window.innerWidth - popoverRect.width - 12;
        if (left < 12) left = 12;
        popover.style.top = `${top}px`;
        popover.style.left = `${left}px`;
        popover.style.bottom = 'auto';
    }

    function positionPopoverMobile(rect, popover) {
        const useTop = rect.top > window.innerHeight * 0.42;
        popover.style.left = '10px';
        popover.style.right = '10px';
        popover.style.width = 'auto';
        if (useTop) {
            popover.style.top = '10px';
            popover.style.bottom = 'auto';
        } else {
            popover.style.top = 'auto';
            popover.style.bottom = '10px';
        }
    }

    function nextStep() {
        if (!state.active) return;
        const currentStep = state.steps[state.index];
        if (currentStep && currentStep.onAction && !currentStep.navigateTo) {
            runStepAction(currentStep.onAction, currentStep);
        }
        if (currentStep && currentStep.navigateTo) {
            goToNextPage(currentStep.navigateTo);
            return;
        }
        if (state.index < state.steps.length - 1) {
            state.index += 1;
            renderStep(true);
            return;
        }
        goToNextScenarioPageOrFinish();
    }

    function previousStep() {
        if (!state.active || state.index <= 0) return;
        state.index -= 1;
        renderStep(true);
    }

    function goToNextScenarioPageOrFinish() {
        const scenario = SCENARIOS[state.scenarioKey];
        if (!scenario || state.pageIndex >= scenario.pages.length - 1) {
            finishTour(true);
            return;
        }
        const nextPageIndex = state.pageIndex + 1;
        const nextPageKey = scenario.pages[nextPageIndex];
        const page = PAGE_DEFINITIONS[nextPageKey];
        if (!page || !page.route) {
            finishTour(true);
            return;
        }
        writeSession({ scenarioKey: state.scenarioKey, pageIndex: nextPageIndex, startedAt: Date.now() });
        window.location.href = page.route;
    }

    function goToNextPage(route) {
        const scenario = SCENARIOS[state.scenarioKey];
        const nextPageIndex = state.pageIndex + 1;
        if (!scenario || nextPageIndex >= scenario.pages.length) {
            finishTour(true);
            return;
        }
        writeSession({ scenarioKey: state.scenarioKey, pageIndex: nextPageIndex, startedAt: Date.now() });
        window.location.href = route;
    }

    function finishTour(clear) {
        cleanupTarget();
        restoreForcedVisible();
        restoreChangedFields();
        runCleanups();
        closeTutorialModals();
        const spotlight = document.getElementById('tutorial-spotlight');
        const popover = document.getElementById('tutorial-popover');
        if (spotlight) spotlight.classList.add('hidden');
        if (popover) popover.classList.add('hidden');
        document.body.classList.remove('tutorial-active');
        state.active = false;
        if (clear) clearSession();
    }

    function preventUnsafeSubmit(event) {
        if (!state.active) return;
        event.preventDefault();
        event.stopPropagation();
    }

    function preventUnsafeClick(event) {
        if (!state.active) return;
        if (event.target.closest('#tutorial-popover') || event.target.closest('#tutorial-start-modal')) return;
        if (state.actionTarget && (event.target === state.actionTarget || state.actionTarget.contains(event.target))) return;
        const risky = event.target.closest('button, a, input[type="submit"], input[type="button"]');
        if (!risky) return;
        const target = state.steps[state.index] && state.steps[state.index].target;
        if (target && (risky === target || target.contains(risky))) {
            event.preventDefault();
            event.stopPropagation();
        }
    }

    function openModal(selector) {
        const modal = document.querySelector(selector);
        if (!modal) return;
        modal.classList.remove('hidden');
        modal.classList.add('tutorial-modal-open');
        if (!state.cleanup.includes(closeTutorialModals)) state.cleanup.push(closeTutorialModals);
    }

    function closeModal(selector) {
        const modal = document.querySelector(selector);
        if (!modal) return;
        modal.classList.add('hidden');
        modal.classList.remove('tutorial-modal-open');
    }

    function closeTutorialModals() {
        document.querySelectorAll('.tutorial-modal-open').forEach((modal) => {
            modal.classList.add('hidden');
            modal.classList.remove('tutorial-modal-open');
        });
    }

    function ensureVisible(selector) {
        if (!selector) return;
        const el = document.querySelector(selector);
        if (!el) return;
        let node = el;
        while (node && node !== document.body) {
            if (node.classList && node.classList.contains('hidden')) {
                node.classList.remove('hidden');
                node.classList.add('tutorial-forced-visible');
                state.forcedVisible.push(node);
            }
            node = node.parentElement;
        }
    }

    function restoreForcedVisible() {
        state.forcedVisible.forEach((el) => {
            if (el && el.classList) {
                el.classList.add('hidden');
                el.classList.remove('tutorial-forced-visible');
            }
        });
        state.forcedVisible = [];
    }

    function openClosestDetails(selector) {
        const el = document.querySelector(selector);
        if (!el) return;
        const details = el.matches('details') ? el : el.closest('details');
        if (details) details.open = true;
        el.querySelectorAll && el.querySelectorAll('details').forEach((item) => { item.open = true; });
    }

    function fillValue(selector, value) {
        const el = document.querySelector(selector);
        if (!el) return;
        rememberField(el);
        el.value = value;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function setChecked(selector, checked) {
        const el = document.querySelector(selector);
        if (!el) return;
        rememberField(el);
        el.checked = !!checked;
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function setSelectValue(selector, value) {
        const el = document.querySelector(selector);
        if (!el) return;
        rememberField(el);
        const option = Array.from(el.options || []).find((item) => item.value === value);
        if (option) el.value = value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function setFirstSelectValue(selector) {
        const el = document.querySelector(selector);
        if (!el || !el.options || !el.options.length) return;
        rememberField(el);
        const first = Array.from(el.options).find((option) => option.value !== '');
        if (first) el.value = first.value;
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function rememberField(el) {
        if (!el || state.changedFields.some((item) => item.el === el)) return;
        state.changedFields.push({ el, value: el.value, checked: el.checked });
    }

    function restoreChangedFields() {
        state.changedFields.forEach((item) => {
            if (!item.el) return;
            if ('checked' in item.el) item.el.checked = item.checked;
            item.el.value = item.value;
        });
        state.changedFields = [];
    }

    function addDemoNotice(selector, text) {
        const container = document.querySelector(selector);
        if (!container || container.querySelector(':scope > .tutorial-demo-notice')) return;
        const note = document.createElement('div');
        note.className = 'tutorial-demo-notice';
        note.textContent = text;
        container.prepend(note);
        state.cleanup.push(() => note.remove());
    }

    function addPlaceholder(selector, text) {
        const container = document.querySelector(selector);
        if (!container || container.querySelector(':scope > .tutorial-placeholder')) return;
        ensureVisible(selector);
        const note = document.createElement('div');
        note.className = 'tutorial-placeholder muted-text';
        note.textContent = text;
        container.appendChild(note);
        state.cleanup.push(() => note.remove());
    }

    function runCleanups() {
        state.cleanup.forEach((fn) => {
            try { fn(); } catch (error) { /* ignore cleanup errors */ }
        });
        state.cleanup = [];
    }

    function getTodayIso() {
        const date = new Date();
        const year = date.getFullYear();
        const month = String(date.getMonth() + 1).padStart(2, '0');
        const day = String(date.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function getCurrentMonthValue() {
        return getTodayIso().slice(0, 7);
    }

    document.addEventListener('DOMContentLoaded', () => {
        createLauncher();
        const session = readSession();
        if (session) {
            setTimeout(() => startCurrentPage(session), 350);
        }
    });
})();
