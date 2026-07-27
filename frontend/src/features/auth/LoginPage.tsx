import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { InputText } from "primereact/inputtext";
import { Password } from "primereact/password";
import { Button } from "primereact/button";
import { Checkbox } from "primereact/checkbox";
import { Message } from "primereact/message";

import { useAuth } from "../../lib/auth";

/**
 * The only unauthenticated page.
 *
 * There is no sign-up: accounts are minted by a superuser (or seeded into an empty
 * database), so this offers nowhere to go but in. `login` stores the token pair and
 * flips the auth context, which is what actually lets the router render anything
 * else — see `lib/auth.tsx`.
 *
 * It fills the window rather than sitting in the app's usual page padding: this is
 * the one screen with no navigation, no header and nothing else to look at, and a
 * small card floating near the top of an empty page looked like a page that had
 * failed to finish loading.
 */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password, remember);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-card surface-card">
        <div className="flex flex-col items-center gap-3 mb-6">
          <span className="app-brand-mark" style={{ width: "2.75rem", height: "2.75rem", fontSize: "1.25rem" }} aria-hidden>
            <i className="pi pi-database" />
          </span>
          <div className="flex flex-col items-center gap-1">
            <h1 className="text-xl font-semibold">Welcome back</h1>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Sign in to your database
            </p>
          </div>
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          {error && <Message severity="error" text={error} />}
          <div className="flex flex-col gap-2">
            <label htmlFor="username" className="text-sm font-medium">
              Username
            </label>
            <InputText
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-2 w-full">
            <label htmlFor="password" className="text-sm font-medium">
              Password
            </label>
            <Password
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              feedback={false}
              toggleMask
              inputStyle={{ width: "100%" }}
              autoComplete="current-password"
              style={{ width: "100%", display: "block" }}
            />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="remember"
              checked={remember}
              onChange={(e) => setRemember(!!e.checked)}
            />
            <label htmlFor="remember" className="text-sm">
              Keep me signed in
            </label>
          </div>
          <Button type="submit" label="Sign in" loading={loading} />
        </form>
      </div>
    </div>
  );
}
