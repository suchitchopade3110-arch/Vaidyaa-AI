{

// shared.jsx — atomic UI components for VAIDYAA AI
// Exports to window for cross-script access

const { useState, useEffect, useRef } = React;

// ─── Tokens ──────────────────────────────────────────────────────────────────
const STATUS_META = {
  pending:    { label: 'Pending',    color: 'oklch(0.55 0.01 260)', bg: 'oklch(0.55 0.01 260 / 0.12)' },
  processing: { label: 'Processing', color: 'oklch(0.46 0.19 145)', bg: 'oklch(0.46 0.19 145 / 0.10)' },
  verified:   { label: 'Verified',   color: 'oklch(0.46 0.19 145)', bg: 'oklch(0.46 0.18 145 / 0.1)' },
  refuted:    { label: 'Refuted',    color: 'oklch(0.65 0.20 25)',  bg: 'oklch(0.65 0.20 25  / 0.12)' },
  uncertain:  { label: 'Uncertain',  color: 'oklch(0.75 0.14 60)',  bg: 'oklch(0.75 0.14 60  / 0.12)' },
  failed:     { label: 'Failed',     color: 'oklch(0.60 0.18 25)',  bg: 'oklch(0.60 0.18 25  / 0.12)' },
  success:    { label: 'Success',    color: 'oklch(0.46 0.19 145)', bg: 'oklch(0.46 0.18 145 / 0.1)' },
  completed:  { label: 'Completed',  color: 'oklch(0.46 0.19 145)', bg: 'oklch(0.46 0.18 145 / 0.1)' },
};

// Maps backend/Celery job states (SUCCESS, complete, FAILURE, ...) onto STATUS_META keys.
function normalizeJobStatus(raw, fallback = 'completed') {
  const s = String(raw || '').toLowerCase();
  if (!s) return fallback;
  if (['success', 'complete', 'completed', 'done', 'finished'].includes(s)) return 'completed';
  if (['failure', 'failed', 'error', 'revoked'].includes(s)) return 'failed';
  if (['pending', 'queued', 'received'].includes(s)) return 'pending';
  if (['processing', 'progress', 'started', 'running', 'retry'].includes(s)) return 'processing';
  return STATUS_META[s] ? s : fallback;
}

// Final status of a finished job's RESULT. Celery reports SUCCESS whenever
// the task returned, but pipelines return their own failures as results
// (status "failed" / "low_quality"), so the inner status wins when it says
// the analysis did not complete.
function resolveResultStatus(result, jobStatus) {
  const inner = String((result && result.status) || '').toLowerCase();
  if (['failed', 'failure', 'error', 'low_quality'].includes(inner)) return 'failed';
  return normalizeJobStatus(jobStatus || inner);
}

// ─── StatusBadge ─────────────────────────────────────────────────────────────
function StatusBadge({ status, size = 'sm' }) {
  const meta = STATUS_META[status] || STATUS_META.pending;
  const isProcessing = status === 'processing';
  const fs = size === 'lg' ? '13px' : '11px';
  const px = size === 'lg' ? '10px' : '7px';
  const py = size === 'lg' ? '5px' : '3px';
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '5px',
      fontSize: fs, fontWeight: 600, letterSpacing: '0.04em',
      textTransform: 'uppercase', fontFamily: 'var(--font-mono)',
      color: meta.color, background: meta.bg,
      border: `1px solid ${meta.color}40`,
      borderRadius: '4px', padding: `${py} ${px}`,
      whiteSpace: 'nowrap',
    }}>
      {isProcessing && (
        <span style={{
          width: '6px', height: '6px', borderRadius: '50%',
          background: meta.color, display: 'inline-block',
          animation: 'pulse-dot 1.2s ease-in-out infinite',
        }} />
      )}
      {!isProcessing && (
        <span style={{
          width: '5px', height: '5px', borderRadius: '50%',
          background: meta.color, display: 'inline-block',
        }} />
      )}
      {meta.label}
    </span>
  );
}

