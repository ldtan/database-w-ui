import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Card } from "primereact/card";
import { InputText } from "primereact/inputtext";
import { Password } from "primereact/password";
import { Button } from "primereact/button";
import { Message } from "primereact/message";

import { useAuth } from "../../lib/auth";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
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
          <div className="flex flex-col gap-2">
            <label htmlFor="password">Password</label>
            <Password
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              feedback={false}
              toggleMask
              inputStyle={{ width: "100%" }}
              style={{ width: "100%" }}
            />
          </div>
          <Button type="submit" label="Sign in" loading={loading} />
        </form>
      </Card>
    </div>
  );
}
