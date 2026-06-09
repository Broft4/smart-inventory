(function () {
    'use strict';

    const THEME_KEY = 'uchetka:theme';
    let panelOpen = false;
    let notificationItems = [];
    let hasOpenedNotifications = false;
    let lastUnreadCount = null;
    let notificationSoundContext = null;
    let notificationSoundUnlocked = false;
    let pendingNotificationSound = false;

    function savedTheme() {
        let saved = null;
        try {
            saved = localStorage.getItem(THEME_KEY);
        } catch (error) {
            saved = null;
        }
        // По умолчанию всегда используем светлую тему.
        // Тёмная тема включается только после явного выбора пользователя.
        if (saved === 'dark') return 'dark';
        return 'light';
    }

    function applyTheme(theme) {
        const normalized = theme === 'dark' ? 'dark' : 'light';
        document.documentElement.dataset.theme = normalized;
        try {
            localStorage.setItem(THEME_KEY, normalized);
        } catch (error) {}
        const button = document.getElementById('app-theme-toggle');
        if (button) {
            button.setAttribute('aria-pressed', normalized === 'dark' ? 'true' : 'false');
            button.title = normalized === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему';
            button.innerHTML = normalized === 'dark'
                ? '<span aria-hidden="true">☀️</span><span class="app-control-label">Светлая</span>'
                : '<span aria-hidden="true">🌙</span><span class="app-control-label">Тёмная</span>';
        }
    }

    function apiFetch(url, options) {
        return fetch(url, Object.assign({
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
        }, options || {}));
    }

    function formatNotificationTime(value) {
        if (!value) return '';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return '';
        return parsed.toLocaleString('ru-RU', {
            day: '2-digit',
            month: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function renderNotificationBadge(count) {
        const badge = document.getElementById('app-notification-badge');
        if (!badge) return;
        const total = Number(count || 0);
        badge.textContent = total > 99 ? '99+' : String(total);
        badge.classList.toggle('hidden', total <= 0);
        const button = document.getElementById('app-notification-toggle');
        if (button) {
            button.setAttribute('aria-label', total > 0 ? `Уведомления: ${total} непрочитанных` : 'Уведомления');
        }
    }

    function getNotificationSoundContext() {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return null;
        if (!notificationSoundContext) {
            notificationSoundContext = new AudioContextClass();
        }
        return notificationSoundContext;
    }

    async function unlockNotificationSound() {
        const context = getNotificationSoundContext();
        if (!context) return;
        try {
            if (context.state === 'suspended') {
                await context.resume();
            }
            notificationSoundUnlocked = context.state === 'running';
            if (notificationSoundUnlocked && pendingNotificationSound) {
                pendingNotificationSound = false;
                window.setTimeout(playNotificationSound, 40);
            }
        } catch (error) {
            notificationSoundUnlocked = false;
        }
    }

    function playNotificationSound() {
        const context = getNotificationSoundContext();
        if (!context || !notificationSoundUnlocked || context.state !== 'running') return false;

        const now = context.currentTime;
        const output = context.createGain();
        output.gain.setValueAtTime(0.0001, now);
        output.gain.exponentialRampToValueAtTime(0.16, now + 0.018);
        output.gain.exponentialRampToValueAtTime(0.0001, now + 0.46);
        output.connect(context.destination);

        [0, 0.13].forEach((offset, index) => {
            const oscillator = context.createOscillator();
            oscillator.type = 'sine';
            oscillator.frequency.setValueAtTime(index === 0 ? 880 : 1175, now + offset);
            oscillator.connect(output);
            oscillator.start(now + offset);
            oscillator.stop(now + offset + 0.12);
        });

        window.setTimeout(() => {
            try {
                output.disconnect();
            } catch (error) {}
        }, 700);
        return true;
    }

    function maybePlayNotificationSound(unreadCount) {
        const total = Number(unreadCount || 0);
        if (lastUnreadCount !== null && total > lastUnreadCount && !panelOpen) {
            pendingNotificationSound = !playNotificationSound();
        }
        lastUnreadCount = total;
    }

    function renderNotifications() {
        const list = document.getElementById('app-notification-list');
        if (!list) return;
        if (!notificationItems.length) {
            list.innerHTML = '<div class="app-notification-empty">Новых уведомлений нет.</div>';
            return;
        }
        list.innerHTML = notificationItems.map((item) => {
            const url = item.url ? escapeHtml(item.url) : '';
            const title = escapeHtml(item.title || 'Уведомление');
            const message = escapeHtml(item.message || 'Новое событие в сервисе.');
            const time = escapeHtml(formatNotificationTime(item.created_at));
            const content = `
                <div class="app-notification-item-title">${title}</div>
                <div class="app-notification-item-message">${message}</div>
                ${time ? `<div class="app-notification-item-time">${time}</div>` : ''}
            `;
            if (url) {
                return `<a class="app-notification-item" href="${url}">${content}</a>`;
            }
            return `<div class="app-notification-item">${content}</div>`;
        }).join('');
    }

    async function loadNotifications() {
        const button = document.getElementById('app-notification-toggle');
        if (button) button.classList.add('is-loading');
        try {
            const response = await apiFetch('/api/notifications/unread');
            if (!response.ok) throw new Error('notifications request failed');
            const data = await response.json();
            notificationItems = Array.isArray(data.items) ? data.items : [];
            const unreadCount = data.unread_count || notificationItems.length;
            renderNotificationBadge(unreadCount);
            renderNotifications();
            maybePlayNotificationSound(unreadCount);
        } catch (error) {
            const list = document.getElementById('app-notification-list');
            if (list) list.innerHTML = '<div class="app-notification-empty app-notification-error">Не удалось загрузить уведомления.</div>';
        } finally {
            if (button) button.classList.remove('is-loading');
        }
    }

    async function clearNotifications() {
        try {
            await apiFetch('/api/notifications/read-all', { method: 'POST', body: '{}' });
        } catch (error) {
            return;
        }
        notificationItems = [];
        lastUnreadCount = 0;
        renderNotificationBadge(0);
        renderNotifications();
    }

    function setPanelOpen(nextOpen) {
        const panel = document.getElementById('app-notification-panel');
        const toggle = document.getElementById('app-notification-toggle');
        if (!panel || !toggle) return;
        panelOpen = Boolean(nextOpen);
        panel.classList.toggle('hidden', !panelOpen);
        toggle.setAttribute('aria-expanded', panelOpen ? 'true' : 'false');
        if (panelOpen) {
            hasOpenedNotifications = true;
            loadNotifications();
        } else if (hasOpenedNotifications) {
            hasOpenedNotifications = false;
            clearNotifications();
        }
    }

    function createShellControls() {
        if (document.getElementById('app-shell-controls')) return;

        const controls = document.createElement('div');
        controls.id = 'app-shell-controls';
        controls.className = 'app-shell-controls';
        controls.innerHTML = `
            <button id="app-theme-toggle" class="app-shell-btn app-theme-toggle" type="button" aria-pressed="false"></button>
            <div class="app-notification-wrap">
                <button id="app-notification-toggle" class="app-shell-btn app-notification-toggle" type="button" aria-expanded="false" aria-controls="app-notification-panel" aria-label="Уведомления">
                    <span class="app-bell-icon" aria-hidden="true">🔔</span>
                    <span id="app-notification-badge" class="app-notification-badge hidden">0</span>
                </button>
                <div id="app-notification-panel" class="app-notification-panel hidden" role="dialog" aria-label="Непрочитанные уведомления">
                    <div class="app-notification-panel-head">
                        <strong>Уведомления</strong>
                        <button id="app-notification-close" class="app-notification-close" type="button" aria-label="Закрыть уведомления">×</button>
                    </div>
                    <div id="app-notification-list" class="app-notification-list">
                        <div class="app-notification-empty">Загрузка...</div>
                    </div>
                    <div class="app-notification-panel-note">После закрытия окна непрочитанные уведомления будут очищены.</div>
                </div>
            </div>
        `;
        document.body.appendChild(controls);

        document.getElementById('app-theme-toggle')?.addEventListener('click', () => {
            const current = document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
            applyTheme(current === 'dark' ? 'light' : 'dark');
        });
        document.getElementById('app-notification-toggle')?.addEventListener('click', (event) => {
            event.stopPropagation();
            unlockNotificationSound();
            setPanelOpen(!panelOpen);
        });
        document.getElementById('app-notification-close')?.addEventListener('click', (event) => {
            event.stopPropagation();
            setPanelOpen(false);
        });
        document.getElementById('app-notification-panel')?.addEventListener('click', (event) => {
            event.stopPropagation();
        });
        document.addEventListener('click', () => {
            if (panelOpen) setPanelOpen(false);
        });
        ['pointerdown', 'keydown', 'touchstart'].forEach((eventName) => {
            document.addEventListener(eventName, unlockNotificationSound, { once: true, passive: true });
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && panelOpen) setPanelOpen(false);
        });

        applyTheme(savedTheme());
        loadNotifications();
        setInterval(loadNotifications, 60000);
    }

    applyTheme(savedTheme());
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', createShellControls, { once: true });
    } else {
        createShellControls();
    }
})();
