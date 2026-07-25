import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "primereact/card";
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
    <div className="flex justify-center" style={{ paddingTop: "12vh" }}>
      <Card title="Sign in" className="w-full" style={{ maxWidth: 380 }}>
        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          {error && <Message severity="error" text={error} />}
          <div className="flex flex-col gap-2">
            <label htmlFor="username">Username</label>
            <InputText
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          </div>
          <div className="flex flex-col gap-2 w-full">
            <label htmlFor="password">Password</label>
            <Password
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              feedback={false}
              toggleMask
              inputStyle={{ width: "100%" }}
              style={{ width: "100%", display: "block" }}
            />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              inputId="remember"
              checked={remember}
              onChange={(e) => setRemember(!!e.checked)}
            />
            <label htmlFor="remember">Remember me</label>
          </div>
          <Button type="submit" label="Sign in" loading={loading} />
        </form>
      </Card>
    </div>
  );
}