// ─── ConfidenceMeter ─────────────────────────────────────────────────────────
function ConfidenceMeter({ value = 0, label = 'Confidence' }) {
  const [displayed, setDisplayed] = useState(0);
  useEffect(() => {
    let start = null;
    const duration = 900;
    const animate = (ts) => {
      if (!start) start = ts;
      const progress = Math.min((ts - start) / duration, 1);
      const ease = 1 - Math.pow(1 - progress, 3);
      setDisplayed(Math.round(ease * value));
      if (progress < 1) requestAnimationFrame(animate);
    };
    const raf = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(raf);
  }, [value]);

  const color = value >= 80 ? 'oklch(0.46 0.19 145)'
    : value >= 55 ? 'oklch(0.75 0.14 60)'
    : 'oklch(0.65 0.20 25)';

  return (
    <div style={{ width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
        <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: 500 }}>{label}</span>
        <span style={{ fontSize: '14px', fontWeight: 700, color, fontFamily: 'var(--font-mono)' }}>{displayed}%</span>
      </div>
      <div style={{ height: '6px', background: 'var(--bg-elevated)', borderRadius: '3px', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${displayed}%`, background: color,
          borderRadius: '3px', transition: 'width 0.05s linear',
          boxShadow: `0 0 8px ${color}80`,
        }} />
      </div>
    </div>
  );
}

// ─── PipelineStepper ─────────────────────────────────────────────────────────
function PipelineStepper({ steps, currentStep }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0' }}>
      {steps.map((step, i) => {
        const done = i < currentStep;
        const active = i === currentStep;
        const waiting = i > currentStep;
        const color = done ? 'oklch(0.46 0.19 145)'
          : active ? 'oklch(0.46 0.19 145)'
          : 'var(--text-muted)';
        return (
          <div key={i} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div style={{
                width: '24px', height: '24px', borderRadius: '50%',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                border: `2px solid ${color}`,
                background: done ? color : active ? `${color}20` : 'transparent',
                flexShrink: 0, fontSize: '11px', fontWeight: 700,
                color: done ? '#fff' : color,
                transition: 'all 0.3s ease',
                boxShadow: active ? `0 0 12px ${color}60` : 'none',
              }}>
                {done ? '✓' : i + 1}
              </div>
              {i < steps.length - 1 && (
                <div style={{
                  width: '2px', height: '28px',
                  background: done ? 'oklch(0.46 0.19 145)' : 'var(--border)',
                  transition: 'background 0.3s ease',
                }} />
              )}
            </div>
            <div style={{ paddingTop: '3px', paddingBottom: i < steps.length - 1 ? '28px' : '0' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: waiting ? 'var(--text-muted)' : 'var(--text-primary)', transition: 'color 0.3s' }}>
                {step.label}
                {active && <span style={{ marginLeft: '8px', fontSize: '11px', color: 'oklch(0.46 0.19 145)', fontFamily: 'var(--font-mono)', animation: 'fade-in-out 1.4s ease infinite' }}>running…</span>}
              </div>
              {step.desc && (
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>{step.desc}</div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── FileUploadZone ───────────────────────────────────────────────────────────
function FileUploadZone({ accept, acceptLabel, onFile, file, error }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef();

  const handleDrop = (e) => {
    e.preventDefault(); setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) onFile(f);
  };

  return (
    <div
      onClick={() => inputRef.current.click()}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      style={{
        border: `2px dashed ${error ? 'oklch(0.65 0.20 25)' : dragging ? 'oklch(0.46 0.19 145)' : 'var(--border-strong)'}`,
        borderRadius: '8px', padding: '28px 20px', cursor: 'pointer',
        textAlign: 'center', transition: 'all 0.2s ease',
        background: dragging ? 'oklch(0.46 0.19 145 / 0.07)' : file ? 'oklch(0.46 0.19 145 / 0.08)' : 'var(--bg-elevated)',
      }}
    >
      <input ref={inputRef} type="file" accept={accept} style={{ display: 'none' }}
        onChange={e => e.target.files[0] && onFile(e.target.files[0])} />
      {file ? (
        <>
          <div style={{ fontSize: '28px', marginBottom: '8px' }}>📄</div>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{file.name}</div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {(file.size / 1024).toFixed(1)} KB · Click to replace
          </div>
        </>
      ) : (
        <>
          <div style={{ fontSize: '28px', marginBottom: '8px', opacity: 0.5 }}>⬆</div>
          <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Drop file here or <span style={{ color: 'oklch(0.46 0.19 145)' }}>browse</span>
          </div>
          <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>{acceptLabel}</div>
        </>
      )}
      {error && <div style={{ fontSize: '12px', color: 'oklch(0.65 0.20 25)', marginTop: '8px', fontWeight: 500 }}>{error}</div>}
    </div>
  );
}

// ─── PatientIdField ───────────────────────────────────────────────────────────
function PatientIdField({ value, onChange }) {
  return (
    <div>
      <label style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Patient ID <span style={{ color: 'var(--text-muted)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>(optional)</span>
      </label>
      <input
        value={value} onChange={e => onChange(e.target.value)}
        placeholder="e.g. PT-20240125-001"
        style={{
          width: '100%', background: 'var(--bg-elevated)', border: '1px solid var(--border)',
          borderRadius: '6px', padding: '9px 12px', color: 'var(--text-primary)',
          fontSize: '13px', fontFamily: 'var(--font-mono)', outline: 'none',
          transition: 'border-color 0.2s', boxSizing: 'border-box',
        }}
        onFocus={e => e.target.style.borderColor = 'oklch(0.46 0.19 145)'}
        onBlur={e => e.target.style.borderColor = 'var(--border)'}
      />
    </div>
  );
}

// ─── MedicalDisclaimer ────────────────────────────────────────────────────────
function MedicalDisclaimer() {
  return (
    <div style={{
      background: 'oklch(0.75 0.14 60 / 0.08)', border: '1px solid oklch(0.75 0.14 60 / 0.3)',
      borderRadius: '6px', padding: '10px 14px', display: 'flex', gap: '10px', alignItems: 'flex-start',
    }}>
      <span style={{ fontSize: '14px', flexShrink: 0, marginTop: '1px' }}>⚠</span>
      <p style={{ fontSize: '11px', color: 'oklch(0.75 0.14 60)', margin: 0, lineHeight: 1.6 }}>
        <strong>Medical Disclaimer:</strong> VAIDYAA AI outputs are for informational and research purposes only. Results do not constitute medical advice, diagnosis, or treatment. Always consult a licensed medical professional before making clinical decisions.
      </p>
    </div>
  );
}

// ─── CitationList ─────────────────────────────────────────────────────────────
function CitationList({ citations = [] }) {
  if (!citations.length) return null;
  return (
    <div>
      <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '10px' }}>Sources & Citations</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {citations.map((c, i) => (
          <div key={i} style={{
            background: 'var(--bg-elevated)', borderRadius: '6px',
            padding: '10px 12px', border: '1px solid var(--border)',
          }}>
            <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>[{i+1}] {c.title}</div>
            {c.source && <div style={{ fontSize: '11px', color: 'oklch(0.46 0.19 145)', marginTop: '2px' }}>{c.source}</div>}
            {c.snippet && <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', lineHeight: 1.5 }}>{c.snippet}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── ApiErrorAlert ────────────────────────────────────────────────────────────
function ApiErrorAlert({ message }) {
  if (!message) return null;
  return (
    <div style={{
      background: 'oklch(0.65 0.20 25 / 0.1)', border: '1px solid oklch(0.65 0.20 25 / 0.4)',
      borderRadius: '6px', padding: '12px 14px', display: 'flex', gap: '10px',
    }}>
      <span style={{ color: 'oklch(0.65 0.20 25)', fontSize: '14px', flexShrink: 0 }}>✕</span>
      <span style={{ fontSize: '13px', color: 'oklch(0.65 0.20 25)' }}>{message}</span>
    </div>
  );
}

// ─── UncertaintyBanner ────────────────────────────────────────────────────────
function UncertaintyBanner({ message }) {
  return (
    <div style={{
      background: 'oklch(0.75 0.14 60 / 0.1)', border: '1px solid oklch(0.75 0.14 60 / 0.35)',
      borderRadius: '6px', padding: '12px 14px', display: 'flex', gap: '10px', alignItems: 'center',
    }}>
      <span style={{ fontSize: '16px' }}>◬</span>
      <span style={{ fontSize: '13px', color: 'oklch(0.75 0.14 60)', fontWeight: 500 }}>{message}</span>
    </div>
  );
}

// ─── EmptyResultState ─────────────────────────────────────────────────────────
function EmptyResultState({ icon = '◫', title, subtitle }) {
  return (
    <div style={{ textAlign: 'center', padding: '48px 24px' }}>
      <div style={{ fontSize: '36px', opacity: 0.2, marginBottom: '12px' }}>{icon}</div>
      <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-secondary)' }}>{title}</div>
      {subtitle && <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>{subtitle}</div>}
    </div>
  );
}

// ─── ResultHeader ─────────────────────────────────────────────────────────────
function ResultHeader({ status, jobId, elapsed }) {
  const meta = STATUS_META[status] || STATUS_META.pending;
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div style={{
          width: '40px', height: '40px', borderRadius: '8px',
          background: meta.bg, border: `1px solid ${meta.color}40`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '18px', color: meta.color,
        }}>
          {status === 'verified' || status === 'completed' || status === 'success' ? '✓' : status === 'refuted' ? '✕' : status === 'uncertain' ? '◬' : status === 'failed' ? '!' : '…'}
        </div>
        <div>
          <StatusBadge status={status} size="lg" />
          {jobId && <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px', fontFamily: 'var(--font-mono)' }}>JOB {jobId}</div>}
        </div>
      </div>
      {elapsed && <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>⏱ {elapsed}s</div>}
    </div>
  );
}

// ─── SectionLabel ─────────────────────────────────────────────────────────────
function SectionLabel({ children }) {
  return (
    <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>
      {children}
    </div>
  );
}

// ─── Card ─────────────────────────────────────────────────────────────────────
function Card({ children, style = {} }) {
  return (
    <div style={{
      background: 'var(--bg-surface)', border: '1px solid var(--border)',
      borderRadius: '10px', padding: '20px', ...style,
    }}>
      {children}
    </div>
  );
}

// ─── SegmentedControl ─────────────────────────────────────────────────────────
function SegmentedControl({ options, value, onChange }) {
  return (
    <div style={{
      display: 'inline-flex', background: 'var(--bg-elevated)',
      border: '1px solid var(--border)', borderRadius: '8px', padding: '3px', gap: '2px',
    }}>
      {options.map(opt => (
        <button key={opt.value} onClick={() => onChange(opt.value)} style={{
          padding: '7px 14px', borderRadius: '6px', border: 'none', cursor: 'pointer',
          fontSize: '12px', fontWeight: 600, transition: 'all 0.18s ease',
          background: value === opt.value ? 'oklch(0.46 0.19 145)' : 'transparent',
          color: value === opt.value ? '#fff' : 'var(--text-secondary)',
        }}>
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ─── Button ──────────────────────────────────────────────────────────────────
function Button({ children, onClick, variant = 'primary', disabled, style = {} }) {
  const [hov, setHov] = useState(false);
  const bg = variant === 'primary'
    ? (disabled ? 'oklch(0.70 0.13 195 / 0.4)' : hov ? 'oklch(0.52 0.19 145)' : 'oklch(0.46 0.19 145)')
    : hov ? 'var(--bg-hover)' : 'var(--bg-elevated)';
  return (
    <button onClick={disabled ? undefined : onClick} disabled={!!disabled} aria-disabled={!!disabled}
      onMouseEnter={() => setHov(true)} onMouseLeave={() => setHov(false)}
      style={{
        padding: '10px 20px', borderRadius: '7px', border: variant === 'ghost' ? '1px solid var(--border)' : 'none',
        background: bg, color: variant === 'primary' ? '#fff' : 'var(--text-secondary)',
        fontSize: '13px', fontWeight: 600, cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'all 0.18s ease', opacity: disabled ? 0.7 : 1, ...style,
      }}>
      {children}
    </button>
  );
}

// ─── EntityChip ───────────────────────────────────────────────────────────────
function EntityChip({ label, type }) {
  const colors = {
    condition:   'oklch(0.46 0.19 145)',
    medication:  'oklch(0.46 0.19 145)',
    lab_value:   'oklch(0.75 0.14 60)',
    finding:     'oklch(0.72 0.13 300)',
  };
  const c = colors[type] || colors.finding;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: '4px',
      padding: '3px 9px', borderRadius: '20px', fontSize: '12px',
      background: `${c}18`, border: `1px solid ${c}40`, color: c, fontWeight: 500,
    }}>
      {label}
    </span>
  );
}

// ─── Auth store (sessionStorage, in-memory fallback) ────────────────────────
const AUTH_BASE = `${window.location.origin}/auth`;
const AUTH_LOGOUT_EVENT = 'vaidya:logout';
const AUTH_LOGIN_EVENT = 'vaidya:login';
const AUTH_KEYS = { access: 'vaidya.access', refresh: 'vaidya.refresh', user: 'vaidya.user' };
const authMemory = { access: null, refresh: null, user: null };

function authRead(name) {
  try {
    const v = window.sessionStorage.getItem(AUTH_KEYS[name]);
    if (v !== null) return v;
  } catch (e) { /* storage unavailable */ }
  return authMemory[name];
}

function authWrite(name, value) {
  authMemory[name] = value || null;
  try {
    if (value) window.sessionStorage.setItem(AUTH_KEYS[name], value);
    else window.sessionStorage.removeItem(AUTH_KEYS[name]);
  } catch (e) { /* storage unavailable: memory copy is used */ }
}

const authStore = {
  getAccess: () => authRead('access'),
  getRefresh: () => authRead('refresh'),
  getUser: () => authRead('user'),
  isAuthenticated: () => !!authRead('access'),
  setTokens({ access_token, refresh_token }, username) {
    if (access_token) authWrite('access', access_token);
    if (refresh_token) authWrite('refresh', refresh_token);
    if (username) authWrite('user', username);
  },
  clear() {
    authWrite('access', null);
    authWrite('refresh', null);
    authWrite('user', null);
  },
};

// Pulls a readable message out of {"error":{"message"}} / {"detail": ...} bodies.
function apiErrorMessage(body, fallback) {
  if (body && typeof body === 'object') {
    if (body.error && typeof body.error === 'object' && body.error.message) return body.error.message;
    if (typeof body.error === 'string') return body.error;
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail) && body.detail.length) {
      return body.detail.map(d => (d && d.msg) ? d.msg : String(d)).join('; ');
    }
    if (typeof body.message === 'string') return body.message;
  }
  return fallback;
}

// Bumped on every logout. A refresh that started before a logout must not
// write its token back afterwards (that silently re-signed the user in).
let authEpoch = 0;

function forceLogout() {
  authEpoch += 1;
  authStore.clear();
  try { window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT)); } catch (e) {}
}

// Resolves to 'ok' (new access token stored), 'rejected' (server said the
// refresh token is invalid/expired/revoked -> sign out), or 'unavailable'
// (network error, 5xx, 429 -> keep the session; the user can retry).
let refreshInFlight = null;
function refreshAccessToken() {
  // Coalesce concurrent refreshes (several polls can 401 at once).
  if (refreshInFlight) return refreshInFlight;
  const refresh_token = authStore.getRefresh();
  if (!refresh_token) return Promise.resolve('rejected');
  const epoch = authEpoch;
  refreshInFlight = fetch(`${AUTH_BASE}/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token }),
  })
    .then(async res => {
      if (res.status === 401 || res.status === 403) return 'rejected';
      if (!res.ok) return 'unavailable';
      const data = await res.json().catch(() => null);
      if (!data || !data.access_token) return 'unavailable';
      if (epoch !== authEpoch) return 'rejected';  // signed out meanwhile
      authStore.setTokens({ access_token: data.access_token, refresh_token: data.refresh_token });
      return 'ok';
    })
    .catch(() => 'unavailable')
    .finally(() => { refreshInFlight = null; });
  return refreshInFlight;
}

