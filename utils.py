import os
import re
import asyncio
import logging
from urllib.parse import urlparse
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ExtBot
from telegram import ChatMember
from database import list_members, remove_member
from translator import translate_hybrid

logger = logging.getLogger(__name__)

# Heuristik: "Deleted Account" in verschiedenen Sprachen/Varianten
_DELETED_NAME_RX = re.compile(
    r"(deleted\s+account|gelösch(tes|ter)\s+(konto|account)|"
    r"(аккаунт\s+удалён|удалённый\s+аккаунт)|"
    r"(حساب\s+محذوف)|"
    r"(compte\s+supprimé)|"
    r"(cuenta\s+eliminada)|"
    r"(konto\s+gelöscht|konto\s+usunięte)|"
    r"(account\s+cancellato)|"
    r"(已删除的帐户|已刪除的帳號)|"
    r"(cont\s+șters)|"
    r"(счет\s+удален))",
    re.IGNORECASE
)

def _looks_deleted(user) -> bool:
    """Erkennt gelöschte Konten anhand Bot-API-Daten (Heuristik)."""
    if not user or getattr(user, "is_bot", False):
        return False
    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    # Typischerweise kein @username und Vorname wie "Deleted Account"
    if not getattr(user, "username", None) and _DELETED_NAME_RX.search(name or ""):
        return True
    return False

async def _get_member(bot: ExtBot, chat_id: int, user_id: int) -> ChatMember | None:
    try:
        return await bot.get_chat_member(chat_id, user_id)
    except RetryAfter as e:
        await asyncio.sleep(getattr(e, "retry_after", 1))
        return await bot.get_chat_member(chat_id, user_id)
    except BadRequest as e:
        # z. B. "Chat member not found" → nicht (mehr) Mitglied
        logger.debug(f"get_chat_member({chat_id},{user_id}) -> {e}")
        return None

async def clean_delete_accounts_for_chat(chat_id: int, bot: ExtBot, *, dry_run: bool = False) -> int:
    """
    Entfernt NUR gelöschte Accounts aus dem Chat (Ban+Unban) und räumt die DB-Mitgliederliste auf.
    Gibt die Anzahl tatsächlich entfernter (gekickter) gelöschter Accounts zurück.
    """
    user_ids = list_members(chat_id)  # aus eurer DB – keine Bot-API-Iteration nötig
    removed = 0

    for uid in user_ids:
        member = await _get_member(bot, chat_id, uid)
        if member is None:
            # Nicht (mehr) im Chat → nur DB aufräumen
            remove_member(chat_id, uid)
            continue

        # Admins/Owner NIE anfassen
        if member.status in ("administrator", "creator"):
            continue

        if _looks_deleted(member.user):
            if dry_run:
                removed += 1
                continue

            # Kick durch ban + optionales unban (so "verschwindet" der Ghost)
            try:
                await bot.ban_chat_member(chat_id, uid)
                try:
                    # Nur wenn gebannt – verhindert Fehlerflut
                    await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
                except BadRequest:
                    pass
                removed += 1
            except Forbidden as e:
                logger.warning(f"Keine Rechte um {uid} zu entfernen in {chat_id}: {e}")
            except BadRequest as e:
                logger.warning(f"Ban/Unban fehlgeschlagen für {uid} in {chat_id}: {e}")

            # Egal ob erfolgreich gebannt: aus eurer DB entfernen
            try:
                remove_member(chat_id, uid)
            except Exception as e:
                logger.debug(f"remove_member DB fail ({chat_id},{uid}): {e}")

    return removed

def tr(text: str, lang: str) -> str:
    try:
        return translate_hybrid(text, lang)
    except Exception as e:
        logger.error(f"Fehler in tr(): {e}")
        return text

def is_deleted_account(member) -> bool:
    """
    Erkenne gelöschte Accounts nur über Namensprüfung:
    - Telegram ersetzt first_name durch 'Deleted Account'
    - oder entfernt alle Namen/Username
    """
    user = member.user
    first = (user.first_name or "").lower()
    # 1) Default-Titel 'Deleted Account' (manchmal abweichend 'Deleted account')
    if first.startswith("deleted account"):
        return True
    # 2) Kein Name, kein Username mehr vorhanden
    if not any([user.first_name, user.last_name, user.username]):
        return True
    return False

