"""
XOVIC Helpdesk - Main Entry Point
รัน Telegram Bot + FastAPI Web Server พร้อมกัน
"""
import logging
import sys
import os
import asyncio
import threading
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def start_web_server(sheets_handler, gemini_handler, host: str, port: int):
    """รัน FastAPI ใน thread แยก"""
    import uvicorn
    from server import create_app

    app = create_app(sheets_handler, gemini_handler)
    logger.info(f"🌐 Web server starting at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main():
    logger.info("🚀 Starting XOVIC Helpdesk (Bot + Web)...")

    # ── Environment Variables ──────────────────
    TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_TEAM_CHAT_ID = os.getenv("TELEGRAM_TEAM_CHAT_ID")
    GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY")
    GOOGLE_SHEETS_ID     = os.getenv("GOOGLE_SHEETS_ID")
    WEB_HOST             = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT             = int(os.getenv("WEB_PORT", "8000"))

    for name, val in [
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("GEMINI_API_KEY",     GEMINI_API_KEY),
        ("GOOGLE_SHEETS_ID",   GOOGLE_SHEETS_ID),
    ]:
        if not val:
            logger.error(f"❌ Missing {name} in .env")
            return

    logger.info("✅ Environment variables loaded")

    # ── Import Modules ─────────────────────────
    try:
        from modules.sheets_handler import SheetsHandlerOAuth
    except ImportError:
        logger.error("❌ ไม่พบ modules/sheets_handler.py")
        return

    try:
        from modules.telegram_handler import TelegramHandler
        from modules.gemini_handler import GeminiHandler
        from modules.message_templates import MessageTemplates
    except ImportError as e:
        logger.error(f"❌ Import error: {e}")
        return

    # ── Initialize Handlers ────────────────────
    logger.info("📊 Initializing Google Sheets handler...")
    sheets_handler = SheetsHandlerOAuth(
        credentials_file="client_secret.json",
        token_file="token.pickle",
        sheet_id=GOOGLE_SHEETS_ID,
    )
    logger.info("✅ Google Sheets ready")

    logger.info("🤖 Initializing Gemini AI handler...")
    gemini_handler = GeminiHandler(api_key=GEMINI_API_KEY)
    logger.info("✅ Gemini AI ready")

    logger.info("📝 Loading message templates...")
    message_templates = MessageTemplates()
    logger.info("✅ Message templates loaded")

    # ── Start Web Server (background thread) ───
    web_thread = threading.Thread(
        target=start_web_server,
        args=(sheets_handler, gemini_handler, WEB_HOST, WEB_PORT),
        daemon=True,
    )
    web_thread.start()
    logger.info(f"🌐 Web server running at http://{WEB_HOST}:{WEB_PORT}")

    # ── Start Telegram Bot ─────────────────────
    logger.info("📱 Initializing Telegram bot...")
    bot = TelegramHandler(
        token=TELEGRAM_BOT_TOKEN,
        sheets_handler=sheets_handler,
        gemini_handler=gemini_handler,
        message_templates=message_templates,
    )

    if TELEGRAM_TEAM_CHAT_ID:
        bot.team_chat_id = TELEGRAM_TEAM_CHAT_ID
        logger.info(f"👥 Team notifications → {TELEGRAM_TEAM_CHAT_ID}")
    else:
        logger.warning("⚠️ TELEGRAM_TEAM_CHAT_ID ไม่ได้ตั้งค่า — ปิดการแจ้งเตือนทีม")

    logger.info("🎉 XOVIC Helpdesk is running! (Ctrl+C to stop)")
    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("👋 หยุดระบบโดยผู้ใช้")


if __name__ == "__main__":
    main()