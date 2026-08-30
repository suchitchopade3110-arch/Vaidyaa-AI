# VaidyaAI

Multi-modal medical intelligence platform for AI-assisted clinical claim verification, medical report analysis, and medical image interpretation — with architecturally enforced hallucination mitigation.

> **AI-assisted analysis, NOT a medical diagnosis.** Every API response carries a mandatory medical disclaimer. This system is a decision-support tool with a human-in-the-loop requirement. It is not FDA/CDSCO cleared and must not be used for autonomous clinical decision-making.

---

## Why this exists

Claim verification, report interpretation, and imaging review are still manual and error-prone. Most AI tooling in this space either handles one modality or hands a single general-purpose LLM the entire job — which is exactly how hallucinated clinical claims get produced.

VaidyaAI takes the opposite approach: **strict model role confinement**. No model is allowed outside its lane.

| Model | Allowed role | Never does |
|---|---|---|
| ClinicalBERT | Named entity extraction only | Reasoning, generation |
| BioGPT | RAG embeddings only | Text generation |
| Groq / LLaMA-3 | Reasoning and generation only | Entity extraction, embedding |
| XGBoost / CheXNet / ViT / Swin | Prediction only | Explanation text |

Mixing these roles is an architectural violation, not a style preference.

---

## Architecture

Six-layer pipeline. Two entry paths (text and image) that converge at the RAG/LLM layer.

```
Layer 1  Input & Preprocessing   PaddleOCR · PyMuPDF · CLAHE · DICOM parsing · ClinicalBERT NER
Layer 2  Segmentation            MedSAM · auto-prompt generation · ROI extraction
Layer 3  ML Prediction           XGBoost + SHAP · CheXNet/DenseNet (14-class NIH) · GradCAM
                                 modality routing: MRI ViT ensemble · CT (TorchXRayVision)
                                 skin ViT (HAM10000, attention rollout) · pathology Swin (CRC)
Layer 4  RAG / LLM Intelligence  ChromaDB (4 collections) · BioGPT embeddings
                                 Groq/LLaMA-3 reasoning · self-verify loop · citation extraction
Layer 5  Async Infrastructure    FastAPI · Celery · Redis · PostgreSQL
Layer 6  Output                  structured JSON · Platt-scaled confidence (0–100)
                                 explainability artifacts · PDF reports · WebSocket updates
```

### Hallucination mitigation

Three layers, all enforced in code rather than in prompts:

1. **RAG grounding** — generation is constrained to retrieved ChromaDB evidence.
2. **Self-verification loop** — the generated answer is re-checked against its own cited sources.
3. **Threshold flagging** — Platt-scaled confidence below threshold, or fewer than 2 supporting sources, returns `Insufficient evidence` instead of an answer.

Confidence is always reported on a **0–100 Platt-scaled range**. A 0–1 score anywhere in the codebase is a bug.

---

## Stack

**Backend** FastAPI · Celery · Redis · PostgreSQL · ChromaDB · SQLAlchemy · Alembic · SlowAPI
**ML/AI** PyTorch · HuggingFace Transformers · ClinicalBERT · BioGPT · XGBoost · SHAP · GradCAM · MedSAM · PaddleOCR · TorchXRayVision · YOLOv8 · Swin · ViT
**LLM** Groq (LLaMA-3)
**Frontend** React
**Ops** Docker Compose

---

## Getting started

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Redis 6+
- A Groq API key

### Install

```bash
git clone https://github.com/suchitchopade3110-arch/vadiyaaAI.git
cd vadiyaaAI

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Set at minimum:

```
DATABASE_URL=postgresql://user:pass@localhost:5432/vaidyaai
REDIS_URL=redis://localhost:6379/0
GROQ_API_KEY=your_key_here
CHROMA_PERSIST_DIR=./chroma_db
```

### Migrate

```bash
alembic upgrade head
```

### Run

Three terminals:

```bash
# Terminal 1 — broker
redis-server

# Terminal 2 — worker
source .venv/bin/activate
celery -A app.workers.celery_app worker -Q reports,images,claims --loglevel=info

# Terminal 3 — API
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Docs at `http://localhost:8000/docs`.

---

## Model weights

Model checkpoints are not tracked in git. Place them under `models/` before running the full pipeline:

| File | Used by | Fallback if missing |
| --- | --- | --- |
| `xgb_calibrated.pkl` | Tabular predictor | Hardcoded heuristic |
| `scaler.pkl` | Feature scaling | Hardcoded heuristic |
| `shap_explainer.pkl` | SHAP explainability | Explanation omitted |
| `sam_vit_b_01ec64.pth` | MedSAM segmentation | Full-image passthrough |

Fallbacks let the app boot, but predictions from a fallback path are **not** production-valid.

---

## API surface

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/claims/verify` | Verify a clinical claim → Verified / Refuted / Uncertain |
| `POST` | `/api/v1/reports/analyze` | Parse lab report, clinical note, or discharge summary |
| `POST` | `/api/v1/images/analyze` | Segment + classify X-ray, CT, MRI, skin, or pathology image |
| `GET` | `/api/v1/jobs/{job_id}` | Poll async job status |
| `WS` | `/ws/jobs/{job_id}` | Live progress stream |
| `GET` | `/health` | Liveness probe |

Long-running analyses return a `job_id` immediately and complete on the Celery worker.

Live routes live in `app/api/v1/routes/`. Anything in `app/routes/` is legacy and unmounted — grep before assuming a file is active.

---

## Known limitations

- **MedSAM falls back silently.** `segment-anything` is not pinned in `requirements.txt` and the checkpoint ships separately, so segmentation currently passes the full image through. Fix before trusting any ROI-dependent output.
- **XGBoost version warnings** on load; the calibrated model needs re-export against the pinned version.
- **HIPAA audit logging is incomplete.** Do not process real PHI on this build.
- **Docker Compose setup is unfinished.**
- Dead code exists in the tree. `app/ml/predictors/` is orphaned.

---

## Project layout

```
app/
├── main.py                  FastAPI app factory
├── config.py                pydantic-settings
├── api/v1/routes/           live route handlers
├── models/                  SQLAlchemy ORM
├── schemas/                 Pydantic request/response
├── services/
│   ├── preprocessing/       OCR, DICOM, ClinicalBERT NER
│   ├── segmentation/        MedSAM wrapper
│   ├── prediction/          XGBoost, CheXNet, modality routing
│   ├── intelligence/        BioGPT RAG, LLM reasoning, self-verify
│   └── explainability/      SHAP, GradCAM, disclaimers
├── tasks/                   Celery task definitions
├── workers/celery_app.py
└── core/                    auth, middleware, error handlers
migrations/
tests/
```

---

## Testing

```bash
source .venv/bin/activate
pytest -v
pytest --cov=app --cov-report=term-missing
```

---

## Team

**Straw Hats** — Sri Shakthi Institute of Engineering and Technology, Coimbatore

| Name | Role |
| --- | --- |
| Suchit Sachin Chopade | Backend Lead / Team Leader |
| Subhiksha B | Domain / Research Lead |
| Shruthi S | LLM / RAG Engineer |
| Shreekumar B | ML / Prediction Engineer |
| Thaariha G | Data / Preprocessing Engineer |

---

## License

TBD — see `LICENSE`. Patent application pending on the underlying architecture.
