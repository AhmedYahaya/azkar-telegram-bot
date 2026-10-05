"""Main entry point for Ahmed & Janna's shared Telegram companion bot."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    Defaults,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import CONFIG
from database import Database
from health import start_health_server, stop_health_server
from handlers import (
    callback_query,
    chat_id_command,
    focus_command,
    focus_message_guard,
    help_command,
    my_id_command,
    quran_command,
    report_command,
    reset_tasbeeh_command,
    set_audio_command,
    set_hidden_message_command,
    set_quote_command,
    start_command,
    status_command,
    tasbeeh_command,
)
from scheduler import schedule_all

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    db = Database(CONFIG.database_path)
    await db.connect()
    await db.migrate_legacy_ids(CONFIG.legacy_ahmed_user_id, CONFIG.legacy_janna_user_id)
    application.bot_data["db"] = db
    await schedule_all(application)
    await start_health_server(application, CONFIG.port)
    logger.info("Bot initialized in timezone %s", CONFIG.timezone_name)


async def post_shutdown(application: Application) -> None:
    await stop_health_server(application)
    db = application.bot_data.get("db")
    if db:
        await db.close()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled bot error", exc_info=context.error)


def build_application() -> Application:
    application = (
        ApplicationBuilder()
        .token(CONFIG.bot_token)
        .defaults(Defaults(tzinfo=CONFIG.timezone))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Group -1 is the Focus guard: it sees group messages before the normal handlers.
    application.add_handler(
        MessageHandler(filters.ChatType.GROUPS, focus_message_guard),
        group=-1,
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("myid", my_id_command))
    application.add_handler(CommandHandler("chatid", chat_id_command))
    application.add_handler(CommandHandler("help", help_command))

    # Friendly short aliases.
    application.add_handler(CommandHandler(["quote", "set_quote"], set_quote_command))
    application.add_handler(CommandHandler(["hidden", "set_hidden_message"], set_hidden_message_command))
    application.add_handler(CommandHandler("focus", focus_command))
    application.add_handler(CommandHandler("tasbeeh", tasbeeh_command))
    application.add_handler(CommandHandler(["reset_tasbeeh", "tasbeeh_reset"], reset_tasbeeh_command))
    application.add_handler(CommandHandler(["audio", "set_audio"], set_audio_command))
    application.add_handler(CommandHandler("quran", quran_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CallbackQueryHandler(callback_query))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    app = build_application()
    logger.info("Starting Telegram polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
