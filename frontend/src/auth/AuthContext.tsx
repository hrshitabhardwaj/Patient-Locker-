import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, ApiError, SIGNED_OUT_EVENT } from "../lib/api";

export type Role = "admin" | "case_manager" | "staff";

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  must_change_password: boolean;
  permissions: string[];
}

type AuthState =
  | { status: "loading" }
  | { status: "signed_out" }
  | { status: "signed_in"; user: CurrentUser };

interface AuthContextValue {
  state: AuthState;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });

  const refresh = useCallback(async () => {
    try {
      const user = await api<CurrentUser>("/auth/me");
      setState({ status: "signed_in", user });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setState({ status: "signed_out" });
      else throw err;
    }
  }, []);

  useEffect(() => {
    refresh().catch(() => setState({ status: "signed_out" }));
  }, [refresh]);

  // Any request that finds the session gone (expired, revoked) returns to the sign-in page.
  useEffect(() => {
    const signedOut = () => setState({ status: "signed_out" });
    window.addEventListener(SIGNED_OUT_EVENT, signedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, signedOut);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api<{ user: CurrentUser }>("/auth/login", {
      method: "POST",
      body: { email, password },
    });
    setState({ status: "signed_in", user: res.user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setState({ status: "signed_out" });
    }
  }, []);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      await api("/auth/change-password", {
        method: "POST",
        body: { current_password: currentPassword, new_password: newPassword },
      });
      await refresh();
    },
    [refresh],
  );

  return (
    <AuthContext.Provider value={{ state, login, logout, changePassword }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
