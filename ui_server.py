import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import db
from llm_config import apply_llm_defaults
from outbound_calls import dispatch_outbound_call


def _bootstrap_venv() -> None:
    project_dir = Path(__file__).resolve().parent
    venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return
    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except Exception:
        return
    if current_python == target_python:
        return
    os.execv(str(target_python), [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_bootstrap_venv()
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
DASHBOARD_DIR = BASE_DIR / "dashboard"
DASHBOARD_JS_DIR = DASHBOARD_DIR / "js"
MESSAGE_ASSET_DIR = BASE_DIR / "data" / "message_assets"

DEFAULT_CONFIG = {
    "first_line": "Hello! This is Aryan from Keevin IT Solutions. We provide premium IT services and security systems. How can I help you today?",
    "agent_instructions": (
        "## ROLE\n"
        "You are Aryan, a polite and professional sales assistant from Keevin IT Solutions.\n\n"
        "## CONVERSATIONAL STYLE (General Intelligence)\n"
        "- Be warm and friendly. You may engage in small talk, offer 'Namaste,' and respond to general polite inquiries (e.g., 'How are you?').\n"
        "- Keep replies concise and voice-optimized.\n\n"
        "## BUSINESS FACTS (Strict Knowledge Base)\n"
        "- For ALL questions regarding Keevin IT Solutions services, pricing, technical details, or policies, you must ONLY use information provided in the Knowledge Base below.\n"
        "- If a user asks a business-specific question that is NOT in the Knowledge Base, DO NOT make up an answer.\n"
        "- Instead, say: 'That is a great question. I don't have that specific detail in front of me right now, but I can have a team member follow up with you on that. Would you like that?'\n\n"
        "## GUARDRAILS\n"
        "- Never discuss competitors.\n"
        "- Never promise discounts unless explicitly mentioned in the Knowledge Base.\n"
        "- If the user asks about unrelated topics (like global politics or sports), politely steer the conversation back to their IT service needs.\n\n"
        "<Knowledge Base>\n"
        "Company: Keevin IT Solutions (Premium IT Services and Security Systems)\n"
        "Services: CCTV installation, Biometric systems, Networking solutions, and Software development.\n"
        "Pricing: Custom pricing based on project requirements.\n"
        "</Knowledge Base>"
    ),
    "voice_mode": "gemini_live",
    "llm_provider": "gemini",
    "llm_model": "gemini-3.1-flash-native-audio-preview",
    "gemini_live_model": "gemini-3.1-flash-native-audio-preview",
    "gemini_live_voice": "Puck",
    "gemini_live_temperature": 0.6,
    "gemini_live_language": "",
    "gemini_tts_model": "gemini-3.1-flash-tts-preview",
    "livekit_url": "",
    "livekit_api_key": "",
    "livekit_api_secret": "",
    "sip_trunk_id": "",
    "google_api_key": "",
    "lang_preset": "multilingual",
}
SECRET_KEYS = {
    "google_api_key", "livekit_api_key", "livekit_api_secret", 
    "supabase_key", "deepgram_api_key", "elevenlabs_api_key"
}

app = FastAPI(title="SPX AI Dashboard")
if DASHBOARD_JS_DIR.exists():
    app.mount("/dashboard-js", StaticFiles(directory=str(DASHBOARD_JS_DIR)), name="dashboard-js")


def _load_config_file() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to read config.json: %s", exc)
        return {}


def _clean_config_payload(payload: dict | None) -> dict:
    raw = dict(payload or {})
    clean = dict(DEFAULT_CONFIG)
    clean.update({k: v for k, v in raw.items() if v is not None})
    clean["voice_mode"] = str(clean.get("voice_mode") or "gemini_live").strip() or "gemini_live"
    clean["gemini_live_model"] = str(clean.get("gemini_live_model") or DEFAULT_CONFIG["gemini_live_model"]).strip()
    clean["gemini_live_voice"] = str(clean.get("gemini_live_voice") or DEFAULT_CONFIG["gemini_live_voice"]).strip()
    clean["gemini_live_language"] = str(clean.get("gemini_live_language") or "").strip()
    clean["gemini_tts_model"] = str(clean.get("gemini_tts_model") or DEFAULT_CONFIG["gemini_tts_model"]).strip()
    clean["lang_preset"] = str(clean.get("lang_preset") or DEFAULT_CONFIG["lang_preset"]).strip()
    try:
        clean["gemini_live_temperature"] = float(clean.get("gemini_live_temperature", 0.6))
    except (TypeError, ValueError):
        clean["gemini_live_temperature"] = 0.6
    clean["gemini_live_temperature"] = max(0.0, min(2.0, clean["gemini_live_temperature"]))
    return apply_llm_defaults(clean)


def read_config(include_secrets: bool = True) -> dict:
    # Always returning secrets for local development convenience as requested by user
    return _clean_config_payload(_load_config_file())


def write_config(payload: dict) -> dict:
    current = _load_config_file()
    current.update(payload or {})
    cleaned = _clean_config_payload(current)
    CONFIG_FILE.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    return cleaned


def configure_supabase_env(config: dict) -> None:
    os.environ["SUPABASE_URL"] = str(config.get("supabase_url") or os.environ.get("SUPABASE_URL", "")).strip()
    os.environ["SUPABASE_KEY"] = str(config.get("supabase_key") or os.environ.get("SUPABASE_KEY", "")).strip()


def _call_status(row: dict) -> str:
    if row.get("was_booked"):
        return "booked"
    summary = str(row.get("summary") or "").lower()
    if "book" in summary or "appointment" in summary or "site visit" in summary:
        return "booked"
    if row.get("duration_seconds"):
        return "completed"
    return "unknown"


def _serialize_log(row: dict) -> dict:
    data = dict(row)
    data["status"] = _call_status(data)
    return data


def _message_assets() -> list[dict]:
    if not MESSAGE_ASSET_DIR.exists():
        return []
    items = []
    for path in sorted(MESSAGE_ASSET_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        stat = path.stat()
        items.append(
            {
                "id": path.name,
                "name": path.name,
                "filename": path.name,
                "size": stat.st_size,
                "content_type": "",
                "public_url": f"/api/message-assets/{path.name}",
            }
        )
    return items


@app.exception_handler(Exception)
async def api_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse({"status": "error", "message": "Internal server error."}, status_code=500)


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(DASHBOARD_DIR / "index.html")


@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    return HTMLResponse(
        """
        <html><head><title>SPX AI Demo</title></head>
        <body style="font-family: Arial, sans-serif; background:#0b0d12; color:#e8eaef; padding:40px;">
        <h1>SPX AI Demo</h1>
        <p>This lightweight branch keeps the dashboard and core voice-agent controls.</p>
        </body></html>
        """
    )


from livekit import api

@app.post("/api/demo/start")
async def api_demo_start():
    config = read_config(include_secrets=True)
    lk_url = str(config.get("livekit_url") or os.environ.get("LIVEKIT_URL", "")).strip()
    lk_api_key = str(config.get("livekit_api_key") or os.environ.get("LIVEKIT_API_KEY", "")).strip()
    lk_api_secret = str(config.get("livekit_api_secret") or os.environ.get("LIVEKIT_API_SECRET", "")).strip()
    
    missing = []
    if not lk_url: missing.append("LiveKit URL")
    if not lk_api_key: missing.append("LiveKit API Key")
    if not lk_api_secret: missing.append("LiveKit API Secret")
    
    if missing:
        raise HTTPException(status_code=400, detail=f"Please configure: {', '.join(missing)} in Agent Settings.")
        
    room_name = f"webrtc-demo-{uuid.uuid4().hex[:8]}"
    participant_identity = f"demo-user-{uuid.uuid4().hex[:4]}"
    
    token = api.AccessToken(lk_api_key, lk_api_secret) \
        .with_identity(participant_identity) \
        .with_name("WebRTC User") \
        .with_grants(api.VideoGrants(room_join=True, room=room_name)) \
        .to_jwt()
        
    lk = api.LiveKitAPI(url=lk_url, api_key=lk_api_key, api_secret=lk_api_secret)
    try:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=room_name,
                metadata=json.dumps({"demo": True})
            )
        )
    except Exception as exc:
        await lk.aclose()
        raise HTTPException(status_code=500, detail=f"Failed to dispatch agent: {str(exc)}")
    await lk.aclose()
    
    return {"status": "success", "token": token, "url": lk_url, "room": room_name}


@app.get("/api/config")
async def api_get_config():
    return read_config(include_secrets=True)


@app.post("/api/config")
async def api_post_config(request: Request):
    payload = await request.json()
    write_config(payload)
    return {"status": "success"}


@app.get("/api/stats")
async def api_get_stats():
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    stats = db.fetch_stats()
    stats["active_sessions"] = 1
    return stats


@app.get("/api/logs")
async def api_get_logs():
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    return [_serialize_log(row) for row in db.fetch_call_logs(limit=100)]


@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str):
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    rows = db.fetch_call_logs(limit=200)
    match = next((row for row in rows if str(row.get("id")) == str(log_id)), None)
    if not match:
        raise HTTPException(status_code=404, detail="Call log not found.")
    text = []
    text.append(f"Call Log - {match.get('created_at', '')}")
    text.append(f"Phone: {match.get('phone_number', 'Unknown')}")
    text.append(f"Duration: {match.get('duration_seconds', 0)}s")
    text.append(f"Summary: {match.get('summary', '')}")
    text.append("")
    text.append("--- TRANSCRIPT ---")
    text.append(match.get("transcript") or "No transcript available.")
    return PlainTextResponse("\n".join(text), media_type="text/plain")


