document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('login-form');
    const message = document.getElementById('login-message');

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        message.textContent = '';

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

            location.href = data.redirect_to || '/';
        } catch (error) {
            console.error(error);
            message.textContent = 'Ошибка сервера при входе.';
        }
    });
});
