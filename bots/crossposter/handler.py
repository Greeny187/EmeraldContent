import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # .../app
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import hashlib
from typing import Dict, Any
from telegram import Update
from telegram.ext import ContextTypes
from .database import get_pool
from .x_client import post_text as x_post_text
import httpx

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
        for ch in ["*","_","`","[","]"]:
            text = text.replace(ch,"")
    return f"{transform.get('prefix','')}{text}{transform.get('suffix','')}"

async def discord_post(webhook_url: str, content: str, username: str = None, avatar_url: str = None):
    data = {"content": content}
    if username: data["username"] = username
    if avatar_url: data["avatar_url"] = avatar_url
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(webhook_url, json=data)
        r.raise_for_status()

async def _get_x_access_token(tenant_id: int):
    pool = await get_pool()
    row = await pool.fetchrow("SELECT config FROM connectors WHERE tenant_id=$1 AND type='x' AND active=TRUE ORDER BY id DESC LIMIT 1", tenant_id)
    if row and row["config"] and "access_token" in row["config"]:
        return row["config"]["access_token"]
    return os.environ.get("X_ACCESS_TOKEN")

async def route_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pool = await get_pool()
    routes = await pool.fetch("SELECT id, tenant_id, destinations, transform, filters FROM crossposter_routes WHERE source_chat_id=$1 AND active=TRUE", chat_id)
    if not routes:
        return

    dedup_hash = await _hash_message(update)
    text = update.effective_message.text or update.effective_message.caption or ""

    # Single pass over routes & destinations
    for r in routes:
        wl = set(r["filters"].get("hashtags_whitelist", []))
        bl = set(r["filters"].get("hashtags_blacklist", []))
        tags = {t for t in text.split() if t.startswith('#')}
        if wl and not (tags & set(f"#{t}" for t in wl)): continue
        if bl and (tags & set(f"#{t}" for t in bl)): continue

        final_text = await _apply_transform(text, r["transform"])

        for dest in r["destinations"]:
            try:
                if dest.get("type") == "telegram" and dest.get("chat_id"):
                    if update.effective_message.photo or update.effective_message.document:
                        await context.bot.copy_message(chat_id=dest["chat_id"], from_chat_id=chat_id, message_id=update.effective_message.message_id, caption=final_text or None)
                    else:
                        await context.bot.send_message(dest["chat_id"], final_text or text)
                    await pool.execute(
                        "INSERT INTO crossposter_logs (tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, dedup_hash) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        r["tenant_id"], r["id"], chat_id, update.effective_message.message_id, dest, "sent", dedup_hash
                    )
                elif dest.get("type") == "x":
                    token = await _get_x_access_token(r["tenant_id"])
                    if not token: raise Exception("Kein X Access Token konfiguriert")
                    await x_post_text(token, final_text or text)
                    await pool.execute(
                        "INSERT INTO crossposter_logs (tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, dedup_hash) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        r["tenant_id"], r["id"], chat_id, update.effective_message.message_id, dest, "sent", dedup_hash
                    )
                elif dest.get("type") in ("discord","discord_webhook"):
                    url = dest.get("webhook_url")
                    if not url: raise Exception("Discord Webhook URL fehlt")
                    await discord_post(url, final_text or text, dest.get("username"), dest.get("avatar_url"))
                    await pool.execute(
                        "INSERT INTO crossposter_logs (tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, dedup_hash) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        r["tenant_id"], r["id"], chat_id, update.effective_message.message_id, dest, "sent", dedup_hash
                    )
            except Exception as e:
                await pool.execute(
                    "INSERT INTO crossposter_logs (tenant_id, route_id, source_chat_id, source_message_id, dest_descriptor, status, error, dedup_hash) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                    r["tenant_id"], r["id"], chat_id, update.effective_message.message_id, dest, "error", str(e), dedup_hash
                )