async def ai_summarize(text: str, lang: str = "de") -> str | None:
    """
    Sehr knapper TL;DR (1–2 Sätze) in 'lang'.
    - Opt-in per group_settings.ai_rss_summary
    - Falls OPENAI_API_KEY fehlt oder lib nicht installiert => None
    """
    key = os.getenv("OPENAI_API_KEY")
    if not key or not text:
        return None
    try:
        # lazy import (damit wir ohne openai laufen können)
        from openai import OpenAI
        client = OpenAI(api_key=key)
        prompt = (
            f"Fasse die folgende News extrem knapp auf {lang} zusammen "
            f"(max. 2 Sätze, keine Floskeln):\n\n{text}"
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":"Du schreibst kurz, sachlich, deutsch."},
                      {"role":"user","content":prompt}],
            temperature=0.2,
            max_tokens=120,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.info(f"OpenAI unavailable: {e}")
        return None

def _extract_domains_from_text(text:str) -> list[str]:
    if not text: return []
    urls = re.findall(r'(https?://\S+|www\.\S+)', text, flags=re.I)
    doms = []
    for u in urls:
        if not u.startswith("http"): u = "http://" + u
        try:
            d = urlparse(u).netloc.lower()
            if d.startswith("www."): d = d[4:]
            if d: doms.append(d)
        except: pass
    return doms

def ai_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))

async def ai_moderate_text(text:str, model:str="omni-moderation-latest") -> dict|None:
    """
    Rückgabe: {'categories': {'toxicity':score,...}, 'flagged': bool}
    Versucht erst Moderation-API, fallback auf Chat-Classifier (gpt-4o-mini).
    """
    key = os.getenv("OPENAI_API_KEY")
    if not key or not text:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        try:
            # Moderations-Endpoint
            res = client.moderations.create(model=model, input=text)
            out = res.results[0]
            scores = {}
            # Map auf unsere Keys
            cats = out.category_scores or {}
            # heuristische Zuordnung (je nach API-Version)
            scores["toxicity"]   = float(cats.get("harassment/threats", 0.0) or cats.get("harassment", 0.0))
            scores["hate"]       = float(cats.get("hate", 0.0) or cats.get("hate/threatening", 0.0))
            scores["sexual"]     = float(cats.get("sexual/minors", 0.0) or cats.get("sexual", 0.0))
            scores["harassment"] = float(cats.get("harassment", 0.0))
            scores["selfharm"]   = float(cats.get("self-harm", 0.0))
            scores["violence"]   = float(cats.get("violence", 0.0) or cats.get("violence/graphic", 0.0))
            return {"categories": scores, "flagged": bool(out.flagged)}
        except Exception:
            # Fallback via Chat-Classifier
            prompt = (
                "Klassifiziere den folgenden Text. Gib JSON zurück mit keys: "
                "toxicity,hate,sexual,harassment,selfharm,violence (Werte 0..1). "
                "Nur das JSON, keine Erklärungen.\n\n" + text[:6000]
            )
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"system","content":"Du antwortest nur mit JSON."},
                          {"role":"user","content":prompt}],
                temperature=0, max_tokens=200
            )
            import json as _json
            data = _json.loads(res.choices[0].message.content)
            return {"categories": {k: float(data.get(k,0)) for k in ["toxicity","hate","sexual","harassment","selfharm","violence"]},
                    "flagged": any(float(data.get(k,0))>=0.8 for k in data)}
    except Exception as e:
        logger.info(f"AI moderation unavailable: {e}")
        return None

def heuristic_link_risk(domains:list[str]) -> float:
    """
    Grobe Risikobewertung für Links ohne AI: Shortener/Suspicious TLDs etc.
    """
    if not domains: return 0.0
    shorteners = {"bit.ly","tinyurl.com","goo.gl","t.co","ow.ly","buff.ly","shorturl.at","is.gd","rb.gy","cutt.ly"}
    bad_tlds   = {".ru",".cn",".tk",".gq",".ml",".ga",".cf"}
    score = 0.0
    for d in domains:
        if d in shorteners: score += 0.4
        if any(d.endswith(t) for t in bad_tlds): score += 0.3
        if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', d): score += 0.5  # blanke IP
    return min(1.0, score)