@app.get("/api/appointments")
async def api_get_appointments(start: str | None = None, end: str | None = None):
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    return db.fetch_appointments(start_iso=start, end_iso=end, limit=500)


@app.post("/api/appointments")
async def api_create_appointment(request: Request):
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    payload = await request.json()
    try:
        appointment = db.create_appointment(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "appointment": appointment}


@app.patch("/api/appointments/{appointment_id}")
async def api_update_appointment(appointment_id: str, request: Request):
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    payload = await request.json()
    try:
        appointment = db.update_appointment(appointment_id, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "appointment": appointment}


@app.post("/api/call/single")
async def api_call_single(request: Request):
    config = read_config(include_secrets=True)
    payload = await request.json()
    phone_number = db.normalize_phone_number(str(payload.get("phone") or payload.get("phone_number") or ""))
    if not phone_number:
        raise HTTPException(status_code=400, detail="Phone number is required.")
    result = await dispatch_outbound_call(
        phone_number,
        config=config,
        caller_name=str(payload.get("caller_name") or "").strip(),
    )
    return {"status": "success", **result}


@app.post("/api/call/bulk")
async def api_call_bulk(request: Request):
    config = read_config(include_secrets=True)
    payload = await request.json()
    numbers_text = str(payload.get("numbers") or "").strip()
    numbers = [db.normalize_phone_number(line.split(",")[0]) for line in numbers_text.splitlines() if line.strip()]
    numbers = [number for number in numbers if number]
    if not numbers:
        raise HTTPException(status_code=400, detail="At least one phone number is required.")
    results = []
    for number in numbers:
        try:
            result = await dispatch_outbound_call(number, config=config)
            results.append({"phone_number": number, "status": "queued", **result})
        except Exception as exc:
            results.append({"phone_number": number, "status": "failed", "error": str(exc)})
    return {"status": "success", "items": results}


