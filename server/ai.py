import os, json, httpx, logging

logger = logging.getLogger(__name__)

async def ai_explain(req, risk, ndvi):
    gemini_key = os.getenv('GEMINI_API_KEY')
    openai_key  = os.getenv('OPENAI_API_KEY')

    system_instruction = (
        "You are MavunoGuard AI, a Kenyan smallholder-farm decision assistant. "
        "Give practical, low-cost actions based only on the supplied evidence. "
        "Never say 'seek agronomic advice'. Never invent measurements. "
        "Clearly distinguish observed data from interpretation. "
        "Use short simple sentences understandable at a glance. "
        "Return JSON with keys: headline, explanation, actions (array of strings), "
        "watch_next (array of strings), confidence_note."
    )
    user_content = json.dumps({"farm": req.model_dump(), "risk": risk, "satellite": ndvi})

    # ------------------------------------------------------------------
    # Primary: Google Gemini
    # Default model updated to gemini-3.6-flash (gemini-2.5-flash is
    # deprecated/unavailable for new API keys as of Sep 2026).
    # ------------------------------------------------------------------
    if gemini_key:
        model = os.getenv('GEMINI_MODEL', 'gemini-3.6-flash')
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={gemini_key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": user_content}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )
                r.raise_for_status()
                data = r.json()

            text = (
                data.get("candidates", [])[0]
                    .get("content", {})
                    .get("parts", [])[0]
                    .get("text", "")
            )
            if text:
                text = text.strip().removeprefix("```json").removesuffix("```").strip()
                return json.loads(text)
        except httpx.HTTPStatusError as exc:
            logger.warning("Gemini HTTP error %s: %s", exc.response.status_code, exc.response.text[:200])
        except Exception as exc:
            logger.warning("Gemini failed: %s", exc)

    # ------------------------------------------------------------------
    # Fallback: OpenAI
    # ------------------------------------------------------------------
    if openai_key and not openai_key.startswith("your_"):
        model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user",   "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
            text = data["choices"][0]["message"]["content"]
            return json.loads(text)
        except Exception as exc:
            logger.warning("OpenAI failed: %s", exc)

    return None
