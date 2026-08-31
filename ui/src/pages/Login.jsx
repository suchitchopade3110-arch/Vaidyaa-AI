// PLT-04 — Login screen (new; didn't exist in the CDN-Babel version).
//
// Talks to /auth/login directly (not under /api/v1 — see
// app/main.py's `app.include_router(auth.router, prefix="/auth", ...)`,
// mounted on the root app, not the versioned api_router).
import React, { useState } from "react";
import { useAuth } from "../lib/auth.jsx";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Login failed (${res.status})`);
      }
      const data = await res.json();
      login(data.access_token);
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--bg-base)",
      }}
    >
      <form
        onSubmit={handleSubmit}
        style={{
          width: "320px",
          background: "var(--bg-surface)",
          border: "1px solid var(--border)",
          borderRadius: "12px",
          padding: "28px",
          display: "flex",
          flexDirection: "column",
          gap: "16px",
        }}
      >
        <div>
          <div style={{ fontSize: "16px", fontWeight: 800, color: "var(--text-primary)" }}>VAIDYAA AI</div>
          <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "2px" }}>Sign in to continue</div>
        </div>

        <div>
          <label
            style={{
              fontSize: "11px",
              fontWeight: 700,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              display: "block",
              marginBottom: "6px",
            }}
          >
            Username
          </label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            style={{
              width: "100%",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border)",
              borderRadius: "6px",
              padding: "9px 12px",
              color: "var(--text-primary)",
              fontSize: "13px",
              outline: "none",
              boxSizing: "border-box",
            }}
          />
        </div>

        <div>
          <label
            style={{
              fontSize: "11px",
              fontWeight: 700,
              color: "var(--text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              display: "block",
              marginBottom: "6px",
            }}
          >
            Password
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{
              width: "100%",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border)",
              borderRadius: "6px",
              padding: "9px 12px",
              color: "var(--text-primary)",
              fontSize: "13px",
              outline: "none",
              boxSizing: "border-box",
            }}
          />
        </div>

        {error && (
          <div style={{ fontSize: "12px", color: "oklch(0.65 0.20 25)" }}>{error}</div>
        )}

        <button
          type="submit"
          disabled={loading || !username || !password}
          style={{
            padding: "10px",
            borderRadius: "7px",
            border: "none",
            background: loading ? "oklch(0.46 0.19 145 / 0.6)" : "oklch(0.46 0.19 145)",
            color: "#fff",
            fontSize: "13px",
            fontWeight: 700,
            cursor: loading ? "not-allowed" : "pointer",
          }}
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
