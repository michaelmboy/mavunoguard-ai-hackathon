"""
MavunoGuard AI Clinic — Gemini Vision-powered diagnosis service.

Accepts a base64-encoded image plus metadata (category, description,
location context) and returns structured diagnostic results covering:
  - Crop diseases & nutrient deficiencies
  - Animal / livestock health conditions
  - Post-harvest produce quality issues
  - Soil surface abnormalities

The image is sent inline as a base64 data part alongside a rich text
prompt. No image is stored after the request completes.
"""

import os
import json
import logging
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category-specific prompt context
# ---------------------------------------------------------------------------

_CATEGORY_CONTEXT: dict[str, str] = {
    "crop": (
        "The image shows a crop or plant. Focus on: visible disease symptoms "
        "(leaf spots, blights, rusts, mildews, wilting), pest damage (holes, "
        "frass, mines, webbing), nutrient deficiencies (yellowing patterns, "
        "necrotic margins, purple discoloration), and overall crop health stage."
    ),
    "animal": (
        "The image shows a farm animal or livestock. Focus on: visible body "
        "condition score, skin/coat/feather abnormalities, eye clarity, "
        "nasal discharge, swelling or wounds, lameness indicators, and any "
        "visible signs of parasites or disease."
    ),
    "produce": (
        "The image shows harvested agricultural produce. Focus on: post-harvest "
        "disease (mold, rot, fungal lesions), mechanical damage, pest infestation, "
        "ripeness/maturity assessment, storage quality issues, and marketability."
    ),
    "soil": (
        "The image shows a soil surface or soil profile. Focus on: visible "
        "erosion, compaction signs, surface crust, waterlogging evidence, "
        "organic matter content indicators, color anomalies, and any surface "
        "pests or weeds that indicate soil health issues."
    ),
}

_SYSTEM_INSTRUCTION = """You are MavunoGuard AI Clinic, an expert agricultural diagnostician 
serving smallholder farmers in East Africa. You analyze images of crops, livestock, produce 
and soil to deliver fast, accurate, actionable diagnoses.

Your rules:
- Base your diagnosis ONLY on what is visible in the image. Never invent symptoms.
- Give practical, low-cost recommendations a farmer with limited resources can act on.
- Use simple language understandable at a glance. Avoid jargon.
- If the image is unclear or too low-quality for a confident diagnosis, say so honestly.
- Always provide a confidence level: High, Moderate, or Low.
- If multiple conditions are possible, list them in order of likelihood.
- Include urgency: Critical (act today), High (act within 3 days), Moderate (act this week), Low (monitor).

Return ONLY valid JSON with this exact structure:
{
  "category": "crop|animal|produce|soil",
  "primary_diagnosis": "Name of the main condition identified",
  "confidence": "High|Moderate|Low",
  "urgency": "Critical|High|Moderate|Low",
  "summary": "One or two sentence plain-language summary of what you see.",
  "findings": ["finding 1", "finding 2", "..."],
  "differential_diagnoses": ["other possible condition 1", "other possible condition 2"],
  "immediate_actions": ["action 1", "action 2", "action 3"],
  "treatment": ["treatment step 1", "treatment step 2"],
  "prevention": ["prevention tip 1", "prevention tip 2"],
  "when_to_escalate": "Describe when the farmer should contact an extension officer or vet.",
  "confidence_note": "Brief note on what limited or supported your confidence."
}"""


async def clinic_diagnose(
    image_b64: str,
    mime_type: str,
    category: str,
    description: str,
    location: str | None = None,
    crop_or_animal: str | None = None,
) -> dict:
    """
    Send image + context to Gemini Vision and return structured diagnosis.

    Parameters
    ----------
    image_b64 : str
        Base64-encoded image data (no data-URI prefix).
    mime_type : str
        MIME type, e.g. 'image/jpeg', 'image/png'.
    category : str
        One of: crop, animal, produce, soil.
    description : str
        Farmer's description of what they are seeing / their concern.
    location : str | None
        Optional location context (e.g. "Western Kenya, altitude 1800m").
    crop_or_animal : str | None
        Optional specific crop or animal type (e.g. "maize", "dairy cow").
    """
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        return _fallback_error("Gemini API key is not configured.")

    # Build the user text prompt
    cat_ctx = _CATEGORY_CONTEXT.get(category, _CATEGORY_CONTEXT["crop"])
    context_parts = [cat_ctx]
    if crop_or_animal:
        context_parts.append(f"Subject: {crop_or_animal}.")
    if location:
        context_parts.append(f"Location context: {location}.")
    if description.strip():
        context_parts.append(f"Farmer's description: {description.strip()}")

    user_text = " ".join(context_parts) + "\n\nAnalyze the image and return your diagnosis as JSON."

    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={gemini_key}"
    )

    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
        "contents": [
            {
                "parts": [
                    # Inline image as base64
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_b64,
                        }
                    },
                    # Text context alongside the image
                    {"text": user_text},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,   # lower temperature → more consistent diagnostics
        },
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()

        text = (
            data.get("candidates", [])[0]
            .get("content", {})
            .get("parts", [])[0]
            .get("text", "")
        )
        if not text:
            return _fallback_error("Gemini returned an empty response.")

        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        result = json.loads(text)
        result["_source"] = "Gemini Vision"
        result["_model"] = model
        return result

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Gemini Vision HTTP error %s: %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        return _fallback_error(f"Gemini Vision API error {exc.response.status_code}.")
    except json.JSONDecodeError as exc:
        logger.warning("Gemini Vision returned invalid JSON: %s", exc)
        return _fallback_error("AI returned a malformed response. Please try again.")
    except Exception as exc:
        logger.warning("Gemini Vision failed: %s", exc)
        return _fallback_error(str(exc))


def _fallback_error(reason: str) -> dict:
    return {
        "error": True,
        "primary_diagnosis": "Diagnosis unavailable",
        "confidence": "Low",
        "urgency": "Low",
        "summary": f"The AI clinic could not complete the diagnosis. {reason}",
        "findings": [],
        "differential_diagnoses": [],
        "immediate_actions": [
            "Check your internet connection and try again.",
            "Ensure the image is clear and well-lit.",
            "Contact your local agricultural extension officer for in-person advice.",
        ],
        "treatment": [],
        "prevention": [],
        "when_to_escalate": "If the condition is worsening rapidly, contact a vet or extension officer immediately.",
        "confidence_note": reason,
        "_source": "Local fallback",
    }
