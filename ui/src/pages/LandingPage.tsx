import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';

export default function LandingPage() {
  useEffect(() => {
    // Scroll reveal logic
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if (e.isIntersecting) e.target.classList.add('visible');
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    
    document.querySelectorAll('.reveal').forEach(el => observer.observe(el));

    // Force hero reveals immediately
    document.querySelectorAll('.hero .reveal').forEach(el => {
      setTimeout(() => el.classList.add('visible'), 100 + parseInt(el.className.match(/delay-(\d)/)?.[1] || '0') * 150);
    });

    // Navbar scroll shadow
    const handleScroll = () => {
      document.getElementById('navbar')?.classList.toggle('scrolled', window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll);
    return () => {
      window.removeEventListener('scroll', handleScroll);
      observer.disconnect();
    };
  }, []);

  return (
    <>
      <nav id="navbar">
        <Link to="/" className="nav-logo">
          <img src="/assets/vaidyaa-logo.jpeg" alt="VAIDYAA AI" style={{height:'38px',width:'38px',objectFit:'cover',borderRadius:'9px',background:'var(--green-mid)'}} />
          <span className="nav-logo-text">VAIDYAA <span>AI</span></span>
        </Link>
        <div className="nav-links">
          <a href="#features">Features</a>
          <a href="#how">How it Works</a>
          <a href="#trust">Trust &amp; Safety</a>
        </div>
        <Link to="/app" className="nav-cta">Open Platform →</Link>
      </nav>

      <section className="hero">
        <div className="hero-bg-blob blob-1"></div>
        <div className="hero-bg-blob blob-2"></div>
        <div className="hero-bg-blob blob-3"></div>

        <div className="hero-left">
          <div className="hero-eyebrow reveal">Medical AI · Research Grade</div>
          <h1 className="hero-headline reveal reveal-delay-1">
            Intelligent.<br/>Evidence-Based.<br/><em>Verified.</em>
          </h1>
          <p className="hero-sub reveal reveal-delay-2">
            VAIDYAA AI brings multi-model clinical reasoning to your workflow — fact-checking medical claims, analyzing lab reports, and reading diagnostic imaging with radiologist-grade pipeline transparency.
          </p>
          <div className="hero-actions reveal reveal-delay-3">
            <Link to="/app" className="btn-primary">Launch Platform</Link>
            <a href="#features" className="btn-ghost">Explore Features</a>
          </div>
        </div>

        <div className="hero-right reveal reveal-delay-2">
          <div className="mock-card">
            <div className="mock-topbar">
              <div className="mock-topbar-dot" style={{background:'#ef4444'}}></div>
              <div className="mock-topbar-dot" style={{background:'#f59e0b'}}></div>
              <div className="mock-topbar-dot" style={{background:'#22c55e'}}></div>
              <span style={{marginLeft:'8px',fontFamily:'var(--font-mono)',fontSize:'11px',color:'rgba(255,255,255,0.6)'}}>claim-verifier · live</span>
            </div>
            <div className="mock-body">
              <div className="mock-label">Submitted Claim</div>
              <div className="mock-claim-box">"Aspirin reduces the risk of myocardial infarction by 25% in secondary prevention patients."</div>
              <div className="mock-label">Analysis Pipeline</div>
              <div className="mock-stepper">
                <div className="mock-step">
                  <div className="mock-step-dot" style={{background:'var(--green-mid)',color:'#fff'}}>✓</div>
                  <span style={{fontSize:'12px',color:'var(--ink-2)'}}>ClinicalBERT NER — 3 entities extracted</span>
                </div>
                <div className="mock-step">
                  <div className="mock-step-dot" style={{background:'var(--green-mid)',color:'#fff'}}>✓</div>
                  <span style={{fontSize:'12px',color:'var(--ink-2)'}}>Evidence retrieval — 14 RCTs found</span>
                </div>
                <div className="mock-step">
                  <div className="mock-step-dot" style={{background:'var(--green-mid)',color:'#fff'}}>✓</div>
                  <span style={{fontSize:'12px',color:'var(--ink-2)'}}>XGBoost + SHAP scoring complete</span>
                </div>
              </div>
              <div className="mock-verdict">
                <div className="mock-verdict-label">◎ Verdict — Verified</div>
                <div className="mock-verdict-text">Supported by Antithrombotic Trialists Collaboration (2009) and ACC/AHA guidelines. Effect size consistent across 14 RCTs.</div>
                <div className="mock-confidence">
                  <div style={{display:'flex',justifyContent:'space-between',fontSize:'11px',color:'var(--ink-3)',marginBottom:'4px'}}>
                    <span>Confidence</span><span style={{fontFamily:'var(--font-mono)',fontWeight:700,color:'var(--green-mid)'}}>82%</span>
                  </div>
                  <div className="mock-conf-bar"><div className="mock-conf-fill"></div></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div className="stats-strip">
        <div className="stat-item">
          <div className="stat-num">4<span>+</span></div>
          <div className="stat-label">Analysis Pipelines</div>
        </div>
        <div className="stat-divider"></div>
        <div className="stat-item">
          <div className="stat-num">6</div>
          <div className="stat-label">AI Models in Pipeline</div>
        </div>
        <div className="stat-divider"></div>
        <div className="stat-item">
          <div className="stat-num">SHAP</div>
          <div className="stat-label">Explainable AI Output</div>
        </div>
        <div className="stat-divider"></div>
        <div className="stat-item">
          <div className="stat-num">RAG</div>
          <div className="stat-label">Evidence Retrieval</div>
        </div>
      </div>

      <section className="section" id="features">
        <div className="section-eyebrow reveal">Platform Modules</div>
        <h2 className="section-title reveal reveal-delay-1">Four pipelines. <em>One platform.</em></h2>
        <p className="section-sub reveal reveal-delay-2">Each module is an end-to-end AI system — from raw input to explainable, evidence-backed clinical output.</p>
        <div className="features-grid">
          <div className="feature-card reveal">
            <div className="feature-icon">◎</div>
            <div className="feature-title">Claim Verifier</div>
            <div className="feature-desc">Submit any medical claim and receive a verdict backed by PubMed evidence, confidence scores, and SHAP-explained feature attribution.</div>
            <div className="feature-tags">
              <span className="feature-tag">ClinicalBERT</span>
              <span className="feature-tag">XGBoost</span>
              <span className="feature-tag">RAG</span>
              <span className="feature-tag">SHAP</span>
            </div>
          </div>
          <div className="feature-card reveal reveal-delay-1">
            <div className="feature-icon">▤</div>
            <div className="feature-title">Report Analyzer</div>
            <div className="feature-desc">Upload lab panels, clinical notes, or discharge summaries. Get risk scores, anomaly detection, NER-extracted entities, and care gap identification.</div>
            <div className="feature-tags">
              <span className="feature-tag">OCR</span>
              <span className="feature-tag">NER</span>
              <span className="feature-tag">Risk Score</span>
              <span className="feature-tag">Anomaly</span>
            </div>
          </div>
          <div className="feature-card reveal reveal-delay-2">
            <div className="feature-icon">⬡</div>
            <div className="feature-title">Image Analysis</div>
            <div className="feature-desc">Analyze X-rays, CT scans, MRI, dermatology and pathology slides. Powered by ResNet classification with GradCAM heatmaps and radiological narratives.</div>
            <div className="feature-tags">
              <span className="feature-tag">DICOM</span>
              <span className="feature-tag">GradCAM</span>
              <span className="feature-tag">Segmentation</span>
              <span className="feature-tag">LLM</span>
            </div>
          </div>
          <div className="feature-card reveal reveal-delay-3">
            <div className="feature-icon">≡</div>
            <div className="feature-title">Job Tracker</div>
            <div className="feature-desc">Every analysis runs async via Celery. Monitor live pipeline status, stream logs, cancel or retry jobs, and filter across all pipeline types.</div>
            <div className="feature-tags">
              <span className="feature-tag">Celery</span>
              <span className="feature-tag">Live Polling</span>
              <span className="feature-tag">Async</span>
            </div>
          </div>
        </div>
      </section>

      <section className="section how-section" id="how">
        <div className="section-eyebrow reveal">Pipeline Architecture</div>
        <h2 className="section-title reveal reveal-delay-1">From input to <em>insight</em> in seconds.</h2>
        <p className="section-sub reveal reveal-delay-2">Every analysis follows a deterministic, transparent pipeline. No black boxes — every decision is traceable.</p>
        <div className="pipeline-flow reveal reveal-delay-2">
          <div className="pipeline-step">
            <div className="pipeline-num">1</div>
            <div className="pipeline-step-title">Submit</div>
            <div className="pipeline-step-desc">Claim text, report file, or medical image uploaded via secure API</div>
          </div>
          <div className="pipeline-step">
            <div className="pipeline-num">2</div>
            <div className="pipeline-step-title">Extract</div>
            <div className="pipeline-step-desc">OCR + ClinicalBERT NER identifies entities, conditions, values</div>
          </div>
          <div className="pipeline-step">
            <div className="pipeline-num">3</div>
            <div className="pipeline-step-title">Score</div>
            <div className="pipeline-step-desc">XGBoost classifies risk; SHAP explains the top contributing features</div>
          </div>
          <div className="pipeline-step">
            <div className="pipeline-num">4</div>
            <div className="pipeline-step-title">Retrieve</div>
            <div className="pipeline-step-desc">RAG queries PubMed, clinical guidelines, and knowledge base</div>
          </div>
          <div className="pipeline-step">
            <div className="pipeline-num">5</div>
            <div className="pipeline-step-title">Synthesize</div>
            <div className="pipeline-step-desc">LLM generates the final verdict, narrative, and citations</div>
          </div>
        </div>
      </section>

      <section className="section" id="trust">
        <div className="section-eyebrow reveal">Trust &amp; Safety</div>
        <h2 className="section-title reveal reveal-delay-1">Built for <em>clinical rigor.</em></h2>
        <p className="section-sub reveal reveal-delay-2">Every design decision prioritizes transparency, explainability, and responsible AI — never a substitute for medical judgment.</p>
        <div className="trust-grid">
          <div className="trust-card reveal">
            <div className="trust-icon">◈</div>
            <div>
              <div className="trust-title">Explainable AI</div>
              <div className="trust-desc">SHAP values for every prediction. Know exactly which features drove each verdict — no hidden reasoning.</div>
            </div>
          </div>
          <div className="trust-card reveal reveal-delay-1">
            <div className="trust-icon">◎</div>
            <div>
              <div className="trust-title">Evidence-Backed</div>
              <div className="trust-desc">Every claim and report finding is grounded in peer-reviewed citations from PubMed and clinical guidelines.</div>
            </div>
          </div>
          <div className="trust-card reveal reveal-delay-2">
            <div className="trust-icon">◬</div>
            <div>
              <div className="trust-title">Uncertainty Flagging</div>
              <div className="trust-desc">Low-confidence outputs are explicitly flagged. The system tells you when it doesn't know.</div>
            </div>
          </div>
          <div className="trust-card reveal reveal-delay-3">
            <div className="trust-icon">▣</div>
            <div>
              <div className="trust-title">Async Audit Trail</div>
              <div className="trust-desc">Every job is logged with Celery task IDs, timestamps, pipeline steps, and full result history.</div>
            </div>
          </div>
        </div>
      </section>

      <div className="disclaimer-strip">
        <span style={{fontSize:'18px',flexShrink:0}}>⚠</span>
        <p><strong>Medical Disclaimer:</strong> VAIDYAA AI is a research and informational tool only. Outputs do not constitute medical advice, diagnosis, or clinical treatment recommendations. All findings must be reviewed and validated by a licensed medical professional before any clinical application.</p>
      </div>

      <section className="cta-section">
        <div className="cta-bg-circle cta-circle-1"></div>
        <div className="cta-bg-circle cta-circle-2"></div>
        <div className="cta-eyebrow reveal">Ready to begin?</div>
        <h2 className="cta-headline reveal reveal-delay-1">Start your first<br/>analysis today.</h2>
        <p className="cta-sub reveal reveal-delay-2">Verify claims, analyze reports, and read images — all in one platform designed for clinical intelligence.</p>
        <Link to="/app" className="btn-white reveal reveal-delay-3">Open VAIDYAA AI →</Link>
      </section>

      <footer>
        <div className="footer-logo">
          <img src="/assets/vaidyaa-logo.jpeg" alt="VAIDYAA AI" style={{height:'30px',width:'30px',objectFit:'cover',borderRadius:'7px',background:'var(--green-mid)'}} />
          <span className="footer-logo-name">VAIDYAA AI</span>
        </div>
        <div className="footer-links">
          <a href="#features">Features</a>
          <a href="#how">Pipeline</a>
          <a href="#trust">Safety</a>
          <Link to="/app">Platform</Link>
        </div>
        <div className="footer-copy">© 2026 VAIDYAA AI · For research use only</div>
      </footer>
    </>
  );
}
