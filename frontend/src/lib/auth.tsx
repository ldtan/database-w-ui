import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";

import { api, clearTokens, getAccessToken, login as apiLogin } from "./api";
import type { ApiUser } from "./types";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setAuthenticated] = useState<boolean>(
    () => !!getAccessToken(),
  );

  const login = useCallback(async (username: string, password: string) => {
    await apiLogin(username, password);
    setAuthenticated(true);
  }, []);

  const logout = useCallback(() => {
    clearTokens();
    setAuthenticated(false);
  }, []);

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

// The token says we are logged in but not as whom, so account-administration UI
// has to ask. Returns null until it resolves, so admin-only controls stay hidden
// rather than flashing in and away.
export function useCurrentUser(): ApiUser | null {
  const { isAuthenticated } = useAuth();
  const query = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<ApiUser>("/auth/users/me"),
    enabled: isAuthenticated,
  });
  return query.data ?? null;
}
