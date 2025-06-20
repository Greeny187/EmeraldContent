import logging
import os
import asyncio
from telegram import Bot


class TelegramErrorHandler(logging.Handler):
    def __init__(self, bot_token, chat_id):
        super().__init__(level=logging.ERROR)
        self.bot = Bot(token=bot_token)
        self.chat_id = chat_id

    def emit(self, record):
        try:
            msg = self.format(record)
            asyncio.create_task(self.bot.send_message (chat_id=self.chat_id, text=f"⚠️ *Bot Error*\n{msg}", parse_mode="MarkdownV2"
            ))
        except Exception:
            pass


def setup_logging():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(format=fmt, level=getattr(logging, log_level, logging.INFO))
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        level=logging.DEBUG,  # vorher vielleicht INFO
    )


    # File-Handler
    fh = logging.FileHandler("bot.log", encoding="utf-8")
    fh.setLevel(log_level)
    fh.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(fh)

    # Telegram Error-Handler
    dev_chat = os.getenv("DEVELOPER_CHAT_ID")
    bot_token = os.getenv("BOT_TOKEN")
    if dev_chat and bot_token:
        th = TelegramErrorHandler(bot_token, dev_chat)
        th.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(th)