"""Phase 3 differential-consideration and explanation helpers.

REG-01: the JSON contract this module produces (and the prompt that asks
the LLM for it) used to instruct the model to return a "diagnosis" and a
"primary_diagnosis" — the deepest violation of the non-diagnostic output
contract found in the codebase, since it shapes actual model output, not
just labelling copy. Renamed to "condition"/"leading_candidate" and the
prompt now asks for candidates to correlate clinically, not a diagnosis.
This was deliberately left for last (see docs/PHASE1_SKELETON.md) because
it's a breaking response-shape change that needed the frontend ported off
the old field names first (PLT-04) before it could ship without silently
breaking ui/src/pages/ReportAnalyzer.jsx's differential-list rendering.
"""

from __future__ import annotations

import json
import os
from typing import Any

from groq import Groq

from app.utils.retry import with_retry


MODEL = os.getenv("GROQ_DDX_MODEL", os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"))
DISCLAIMER = "AI-assisted analysis. NOT a medical diagnosis. Consult a qualified healthcare professional."


DDX_SYSTEM = """You are a clinical decision-support assistant for VaidyaAI.
Return only valid JSON. If evidence is insufficient, say so. Do not diagnose
and do not state or imply a diagnosis — list candidate conditions for a
clinician to correlate against the full clinical picture, each grounded in
supporting evidence from the provided input."""

DDX_PROMPT = """Generate a top-3 list of candidate conditions for clinical correlation.

Conditions: {conditions}
Lab anomalies: {anomalies}
Medications: {medications}
Risk score: {risk_score}
Report type: {report_type}

Return JSON:
{{
  "verdict": "candidates_generated" | "insufficient_evidence" | "uncertain",
  "differentials": [
    {{
      "rank": 1,
      "condition": "string",
      "icd10": "string",
      "confidence": 0.0,
      "supporting_evidence": ["string"],
      "against_evidence": ["string"],
      "recommended_tests": ["string"],
      "urgency": "routine" | "follow_up" | "urgent" | "emergency"
    }}
  ],
  "leading_candidate": "string",
  "urgency_flag": "routine" | "follow_up" | "urgent" | "emergency",
  "brief_summary": "string",
  "full_explanation": "string",
  "uncertainty_flag": false,
  "disclaimer": "string"
}}"""


def _client() -> Groq | None:
    api_key = os.environ.get("GROQ_API_KEY")
    return Groq(api_key=api_key) if api_key else None


@with_retry(max_retries=2, backoff_seconds=2.0)
def _call_groq_ddx(client: Groq, prompt: str):
    return client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": DDX_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1500,
    )


def _parse_json(raw: str) -> dict[str, Any]:
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


def _fallback_differential(entities: dict[str, Any], anomalies: list[dict[str, Any]], risk_score: float) -> dict[str, Any]:
    conditions = entities.get("conditions", []) or []
    if len(anomalies) < 2 and not conditions:
        return {
            "verdict": "insufficient_evidence",
            "differentials": [],
            "leading_candidate": "Insufficient evidence to list candidate conditions",
            "urgency_flag": "routine",
            "brief_summary": "Not enough findings are available to list candidate conditions.",
            "full_explanation": "Fewer than two abnormalities were detected and no conditions were identified.",
            "uncertainty_flag": True,
            "disclaimer": DISCLAIMER,
        }

    top = anomalies[:3]
    differentials = []
    for index, item in enumerate(top, start=1):
        field = item.get("field") or item.get("test") or "abnormal finding"
        severity = str(item.get("severity", "moderate")).lower()
        differentials.append(
            {
                "rank": index,
                "condition": f"{field} abnormality",
                "icd10": "",
                "confidence": 0.55,
                "supporting_evidence": [f"{field}: {item.get('value', '')} {item.get('unit', '')}".strip()],
                "against_evidence": [],
                "recommended_tests": ["Clinical correlation", "Repeat or confirmatory testing"],
                "urgency": "urgent" if severity in {"critical", "high"} or risk_score >= 80 else "follow_up",
            }
        )

    return {
        "verdict": "candidates_generated",
        "differentials": differentials,
        "leading_candidate": differentials[0]["condition"] if differentials else (conditions[0] if conditions else "Uncertain"),
        "urgency_flag": "urgent" if risk_score >= 80 else "follow_up" if risk_score >= 55 else "routine",
        "brief_summary": "The report contains abnormal findings that should be reviewed with a clinician.",
        "full_explanation": "Candidate conditions are listed from detected conditions, abnormal labs, and overall risk score, for clinical correlation.",
        "uncertainty_flag": False,
        "disclaimer": DISCLAIMER,
    }


def generate_differential(
    entities: dict[str, Any],
    anomalies: list[dict[str, Any]],
    risk_score: float,
    report_type: str = "lab",
) -> dict[str, Any]:
    """List top candidate conditions for clinical correlation, gated on insufficient evidence."""
    if len(anomalies) < 2 and not entities.get("conditions"):
        return _fallback_differential(entities, anomalies, risk_score)

    client = _client()
    if client is None:
        return _fallback_differential(entities, anomalies, risk_score)

    prompt = DDX_PROMPT.format(
        conditions=json.dumps((entities.get("conditions") or [])[:10]),
        anomalies=json.dumps(
            [
                {
                    "field": item.get("field") or item.get("test"),
                    "direction": item.get("direction"),
                    "severity": item.get("severity"),
                    "value": item.get("value"),
                }
                for item in anomalies[:10]
            ]
        ),
        medications=json.dumps((entities.get("medications") or [])[:5]),
        risk_score=round(float(risk_score or 0), 1),
        report_type=report_type,
    )

    try:
        response = _call_groq_ddx(client, prompt)
        result = _parse_json(response.choices[0].message.content.strip())
        result["differentials"] = (result.get("differentials") or [])[:3]
        result["disclaimer"] = DISCLAIMER
        return result
    except Exception:
        return _fallback_differential(entities, anomalies, risk_score)


def generate_explanation(
    entities: dict[str, Any],
    anomalies: list[dict[str, Any]],
    risk_score: float,
    risk_label: str,
    mode: str = "brief",
) -> str:
    """Generate brief or full explanation. Falls back locally when Groq is unavailable."""
    conditions = entities.get("conditions") or []
    top_anomaly = anomalies[0] if anomalies else {}
    top_name = top_anomaly.get("field") or top_anomaly.get("test") or "no critical finding"

    if mode == "brief":
        return (
            f"Overall risk is {risk_label} with a score of {round(float(risk_score or 0), 1)}. "
            f"Key finding: {top_name}; please review with a qualified healthcare professional."
        )

    anomaly_lines = [
        f"- {item.get('field') or item.get('test')}: {item.get('value', '')} {item.get('unit', '')} "
        f"({item.get('severity', 'abnormal')})"
        for item in anomalies[:8]
    ]
    condition_text = ", ".join(conditions[:8]) or "No conditions identified"
    return (
        f"Risk score: {round(float(risk_score or 0), 1)} ({risk_label}).\n"
        f"Conditions: {condition_text}.\n"
        "Relevant abnormalities:\n"
        + ("\n".join(anomaly_lines) if anomaly_lines else "- None detected")
        + f"\n\n{DISCLAIMER}"
    )
