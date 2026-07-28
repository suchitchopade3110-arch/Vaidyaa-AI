import React, { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';

const NAV = [
  { id: 'dashboard', icon: '⊞',  label: 'Dashboard', path: '/app/dashboard' },
  { id: 'claim',     icon: '◎',  label: 'Claim Verifier', path: '/app/claim' },
  { id: 'report',    icon: '▤',  label: 'Report Analyzer', path: '/app/report' },
  { id: 'image',     icon: '⬡',  label: 'Image Analysis', path: '/app/image' },
  { id: 'jobs',      icon: '≡',  label: 'Job Tracker', path: '/app/jobs' },
];

function Sidebar({ activePath, onNavigate, compact }: { activePath: string, onNavigate: (path: string) => void, compact: boolean }) {
  return (
    <aside style={{
      width: compact ? '60px' : '220px', flexShrink: 0,
      background: 'var(--green-dark)', borderRight: '1px solid oklch(0.85 0.08 145)',
      display: 'flex', flexDirection: 'column', height: '100vh',
      position: 'sticky', top: 0, transition: 'width 0.25s ease', overflow: 'hidden',
    }}>
      {/* Logo */}
      <div style={{ padding: compact ? '20px 0' : '20px 18px', borderBottom: '1px solid oklch(0.82 0.08 145)', display: 'flex', alignItems: 'center', gap: '10px', justifyContent: compact ? 'center' : 'flex-start' }}>
        <img src="/assets/vaidyaa-logo.jpeg" alt="VAIDYAA AI" style={{ width: '32px', height: '32px', borderRadius: '8px', objectFit: 'cover', flexShrink: 0 }} />
        {!compact && (
          <div>
            <div style={{ fontSize: '14px', fontWeight: 800, color: 'var(--ink, oklch(0.18 0.02 145))', letterSpacing: '-0.01em' }}>VAIDYAA</div>
            <div style={{ fontSize: '10px', color: 'oklch(0.46 0.10 145)', fontFamily: 'var(--font-mono)', fontWeight: 600, letterSpacing: '0.08em' }}>AI PLATFORM</div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: '12px 8px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
        {NAV.map(item => {
          const isActive = activePath.startsWith(item.path);
          return (
            <button key={item.id} onClick={() => onNavigate(item.path)}
              title={compact ? item.label : undefined}
              style={{
                display: 'flex', alignItems: 'center', gap: '10px',
                padding: compact ? '10px' : '9px 12px',
                justifyContent: compact ? 'center' : 'flex-start',
                borderRadius: '7px', border: 'none', cursor: 'pointer',
                background: isActive ? 'oklch(0.46 0.19 145)' : 'transparent',
                color: isActive ? '#fff' : 'oklch(0.32 0.12 145)',
                fontSize: '13px', fontWeight: isActive ? 700 : 500,
                transition: 'all 0.15s ease', width: '100%',
                borderLeft: isActive && !compact ? '2px solid oklch(0.46 0.19 145)' : '2px solid transparent',
              }}
              onMouseEnter={e => { if (!isActive) { e.currentTarget.style.background = 'oklch(0.46 0.19 145 / 0.12)'; e.currentTarget.style.color = 'oklch(0.32 0.12 145)'; } }}
              onMouseLeave={e => { e.currentTarget.style.background = isActive ? 'oklch(0.46 0.19 145)' : 'transparent'; e.currentTarget.style.color = isActive ? '#fff' : 'oklch(0.32 0.12 145)'; }}
            >
              <span style={{ fontSize: '16px', flexShrink: 0 }}>{item.icon}</span>
              {!compact && <span>{item.label}</span>}
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div style={{ padding: compact ? '14px 8px' : '14px 16px', borderTop: '1px solid oklch(0.82 0.08 145)' }}>
        {!compact && (
          <div style={{ fontSize: '10px', color: 'oklch(0.40 0.10 145)', fontFamily: 'var(--font-mono)', lineHeight: 1.6 }}>
            v1.0.0 · API v1<br />
            <span style={{ color: 'oklch(0.46 0.19 145)' }}>● Online</span>
          </div>
        )}
        {compact && <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'oklch(0.46 0.19 145)', margin: '0 auto', boxShadow: '0 0 6px oklch(0.46 0.19 145)' }} />}
      </div>
    </aside>
  );
}

function Topbar({ activePath, onToggleCompact }: { activePath: string, onToggleCompact: () => void }) {
  const pageInfo = NAV.find(n => activePath.startsWith(n.path)) || NAV[0];
  return (
    <header style={{
      height: '52px', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', padding: '0 24px',
      justifyContent: 'space-between', background: 'var(--bg-surface)',
      position: 'sticky', top: 0, zIndex: 30, flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <button onClick={onToggleCompact} style={{
          background: 'transparent', border: 'none', cursor: 'pointer',
          color: 'var(--text-muted)', fontSize: '16px', padding: '4px', borderRadius: '4px',
        }}>☰</button>
        <div style={{ width: '1px', height: '16px', background: 'var(--border)' }} />
        <span style={{ fontSize: '13px', color: 'var(--text-muted)' }}>{pageInfo.icon}</span>
        <span style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)' }}>{pageInfo.label}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <div style={{ width: '7px', height: '7px', borderRadius: '50%', background: 'oklch(0.46 0.19 145)', boxShadow: '0 0 6px oklch(0.46 0.19 145)' }} />
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>API ONLINE</span>
        </div>
        <div style={{ width: '1px', height: '16px', background: 'var(--border)' }} />
        <div style={{ padding: '5px 12px', background: 'oklch(0.75 0.14 60 / 0.12)', border: '1px solid oklch(0.75 0.14 60 / 0.3)', borderRadius: '5px' }}>
          <span style={{ fontSize: '11px', fontWeight: 700, color: 'oklch(0.75 0.14 60)' }}>⚕ Medical Disclaimer Active</span>
        </div>
      </div>
    </header>
  );
}

export default function AppLayout() {
  const [compact, setCompact] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <Sidebar activePath={location.pathname} onNavigate={(p) => navigate(p)} compact={compact} />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <Topbar activePath={location.pathname} onToggleCompact={() => setCompact(c => !c)} />
        <main style={{ flex: 1, overflowY: 'auto', padding: '28px 32px', animation: 'fade-in 0.2s ease' }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
