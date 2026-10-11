interface AuthScreenProps {
  state: "loading" | "unauthenticated" | "unavailable";
  password: string;
  error: string;
  onPasswordChange: (value: string) => void;
  onLogin: () => void;
  onRetry: () => void;
}

export function AuthScreen({
  state,
  password,
  error,
  onPasswordChange,
  onLogin,
  onRetry,
}: AuthScreenProps) {
  if (state === "loading")
    return (
      <main className="login">
        <div className="brand">
          A<span>ATLAS</span>
        </div>
        <h1>Подключение к Atlas…</h1>
        <p>Проверяю доступ к серверу.</p>
      </main>
    );
  if (state === "unavailable")
    return (
      <main className="login">
        <div className="brand">
          A<span>ATLAS</span>
        </div>
        <h1>Atlas недоступен</h1>
        <p>
          Не удалось связаться с API. Проверьте, запущен ли backend, и повторите
          попытку.
        </p>
        <button onClick={onRetry}>Повторить подключение</button>
      </main>
    );
  return (
    <main className="login">
      <div className="brand">
        A<span>ATLAS</span>
      </div>
      <h1>
        Ваше пространство.
        <br />
        Ваша память.
      </h1>
      <p>Персональный ассистент под вашим контролем.</p>
      <p><a href="/about">Что умеет Atlas →</a></p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onLogin();
        }}
      >
        <label>
          Пароль владельца
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => onPasswordChange(event.target.value)}
            required
          />
        </label>
        <button>Войти в Atlas →</button>
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </main>
  );
}
