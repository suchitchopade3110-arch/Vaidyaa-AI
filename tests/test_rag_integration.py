import pytest


def test_verify_claim_without_sources_is_uncertain_and_low_confidence():
    """RAGPipeline.verify_claim (the current API; the old module-level
    verify_claim() these tests used was removed) must not call the LLM or
    claim confidence when nothing was retrieved."""
    from app.services.rag_pipeline import rag_pipeline

    result = rag_pipeline.verify_claim("High glucose may indicate diabetes risk.", {}, [])

    assert result["verdict"] == "uncertain"
    assert 0.0 <= result["confidence_score"] <= 0.3
    assert "NOT" in result["disclaimer"].upper() and "DIAGNOSIS" in result["disclaimer"].upper()


def test_claim_pipeline_without_llm_is_flagged_insufficient(monkeypatch):
    """End to end through the pipeline with retrieval returning evidence but
    the reasoning step unavailable: the result must be flagged, not given a
    confident score."""
    import app.pipeline as pipeline
    from app.services.rag_pipeline import rag_pipeline

    monkeypatch.setattr(rag_pipeline, "retrieve_evidence",
                        lambda *a, **k: [{"id": "1", "text": "x", "score": 0.8}, {"id": "2", "text": "y", "score": 0.6}])
    monkeypatch.setattr(pipeline, "_rag_service", lambda: rag_pipeline)
    monkeypatch.setattr("app.services.rag_pipeline._get_groq",
                        lambda: (_ for _ in ()).throw(RuntimeError("GROQ_API_KEY is not set")))

    result = pipeline.run_claim_pipeline("Metformin is a first-line therapy for type 2 diabetes.")

    assert result.status == "completed"
    assert result.uncertainty_flag is True
    assert result.confidence_score <= 30
    assert result.explanation.startswith("Insufficient evidence")


def test_verify_claim_task_exists():
    from app.workers.tasks_rag import verify_claim_task

    assert verify_claim_task is not None
    assert verify_claim_task.name == "rag.verify_claim"


@pytest.mark.integration
def test_full_claim_pipeline_roundtrip():
    from app.workers.tasks_rag import verify_claim_task
    import time

    job = verify_claim_task.delay(
        "High glucose may indicate diabetes risk.",
        "test-claim-id-001",
    )

    deadline = time.time() + 90
    while not job.ready():
        assert time.time() < deadline, "Task timed out"
        time.sleep(2)

    result = job.get()

    assert result["verdict"] in {
        "supported",
        "contradicted",
        "insufficient_evidence",
    }
    assert "disclaimer" in result
    assert "NOT a medical diagnosis" in result["disclaimer"]
    assert "completed_at" in result
    assert result["claim_id"] == "test-claim-id-001"

    if result["verdict"] == "supported":
        assert len(result["citations"]) > 0


@pytest.mark.integration
def test_http_claim_submit_and_poll():
    from fastapi.testclient import TestClient
    from app.main import app
    import time

    client = TestClient(app)

    resp = client.post(
        "/api/v1/verify/claim",
        json={
            "claim_text": "Low hemoglobin may suggest anemia.",
            "top_k": 3,
            "prompt_version": "v2",
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "celery_task_id" in data
    assert "id" in data
    claim_id = data["id"]

    deadline = time.time() + 90
    while True:
        poll = client.get(f"/api/v1/verify/claim/{claim_id}/status")
        assert poll.status_code == 200
        poll_data = poll.json()
        if poll_data["status"] in {"verified", "refuted", "uncertain", "failed"}:
            break
        assert time.time() < deadline, "Poll timed out"
        time.sleep(2)

    assert "NOT a medical diagnosis" in poll_data["medical_disclaimer"]
