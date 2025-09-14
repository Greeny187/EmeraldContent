import os, json, urllib.parse, logging
from typing import List, Tuple
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
)
from telegram.ext import (
    CommandHandler, MessageHandler, ContextTypes, filters, Application
)

logger = logging.getLogger(__name__)

# URL deiner gehosteten index.html (Canvas-Version) – per ENV überschreibbar
MINIAPP_URL = os.getenv(
    "MINIAPP_URL",
    "https://greeny187.github.io/GreenyManagementBots/index.html"
)

# --- DB-Fallback-Imports ------------------------------------------------------
def _db():
    """Liefert die DB-Funktionen, egal ob shared.* vorhanden ist oder lokale .database."""
    try:
        from shared.database import (
            get_registered_groups,
            set_welcome, delete_welcome,
            get_link_settings, set_link_settings,
            get_ai_settings, set_ai_settings,
        )
    except Exception:
        from shared.database import (
            get_registered_groups,
            set_welcome, delete_welcome,
            get_link_settings, set_link_settings,
            get_ai_settings, set_ai_settings,
        )
    return {
        "get_registered_groups": get_registered_groups,
        "set_welcome": set_welcome, "delete_welcome": delete_welcome,
        "get_link_settings": get_link_settings, "set_link_settings": set_link_settings,
        "get_ai_settings": get_ai_settings, "set_ai_settings": set_ai_settings,
    }

# --- Helper -------------------------------------------------------------------
async def _is_admin_or_owner(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """True, wenn der Nutzer in chat_id Admin/Owner ist (ein get_chat_member-Call)."""
    try:
        cm = await context.bot.get_chat_member(chat_id, user_id)
        status = (getattr(cm, "status", "") or "").lower()
        return status in ("administrator", "creator")
    except Exception as e:
        logger.debug(f"[miniapp] get_chat_member({chat_id},{user_id}) failed: {e}")
        return False

# --- /miniapp Befehl ----------------------------------------------------------
async def miniapp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt Buttons, um die Mini-App pro Gruppe zu öffnen."""
    if not update.effective_user or not update.effective_message:
        return
    user = update.effective_user
    msg  = update.effective_message

    db = _db()
    try:
        all_groups: List[Tuple[int, str]] = db["get_registered_groups"]() or []
    except Exception as e:
        logger.warning(f"[miniapp] get_registered_groups failed: {e}")
        all_groups = []

    visible: List[Tuple[int, str]] = []
    for cid, title in all_groups:
        if not isinstance(cid, int):
            # falls DB (cid, title) anders liefert
            try:
                cid = int(cid)
            except Exception:
                continue
        if await _is_admin_or_owner(context, cid, user.id):
            visible.append((cid, title))

    if not visible:
        return await msg.reply_text("Keine Gruppe gefunden, in der du Admin bist.")

    rows = []
    for cid, title in visible:
        url = f"{MINIAPP_URL}?cid={cid}&title={urllib.parse.quote(title or str(cid))}"
        rows.append([InlineKeyboardButton(f"{title or cid} – Mini-App öffnen", web_app=WebAppInfo(url=url))])

    await msg.reply_text("Wähle eine Gruppe:", reply_markup=InlineKeyboardMarkup(rows))

# --- Rückkanal der Mini-App ---------------------------------------------------
async def webapp_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Empfängt Daten aus der Mini-App (update.message.web_app_data.data) und speichert sie."""
    msg = update.message
    if not msg or not getattr(msg, "web_app_data", None):
        return

    try:
        data = json.loads(msg.web_app_data.data or "{}")
    except Exception:
        return await msg.reply_text("❌ Ungültige Daten von der Mini-App.")

    cid_raw = data.get("cid")
    try:
        cid = int(cid_raw)
    except Exception:
        return await msg.reply_text("❌ Gruppen-ID fehlt oder ist ungültig.")

    # Sicherheitscheck: Absender muss Admin/Owner in der Zielgruppe sein
    if not await _is_admin_or_owner(context, cid, update.effective_user.id):
        return await msg.reply_text("❌ Du bist in dieser Gruppe kein Admin.")

    db = _db()
    errors: List[str] = []

    # Welcome speichern/löschen
    try:
        if data.get("welcome_on"):
            text = (data.get("welcome_text") or "Willkommen {user} 👋").strip()
            db["set_welcome"](cid, text)
        else:
            db["delete_welcome"](cid)
    except Exception as e:
        errors.append(f"Welcome: {e}")

    # Spam-Level speichern (legt/erweitert Konfig-Dict)
    try:
        cfg = db["get_link_settings"](cid) or {}
        cfg["spam_level"] = data.get("spam_level", "mid")
        db["set_link_settings"](cid, cfg)
    except Exception as e:
        errors.append(f"Spam: {e}")

    # FAQ-KI speichern
    try:
        ai_faq_old, ai_rss = db["get_ai_settings"](cid)
        ai_faq_new = bool(data.get("faq_ai"))
        db["set_ai_settings"](cid, ai_faq_new, ai_rss)
    except Exception as e:
        errors.append(f"KI: {e}")

    if errors:
        return await msg.reply_text("⚠️ Teilweise gespeichert:\n• " + "\n• ".join(errors))
    return await msg.reply_text("✅ Einstellungen gespeichert.")

# --- Öffentliche Registrierung ------------------------------------------------
def register_miniapp(app: Application):
    """Von app.register(...) oder deiner main.py aufrufen."""
    # /miniapp sehr früh, damit Nutzer sie leicht finden
    app.add_handler(CommandHandler("miniapp", miniapp_cmd), group=-3)

    # WebApp-Daten kommen als Message im Privat-Chat.
    # Wir filtern locker und prüfen im Handler selbst auf msg.web_app_data.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, webapp_data_handler), group=0)

    logger.info("miniapp: handlers registered")