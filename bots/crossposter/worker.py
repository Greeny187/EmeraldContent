
# bots/content/crossposter_worker.py
# Routing/Versand (mandantenfähig: Logs mit tenant_id).

import hashlib
from typing import Dict, Any
from telegram import Update
from telegram.ext import ContextTypes
from common.database import get_pool

async def _hash_message(update: Update) -> str:
    text = update.effective_message.text or update.effective_message.caption or ""
    media_id = None
    if update.effective_message.photo:
        media_id = update.effective_message.photo[-1].file_unique_id
    elif update.effective_message.document:
        media_id = update.effective_message.document.file_unique_id
    payload = f"{update.effective_chat.id}|{update.effective_message.message_id}|{text}|{media_id or ''}"
    return hashlib.sha256(payload.encode()).hexdigest()

async def _apply_transform(text: str, transform: Dict[str, Any]) -> str:
    if transform.get("plain_text"):
        text = text.replace("*","").replace("_","").replace("`","").replace("[","").replace("]","")
    return f"{transform.get('prefix','')}{text}{transform.get('suffix','')}"

async def route_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pool = await get_pool()
    # Alle aktiven Routen (mandanten-agnostisch im Worker: tenant_id wird pro Route aus DB geholt)
    routes = await pool.fetch(
        "SELECT id, tenant_id, destinations, transform, filters FROM crossposter_routes WHERE source_chat_id=$1 AND active=TRUE",
        chat_id
    )
    if not routes:
        return

    dedup_hash = await _hash_message(update)
    text = update.effective_message.text or update.effective_message.caption or ""

    for r in routes:
        wl = set(r["filters"].get("hashtags_whitelist", []))
        bl = set(r["filters"].get("hashtags_blacklist", []))
        tags = {t for t in text.split() if t.startswith('#')}
        if wl and not (tags & set(f"#{t}" for t in wl)):
            continue
        if bl and (tags & set(f"#{t}" for t in bl)):
            continue

        final_text = await _apply_transform(text, r["transform"])

        for dest in r["destinations"]:
            if dest.get("type") == "telegram" and dest.get("chat_id"):
                try:
                    if update.effective_message.photo or update.effective_message.document:
                        await context.bot.copy_message(
                            chat_id=dest["chat_id"], from_chat_id=chat_id,
                            message_id=update.effective_message.message_id,
                            caption=final_text or None
                        )
                    else:
                        await context.bot.send_message(dest["chat_id"], final_text or text)

                    await pool.execute(
                        "INSERT INTO crossposter_logs (tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, dedup_hash) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        r["tenant_id"], r["id"], chat_id, update.effective_message.message_id, dest, "sent", dedup_hash
                    )
                except Exception as e:
                    await pool.execute(
                        "INSERT INTO crossposter_logs (tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, error, dedup_hash) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                        r["tenant_id"], r["id"], chat_id, update.effective_message.message_id, dest, "error", str(e), dedup_hash
                    )