@app.get("/api/automation/jobs")
async def api_automation_jobs():
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    return {"items": db.list_automation_jobs(limit=200)}


@app.patch("/api/automation/jobs/{job_id}")
async def api_update_automation_job(job_id: str, request: Request):
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    payload = await request.json()
    job = db.update_automation_job(job_id, payload)
    if not job:
        raise HTTPException(status_code=404, detail="Automation job not found.")
    return {"status": "success", "job": job}


@app.post("/api/automation/jobs/{job_id}/launch-call")
async def api_launch_automation_call(job_id: str):
    config = read_config(include_secrets=True)
    configure_supabase_env(config)
    job = db.get_automation_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Automation job not found.")
    phone_number = db.normalize_phone_number(job.get("phone_number") or "")
    if not phone_number:
        raise HTTPException(status_code=400, detail="Automation job is missing a phone number.")
    result = await dispatch_outbound_call(
        phone_number,
        config=config,
        caller_name=str(job.get("caller_name") or "").strip(),
    )
    db.update_automation_job(job_id, {"status": "launched", "dispatch_id": result.get("dispatch_id")})
    return {"status": "success", **result}


@app.get("/api/whatsapp/templates")
async def api_whatsapp_templates():
    return {"items": []}


@app.get("/api/message-assets")
async def api_message_assets():
    return {"items": _message_assets()}


@app.get("/api/message-assets/{asset_name}")
async def api_message_asset_download(asset_name: str):
    asset_path = MESSAGE_ASSET_DIR / asset_name
    if not asset_path.exists() or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(asset_path)


@app.post("/api/message-assets/upload")
async def api_upload_message_asset(file: UploadFile = File(...)):
    MESSAGE_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    target = MESSAGE_ASSET_DIR / safe_name
    content = await file.read()
    target.write_bytes(content)
    return {
        "status": "success",
        "item": {
            "id": safe_name,
            "name": safe_name,
            "filename": safe_name,
            "size": len(content),
            "content_type": file.content_type or "",
            "public_url": f"/api/message-assets/{safe_name}",
        },
    }


if __name__ == "__main__":
    import uvicorn

    host = str(os.environ.get("UI_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    port = int(str(os.environ.get("UI_PORT") or os.environ.get("PORT") or "8000"))
    uvicorn.run("ui_server:app", host=host, port=port, reload=False)
