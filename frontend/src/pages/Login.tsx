export function LoginPage() {
  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-card__title">Скандата</h1>
        <p className="login-card__sub">Приёмка и отгрузка маркированной продукции</p>

        <p
          style={{
            fontSize: 14,
            color: 'var(--ms-text-muted, #667085)',
            lineHeight: 1.5,
            margin: '18px 0',
          }}
        >
          Вход выполняется через МойСклад. Откройте приложение из карточки решения
          в вашем аккаунте МойСклад — или войдите по кнопке ниже.
        </p>

        <a
          href="/api/auth/moysklad/login"
          className="button button--success"
          style={{ display: 'block', textAlign: 'center' }}
        >
          Войти через МойСклад
        </a>
      </div>
    </div>
  )
}
