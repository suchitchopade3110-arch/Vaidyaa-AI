{

// report-qr-widget.jsx - QR card for report/image result screens

const QR_API_BASE = window.location.origin;

const SIGNOFF_ROLES = ['clinician', 'admin'];
const AWAITING_SIGNOFF_MSG = 'Awaiting clinician sign-off — QR sharing unlocks after review';

function ReportQRWidget({ reportId, patientId, modelVersions }) {
  const { useEffect, useState } = React;
  const [qrUrl, setQrUrl] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [needsSignOff, setNeedsSignOff] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [signing, setSigning] = useState(false);
  const [signError, setSignError] = useState('');
  const [refreshCount, setRefreshCount] = useState(0);

  const canSignOff = SIGNOFF_ROLES.includes(currentUserRole());

  useEffect(() => {
    if (!reportId) return undefined;

    let objectUrl = null;
    let cancelled = false;
    setLoading(true);
    setError('');
    setNeedsSignOff(false);
    setQrUrl(null);

    const params = patientId ? `?patient_id=${encodeURIComponent(patientId)}` : '';
    apiFetch(`${QR_API_BASE}/reports/${encodeURIComponent(reportId)}/qr${params}`)
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          if (response.status === 403 && body?.error?.code === 'NOT_SIGNED_OFF') {
            const err = new Error(AWAITING_SIGNOFF_MSG);
            err.notSignedOff = true;
            throw err;
          }
          throw new Error(apiErrorMessage(body, `QR generation failed (${response.status})`));
        }
        return response.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setQrUrl(objectUrl);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err.notSignedOff) setNeedsSignOff(true);
        setError(err.message || 'QR generation failed');
      })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [reportId, patientId, refreshCount]);

  const submitSignOff = async () => {
    if (!reviewed || signing) return;
    setSigning(true);
    setSignError('');
    try {
      const versions = String(modelVersions || '').trim().slice(0, 200) || 'ui-signoff';
      const response = await apiFetch(`${QR_API_BASE}/api/v1/jobs/${encodeURIComponent(reportId)}/sign-off`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_versions: versions }),
      });
      const body = await response.json().catch(() => ({}));
      const alreadySigned = response.status === 409 && body?.error?.code === 'ALREADY_SIGNED_OFF';
      if (response.status === 201 || response.ok || alreadySigned) {
        setConfirming(false);
        setReviewed(false);
        setRefreshCount((n) => n + 1);
        return;
      }
      setSignError(apiErrorMessage(body, `Sign-off failed (${response.status})`));
    } catch (err) {
      setSignError(err.message || 'Sign-off failed');
    } finally {
      setSigning(false);
    }
  };

  return (
    <div style={styles.wrapper}>
      <div style={styles.header}>
        <div>
          <div style={styles.title}>Download QR</div>
          <div style={styles.subtitle}>Preview first · 30 min · single use</div>
        </div>
        <button type="button" onClick={() => setRefreshCount((n) => n + 1)} style={styles.iconButton} title="Generate a new QR code">
          ↺
        </button>
      </div>

      <div style={styles.qrBox}>
        {loading && <div style={styles.placeholder}>Generating...</div>}
        {error && !loading && <div style={needsSignOff ? styles.awaiting : styles.error}>{error}</div>}
        {qrUrl && !loading && !error && <img src={qrUrl} alt="Report download QR code" style={styles.qrImg} />}
      </div>

      {needsSignOff && !loading && canSignOff && (
        <div style={{ marginTop: '12px' }}>
          {!confirming ? (
            <button type="button" onClick={() => { setConfirming(true); setSignError(''); }} style={styles.primaryButton}>
              Review &amp; sign off
            </button>
          ) : (
            <div style={styles.confirmBox}>
              <label style={styles.checkboxLabel}>
                <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
                <span>I have reviewed this AI-assisted output</span>
              </label>
              <div style={{ display: 'flex', gap: '6px', marginTop: '10px' }}>
                <button type="button" onClick={submitSignOff} disabled={!reviewed || signing}
                  style={{ ...styles.primaryButton, opacity: !reviewed || signing ? 0.5 : 1, cursor: !reviewed || signing ? 'not-allowed' : 'pointer' }}>
                  {signing ? 'Signing…' : 'Confirm sign-off'}
                </button>
                <button type="button" onClick={() => { setConfirming(false); setReviewed(false); setSignError(''); }} style={styles.ghostButton}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {signError && <div style={{ ...styles.error, marginTop: '8px', textAlign: 'left' }}>{signError}</div>}
        </div>
      )}
    </div>
  );
}

const styles = {
  wrapper: {
    background: 'var(--bg-surface)',
    border: '1px solid var(--border)',
    borderRadius: '8px',
    padding: '16px',
    width: '240px',
    flexShrink: 0,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: '10px',
    marginBottom: '12px',
  },
  title: {
    fontSize: '13px',
    fontWeight: 800,
    color: 'var(--text-primary)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  subtitle: {
    fontSize: '11px',
    color: 'var(--text-muted)',
    marginTop: '3px',
  },
  iconButton: {
    width: '30px',
    height: '30px',
    borderRadius: '6px',
    border: '1px solid var(--border)',
    background: 'var(--bg-elevated)',
    color: 'var(--text-secondary)',
    cursor: 'pointer',
    fontSize: '15px',
  },
  qrBox: {
    width: '204px',
    height: '204px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#fff',
    borderRadius: '8px',
    padding: '8px',
    overflow: 'hidden',
  },
  qrImg: {
    width: '100%',
    height: '100%',
    objectFit: 'contain',
  },
  awaiting: {
    color: 'oklch(0.55 0.12 60)',
    fontSize: '12px',
    lineHeight: 1.4,
    textAlign: 'center',
    fontWeight: 600,
  },
  primaryButton: {
    width: '100%',
    padding: '8px 12px',
    borderRadius: '6px',
    border: 'none',
    background: 'oklch(0.46 0.19 145)',
    color: '#fff',
    fontSize: '12px',
    fontWeight: 600,
    cursor: 'pointer',
  },
  ghostButton: {
    padding: '8px 12px',
    borderRadius: '6px',
    border: '1px solid var(--border)',
    background: 'var(--bg-elevated)',
    color: 'var(--text-secondary)',
    fontSize: '12px',
    fontWeight: 600,
    cursor: 'pointer',
  },
  confirmBox: {
    padding: '10px',
    borderRadius: '6px',
    border: '1px solid var(--border)',
    background: 'var(--bg-elevated)',
  },
  checkboxLabel: {
    display: 'flex',
    gap: '8px',
    alignItems: 'flex-start',
    fontSize: '12px',
    color: 'var(--text-primary)',
    lineHeight: 1.4,
    cursor: 'pointer',
  },
  placeholder: {
    color: '#64748b',
    fontSize: '12px',
  },
  error: {
    color: '#dc2626',
    fontSize: '12px',
    lineHeight: 1.4,
    textAlign: 'center',
  },
};

window.ReportQRWidget = ReportQRWidget;

}
