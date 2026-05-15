import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


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

import asyncio
from livekit.agents import JobContext, WorkerOptions, cli, stt, tts, llm, AutoSubscribe
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import silero, deepgram, elevenlabs
try:
    from livekit.plugins import google as google_plugin
except ImportError:
    google_plugin = None


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-agent")

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
DEFAULT_CONFIG = {
    "first_line": "Hello! This is Aryan from Keevin IT Solutions. We provide premium IT services and security systems. How can I help you today?",
    "agent_instructions": "",
    "gemini_live_model": "gemini-3.1-flash-native-audio-preview",
    "gemini_live_voice": "Puck",
    "gemini_live_language": "",
    "gemini_tts_model": "gemini-3.1-flash-tts-preview",
}


def read_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def get_setting(config: dict, key: str, env_key: str, default: str = "") -> str:
    value = config.get(key)
    if value not in (None, ""):
        return str(value)
    return os.getenv(env_key, default)


class MinimalVoiceAgent(Agent):
    def __init__(self, config: dict) -> None:
        prompt = str(config.get("agent_instructions") or "").strip()
        instructions = prompt or (
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
        )

        super().__init__(
            instructions=instructions,
            stt=self._build_stt(config),
            llm=self._build_llm(config),
            tts=self._build_tts(config),
        )
        self._first_line = str(config.get("first_line") or DEFAULT_CONFIG["first_line"]).strip()

    def _build_stt(self, config: dict):
        api_key = str(config.get("deepgram_api_key") or os.environ.get("DEEPGRAM_API_KEY") or "").strip()
        return deepgram.STT(api_key=api_key)

    def _build_llm(self, config: dict):
        if google_plugin is None:
            raise RuntimeError("livekit-plugins-google is required to start the voice agent.")
        model = str(config.get("gemini_live_model") or DEFAULT_CONFIG["gemini_live_model"]).strip()
        api_key = str(config.get("google_api_key") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        return google_plugin.LLM(model=model, api_key=api_key)

    def _build_tts(self, config: dict):
        api_key = str(config.get("elevenlabs_api_key") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        return elevenlabs.TTS(
            api_key=api_key, 
            voice_id="pNInz6obpgnuS75pcn9f",
            model_id="eleven_monolingual_v1"
        )

    async def on_enter(self):
        if self._first_line:
            await self.session.generate_reply(instructions=f'Say exactly this opening line: "{self._first_line}"')


async def entrypoint(ctx: JobContext):
    config = read_config()
    logger.info("Worker joined room: %s", ctx.room.name)
    
    agent = MinimalVoiceAgent(config)
    session = AgentSession(
        vad=silero.VAD.load(),
        turn_detection="stt",
        min_endpointing_delay=0.15,
    )
    
    await session.start(
        agent=agent,
        room=ctx.room,
    )
    
    # Trigger the opening greeting immediately after session starts
    if agent._first_line:
        await session.say(agent._first_line, allow_interruptions=True)
    
    # Keep the agent alive
    await asyncio.Future()


if __name__ == "__main__":
    config = read_config()
    if config.get("livekit_url"):
        os.environ["LIVEKIT_URL"] = str(config["livekit_url"]).strip()
    if config.get("livekit_api_key"):
        os.environ["LIVEKIT_API_KEY"] = str(config["livekit_api_key"]).strip()
    if config.get("livekit_api_secret"):
        os.environ["LIVEKIT_API_SECRET"] = str(config["livekit_api_secret"]).strip()

    worker_host = str(os.environ.get("AGENT_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    worker_port = int(str(os.environ.get("AGENT_PORT") or "8081"))
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="outbound-caller",
            host=worker_host,
            port=worker_port,
        )
    )
