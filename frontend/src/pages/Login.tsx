export function LoginPage() {
  return (
    <div className="login-page">
      <div className="login-card">
        <h1 className="login-card__title">Вход в Скандату</h1>
        <p className="login-card__sub">Приёмка и отгрузка маркированной продукции</p>

        <p className="login-card__description">
          Откройте установленное решение в МойСклад — вход в Скандату выполнится
          автоматически, без отдельного логина и пароля.
        </p>
        <ol className="login-card__steps">
          <li>Войдите в свой аккаунт МойСклад.</li>
          <li>В разделе «Решения» откройте «Скандата».</li>
          <li>Нажмите «Начать работу».</li>
        </ol>
        <a
          href="https://online.moysklad.ru/"
          target="_blank"
          rel="noopener noreferrer"
          className="button button--success"
          style={{ display: 'block', textAlign: 'center' }}
        >
          Открыть МойСклад
        </a>
        <p className="login-card__description">
          Окно сканирования открывается из МойСклад в отдельной вкладке для
          подключения COM-сканера. Повторно входить в нём не требуется.
        </p>
        <a href="/instruction.html" target="_blank" rel="noopener noreferrer">
          Инструкция по работе с решением
        </a>
        <details className="login-card__direct">
          <summary>Прямой вход вне МойСклад</summary>
          <p className="login-card__description">
            Если вы используете Скандату напрямую, перейдите на страницу
            авторизации МойСклад и разрешите доступ к аккаунту.
          </p>
          <a href="/api/auth/moysklad/login" className="button">
            Войти через МойСклад
          </a>
        </details>
      </div>
    </div>
  )
}