function withAuthHeaders(options) {
  const headers = new Headers((options && options.headers) || {});
  const access = authStore.getAccess();
  if (access) headers.set('Authorization', `Bearer ${access}`);
  // Let the browser set the multipart boundary for FormData bodies.
  if (options && typeof FormData !== 'undefined' && options.body instanceof FormData) {
    headers.delete('Content-Type');
  }
  return { ...(options || {}), headers };
}

// fetch() with Bearer auth; on 401 refreshes once and retries once.
async function apiFetch(url, options = {}) {
  const res = await fetch(url, withAuthHeaders(options));
  // Only 401 means "token missing/expired". 403 is a real permission
  // answer (wrong role, report not signed off) — refreshing won't change it.
  if (res.status !== 401) return res;

  const refreshed = await refreshAccessToken();
  if (refreshed === 'rejected') {
    forceLogout();
    return res;
  }
  if (refreshed !== 'ok') return res;  // auth service briefly unreachable: don't sign out
  const retry = await fetch(url, withAuthHeaders(options));
  // A 403 after a successful refresh is a permission error, not a dead session.
  if (retry.status === 401) forceLogout();
  return retry;
}

// Builds a ws(s):// URL for `path` on this origin with ?token=<access>.
// Same-origin ws(s):// URL WITHOUT credentials. The access token travels in
// the Sec-WebSocket-Protocol header (see openAuthWebSocket) so it never
// lands in server or proxy access logs the way a ?token= query string does.
function authWsUrl(path) {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${protocol}://${window.location.host}${path}`;
}

