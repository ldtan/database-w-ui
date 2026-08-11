import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";

import {
  api,
  getAccessToken,
  login as apiLogin,
  logout as apiLogout,
} from "./api";
import { queryClient } from "./queryClient";
import type { ApiUser } from "./types";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (
    username: string,
    password: string,
    remember?: boolean,
  ) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setAuthenticated] = useState<boolean>(
    () => !!getAccessToken(),
  );

  // Nothing cached survives an account boundary. Query keys are shared and mostly
  // say *what* rather than *whose* (`["me"]`, `["items", uuid]`), so a second account
  // signing in on the same tab would be served the first one's data until each query
  // refetched — reading someone else's rows, and writing their preferences back to
  // its own account. Cleared on the way in as well as out, because a session can end
  // without `logout` running at all: a refresh token that expires just drops the
  // tokens (see api.ts) and leaves the cache behind.
  const login = useCallback(
    async (username: string, password: string, remember = true) => {
      await apiLogin(username, password, remember);
      queryClient.clear();
      setAuthenticated(true);
    },
    [],
  );

  // The UI turns over immediately and the revoke settles behind it: `apiLogout`
  // clears local storage in its own `finally`, so the session is over here whether or
  // not the server was reachable, and awaiting it would only make sign-out sit on a
  // network round trip to reach the same state.
  const logout = useCallback(() => {
    void apiLogout();
    setAuthenticated(false);
    queryClient.clear();
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
