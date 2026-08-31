// PLT-04 — minimal auth context.
//
// None of the ported pages had a concept of "logged in" before — the
// backend didn't require it. Now every real endpoint does (SEC-01/
// SEC-02), so the app needs, at minimum, a login screen and somewhere to
// keep the token. This is that minimum: no session refresh scheduling,
// no silent-refresh-on-401 retry, no "remember me" — just enough to get
// a token into apiFetch's reach and let App.jsx gate the real pages
// behind it.
import React, { createContext, useContext, useState } from "react";
import { getToken, setToken as persistToken } from "./api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(() => getToken());

  const login = (accessToken) => {
    persistToken(accessToken);
    setTokenState(accessToken);
  };

  const logout = () => {
    persistToken(null);
    setTokenState(null);
  };

  return (
    <AuthContext.Provider value={{ token, isAuthenticated: !!token, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