const WS_AUTH_SUBPROTOCOL = 'vaidya.bearer';

function openAuthWebSocket(path) {
  const token = authStore.getAccess() || '';
  return new WebSocket(authWsUrl(path), [WS_AUTH_SUBPROTOCOL, token]);
}

// Role claim from the access token's JWT payload (no signature check — UI hint only).
function currentUserRole() {
  try {
    const token = authStore.getAccess();
    if (!token) return null;
    const part = token.split('.')[1];
    if (!part) return null;
    let b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    const payload = JSON.parse(atob(b64));
    return payload.role || null;
  } catch (e) {
    return null;
  }
}

async function logout() {
  const refresh_token = authStore.getRefresh();
  if (refresh_token) {
    try {
      await fetch(`${AUTH_BASE}/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token }),
      });
    } catch (e) { /* best effort */ }
  }
  forceLogout();
}

async function login(username, password) {
  const res = await fetch(`${AUTH_BASE}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(body, res.status === 401 ? 'Invalid username or password' : `Sign-in failed (${res.status})`));
  }
  if (!body.access_token) throw new Error('Sign-in failed: no access token returned');
  authStore.setTokens(body, username);
  try { window.dispatchEvent(new Event(AUTH_LOGIN_EVENT)); } catch (e) {}
  return body;
}

async function register(username, password) {
  const res = await fetch(`${AUTH_BASE}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(body, res.status === 409 ? 'That username is already taken' : `Registration failed (${res.status})`));
  }
  return body;
}

// ─── LoginScreen ──────────────────────────────────────────────────────────────
function LoginScreen({ onLogin }) {
  const [mode, setMode] = useState('signin');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const isRegister = mode === 'register';

  const inputStyle = {
    width: '100%', background: 'var(--bg-elevated)', border: '1px solid var(--border)',
    borderRadius: '6px', padding: '9px 12px', color: 'var(--text-primary)',
    fontSize: '13px', outline: 'none', transition: 'border-color 0.2s', boxSizing: 'border-box',
  };
  const labelStyle = {
    fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', display: 'block',
    marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.05em',
  };
  const focus = e => { e.target.style.borderColor = 'oklch(0.46 0.19 145)'; };
  const blur = e => { e.target.style.borderColor = 'var(--border)'; };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (busy) return;
    const u = username.trim();
    if (u.length < 3 || u.length > 100) { setError('Username must be 3–100 characters.'); return; }
    if (password.length < 8) { setError('Password must be at least 8 characters.'); return; }
    if (isRegister && password !== confirm) { setError('Passwords do not match.'); return; }
    setBusy(true);
    setError('');
    try {
      if (isRegister) await register(u, password);
      await login(u, password);
      if (onLogin) onLogin();
    } catch (err) {
      setError(err.message || 'Something went wrong');
      setBusy(false);
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px', background: 'var(--bg-base)' }}>
      <Card style={{ width: '100%', maxWidth: '380px', padding: '28px', animation: 'fade-in 0.2s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '20px' }}>
          <img src="assets/vaidyaa-logo.jpeg" alt="VAIDYAA AI" style={{ width: '36px', height: '36px', borderRadius: '8px', objectFit: 'cover' }} />
          <div>
            <div style={{ fontSize: '16px', fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>VAIDYAA</div>
            <div style={{ fontSize: '10px', color: 'oklch(0.46 0.10 145)', fontFamily: 'var(--font-mono)', fontWeight: 600, letterSpacing: '0.08em' }}>AI PLATFORM</div>
          </div>
        </div>

        <div style={{ marginBottom: '18px' }}>
          <SegmentedControl
            options={[{ value: 'signin', label: 'Sign in' }, { value: 'register', label: 'Create account' }]}
            value={mode}
            onChange={(m) => { setMode(m); setError(''); }}
          />
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div>
            <label style={labelStyle} htmlFor="vaidya-username">Username</label>
            <input id="vaidya-username" autoComplete="username" value={username}
              onChange={e => setUsername(e.target.value)} style={inputStyle} onFocus={focus} onBlur={blur} />
          </div>
          <div>
            <label style={labelStyle} htmlFor="vaidya-password">Password</label>
            <input id="vaidya-password" type="password" autoComplete={isRegister ? 'new-password' : 'current-password'}
              value={password} onChange={e => setPassword(e.target.value)} style={inputStyle} onFocus={focus} onBlur={blur} />
          </div>
          {isRegister && (
            <div>
              <label style={labelStyle} htmlFor="vaidya-confirm">Confirm password</label>
              <input id="vaidya-confirm" type="password" autoComplete="new-password"
                value={confirm} onChange={e => setConfirm(e.target.value)} style={inputStyle} onFocus={focus} onBlur={blur} />
            </div>
          )}
          <ApiErrorAlert message={error} />
          <Button disabled={busy} style={{ width: '100%' }}>
            {busy ? (isRegister ? 'Creating account…' : 'Signing in…') : (isRegister ? 'Create account' : 'Sign in')}
          </Button>
        </form>
      </Card>
    </div>
  );
}

Object.assign(window, {
  StatusBadge, ConfidenceMeter, PipelineStepper,
  FileUploadZone, PatientIdField, MedicalDisclaimer,
  CitationList, ApiErrorAlert, UncertaintyBanner,
  EmptyResultState, ResultHeader, SectionLabel,
  Card, SegmentedControl, Button, EntityChip,
  STATUS_META,
  authStore, apiFetch, authWsUrl, openAuthWebSocket, logout, apiErrorMessage,
  currentUserRole, normalizeJobStatus, resolveResultStatus,
  LoginScreen, AUTH_LOGOUT_EVENT, AUTH_LOGIN_EVENT,
});

}
