document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('login-form');
    const message = document.getElementById('login-message');
    const submitButton = form.querySelector('button[type="submit"]');

    const resetModal = document.getElementById('password-reset-modal');
    const openResetButton = document.getElementById('open-password-reset-btn');
    const closeResetButton = document.getElementById('close-password-reset-modal-btn');
    const resetRequestForm = document.getElementById('password-reset-request-form');
    const resetVerifyForm = document.getElementById('password-reset-verify-form');
    const resetCompleteForm = document.getElementById('password-reset-complete-form');
    const resetBackButton = document.getElementById('password-reset-back-btn');
    const resetMessage = document.getElementById('password-reset-message');
    const registrationModal = document.getElementById('registration-modal');
    const openRegistrationButton = document.getElementById('open-registration-btn');
    const closeRegistrationButton = document.getElementById('close-registration-modal-btn');
    const registrationForm = document.getElementById('registration-form');
    const registrationMessage = document.getElementById('registration-message');
    const registrationReferralTokenInput = document.getElementById('registration-referral-token');
    const registrationReferralNote = document.getElementById('registration-referral-note');
    const initialParams = new URLSearchParams(window.location.search);
    const initialReferralToken = (initialParams.get('ref') || '').trim();

    const resetState = {
        requestId: '',
        resetToken: '',
    };

    function setButtonLoading(button, isLoading, loadingText, defaultText) {
        if (!button) return;
        button.disabled = Boolean(isLoading);
        button.textContent = isLoading ? loadingText : defaultText;
    }

    function setResetMessage(text, success = false) {
        resetMessage.textContent = text || '';
        resetMessage.style.color = success ? '#1f9d55' : '#dc3545';
    }


    function setRegistrationMessage(text, success = false) {
        if (!registrationMessage) return;
        registrationMessage.textContent = text || '';
        registrationMessage.style.color = success ? '#1f9d55' : '#dc3545';
    }

    function showResetStep(step) {
        resetRequestForm.classList.toggle('hidden', step !== 'request');
        resetVerifyForm.classList.toggle('hidden', step !== 'verify');
        resetCompleteForm.classList.toggle('hidden', step !== 'complete');
    }

    function openResetModal() {
        resetState.requestId = '';
        resetState.resetToken = '';
        resetRequestForm.reset();
        resetVerifyForm.reset();
        resetCompleteForm.reset();
        setResetMessage('');
        showResetStep('request');
        resetModal.classList.remove('hidden');
        resetModal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        setTimeout(() => document.getElementById('reset-email')?.focus(), 50);
    }

    function closeResetModal() {
        resetModal.classList.add('hidden');
        resetModal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('modal-open');
    }


    function setReferralToken(token, referrerName = '') {
        const normalized = String(token || '').trim();
        if (registrationReferralTokenInput) registrationReferralTokenInput.value = normalized;
        if (!registrationReferralNote) return;
        if (!normalized) {
            registrationReferralNote.classList.add('hidden');
            registrationReferralNote.textContent = '';
            return;
        }
        registrationReferralNote.classList.remove('hidden');
        registrationReferralNote.textContent = referrerName
            ? `Вы регистрируетесь по реферальной ссылке: ${referrerName}.`
            : 'Вы регистрируетесь по реферальной ссылке.';
    }

    async function resolveInitialReferralToken() {
        if (!initialReferralToken) return;
        setReferralToken(initialReferralToken);
        try {
            const response = await fetch(`/api/referrals/resolve/${encodeURIComponent(initialReferralToken)}`);
            const data = await response.json();
            if (response.ok && data.found) {
                setReferralToken(initialReferralToken, data.referrer_name || '');
            } else {
                setRegistrationMessage('Реферальная ссылка не найдена или уже отключена. Можно отправить обычную заявку.');
                setReferralToken('');
            }
        } catch (error) {
            console.error(error);
            setReferralToken(initialReferralToken);
        }
    }

    function openRegistrationModal({ preserveReferral = true } = {}) {
        const referralToken = preserveReferral ? (registrationReferralTokenInput?.value || initialReferralToken || '') : '';
        registrationForm?.reset();
        setRegistrationMessage('');
        setReferralToken(referralToken);
        registrationModal?.classList.remove('hidden');
        registrationModal?.setAttribute('aria-hidden', 'false');
        document.body.classList.add('modal-open');
        setTimeout(() => document.getElementById('registration-full-name')?.focus(), 50);
    }

    function closeRegistrationModal() {
        registrationModal?.classList.add('hidden');
        registrationModal?.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('modal-open');
    }

    async function parseResponse(response, fallbackMessage) {
        try {
            const data = await response.json();
            if (!response.ok) {
                if (Array.isArray(data.detail)) return { ok: false, message: data.detail.map(item => item.msg).join(', ') };
                return { ok: false, message: data.detail || data.message || fallbackMessage };
            }
            return { ok: true, data };
        } catch {
            return { ok: false, message: fallbackMessage };
        }
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        message.textContent = 'Выполняем вход...';
        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = 'Входим...';
        }

        try {
            const response = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: document.getElementById('username').value.trim(),
                    password: document.getElementById('password').value,
                }),
            });

            const data = await response.json();
            if (!response.ok || !data.success) {
                message.textContent = data.detail || data.message || 'Не удалось войти.';
                return;
            }

            message.textContent = 'Вход выполнен. Переходим на страницу ревизии...';
            location.href = data.redirect_to || '/';
        } catch (error) {
            console.error(error);
            message.textContent = 'Ошибка сервера при входе.';
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = 'Войти';
            }
        }
    });

    openResetButton?.addEventListener('click', openResetModal);
    closeResetButton?.addEventListener('click', closeResetModal);
    resetBackButton?.addEventListener('click', () => {
        resetState.requestId = '';
        resetState.resetToken = '';
        resetVerifyForm.reset();
        resetCompleteForm.reset();
        setResetMessage('');
        showResetStep('request');
    });

    resetModal?.addEventListener('click', (event) => {
        if (event.target === resetModal) closeResetModal();
    });


    openRegistrationButton?.addEventListener('click', openRegistrationModal);
    closeRegistrationButton?.addEventListener('click', closeRegistrationModal);
    registrationModal?.addEventListener('click', (event) => {
        if (event.target === registrationModal) closeRegistrationModal();
    });

    if (initialReferralToken || initialParams.get('register') === '1') {
        openRegistrationModal({ preserveReferral: true });
        resolveInitialReferralToken();
    }

    registrationForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const password = document.getElementById('registration-password').value;
        const passwordConfirm = document.getElementById('registration-password-confirm').value;
        if (password !== passwordConfirm) {
            setRegistrationMessage('Пароли не совпадают.');
            return;
        }

        const button = registrationForm.querySelector('button[type="submit"]');
        setButtonLoading(button, true, 'Отправляем...', 'Отправить заявку');
        setRegistrationMessage('');

        try {
            const response = await fetch('/api/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    full_name: document.getElementById('registration-full-name').value.trim(),
                    organization_name: document.getElementById('registration-organization').value.trim(),
                    location_name: document.getElementById('registration-location').value.trim(),
                    email: document.getElementById('registration-email').value.trim(),
                    phone: document.getElementById('registration-phone').value.trim() || null,
                    username: document.getElementById('registration-username').value.trim(),
                    password,
                    password_confirm: passwordConfirm,
                    comment: document.getElementById('registration-comment').value.trim() || null,
                    referral_token: registrationReferralTokenInput?.value.trim() || null,
                }),
            });
            const result = await parseResponse(response, 'Не удалось отправить заявку.');
            if (!result.ok) {
                setRegistrationMessage(result.message);
                return;
            }
            setRegistrationMessage(result.data.message || 'Заявка отправлена.', true);
            registrationForm.reset();
        } catch (error) {
            console.error(error);
            setRegistrationMessage('Ошибка сервера при отправке заявки.');
        } finally {
            setButtonLoading(button, false, 'Отправляем...', 'Отправить заявку');
        }
    });

    resetRequestForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const button = resetRequestForm.querySelector('button[type="submit"]');
        setButtonLoading(button, true, 'Отправляем...', 'Отправить код');
        setResetMessage('');

        try {
            const response = await fetch('/api/auth/password-reset/request', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    email: document.getElementById('reset-email').value.trim(),
                }),
            });
            const result = await parseResponse(response, 'Не удалось отправить код. Проверьте email.');
            if (!result.ok) {
                setResetMessage(result.message);
                return;
            }
            resetState.requestId = result.data.request_id || '';
            setResetMessage(result.data.message || 'Код восстановления отправлен на указанную почту.', true);
            showResetStep('verify');
            setTimeout(() => document.getElementById('reset-code')?.focus(), 50);
        } catch (error) {
            console.error(error);
            setResetMessage('Ошибка сервера при отправке кода.');
        } finally {
            setButtonLoading(button, false, 'Отправляем...', 'Отправить код');
        }
    });

    resetVerifyForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const button = resetVerifyForm.querySelector('button[type="submit"]');
        setButtonLoading(button, true, 'Проверяем...', 'Проверить код');
        setResetMessage('');

        try {
            const response = await fetch('/api/auth/password-reset/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    request_id: resetState.requestId,
                    code: document.getElementById('reset-code').value.trim(),
                }),
            });
            const result = await parseResponse(response, 'Не удалось проверить код.');
            if (!result.ok) {
                setResetMessage(result.message);
                return;
            }
            resetState.resetToken = result.data.reset_token || '';
            setResetMessage(result.data.message || 'Код подтверждён.', true);
            showResetStep('complete');
            setTimeout(() => document.getElementById('reset-new-password')?.focus(), 50);
        } catch (error) {
            console.error(error);
            setResetMessage('Ошибка сервера при проверке кода.');
        } finally {
            setButtonLoading(button, false, 'Проверяем...', 'Проверить код');
        }
    });

    resetCompleteForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const newPassword = document.getElementById('reset-new-password').value;
        const confirmPassword = document.getElementById('reset-new-password-confirm').value;
        if (newPassword !== confirmPassword) {
            setResetMessage('Пароли не совпадают.');
            return;
        }

        const button = resetCompleteForm.querySelector('button[type="submit"]');
        setButtonLoading(button, true, 'Сохраняем...', 'Сохранить пароль');
        setResetMessage('');

        try {
            const response = await fetch('/api/auth/password-reset/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    reset_token: resetState.resetToken,
                    password: newPassword,
                    password_confirm: confirmPassword,
                }),
            });
            const result = await parseResponse(response, 'Не удалось сохранить новый пароль.');
            if (!result.ok) {
                setResetMessage(result.message);
                return;
            }
            setResetMessage(result.data.message || 'Пароль изменён. Теперь можно войти.', true);
            setTimeout(() => {
                closeResetModal();
                document.getElementById('password').value = '';
                document.getElementById('password')?.focus();
                message.textContent = 'Пароль изменён. Войдите с новым паролем.';
            }, 900);
        } catch (error) {
            console.error(error);
            setResetMessage('Ошибка сервера при сохранении пароля.');
        } finally {
            setButtonLoading(button, false, 'Сохраняем...', 'Сохранить пароль');
        }
    });
});
