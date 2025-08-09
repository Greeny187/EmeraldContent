import os
import logging
from datetime import date, time
from zoneinfo import ZoneInfo
from telegram.ext import ContextTypes
from telethon_client import telethon_client, start_telethon
from telethon.tl.functions.channels import GetFullChannelRequest, GetForumTopicsRequest
from database import _db_pool, get_registered_groups, is_daily_stats_enabled, purge_deleted_members, get_group_stats
from statistic import (
    DEVELOPER_IDS, get_all_group_ids, get_group_meta, fetch_message_stats,
    compute_response_times, fetch_media_and_poll_stats, get_member_stats,
    get_message_insights, get_engagement_metrics, get_trend_analysis,
    update_group_activity_score
)
from telegram.constants import ParseMode
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
CHANNEL_USERNAMES = [u.strip() for u in os.getenv("STATS_CHANNELS", "").split(",") if u.strip()]
TIMEZONE = os.getenv("TZ", "Europe/Berlin")

async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    bot = context.bot
    for chat_id, _ in get_registered_groups():
        if not is_daily_stats_enabled(chat_id):
            continue
        try:
            top3 = get_group_stats(chat_id, today)
            if not top3:
                continue
            
            # Create lines with proper HTML formatting
            lines = []
            for i, (uid, cnt) in enumerate(top3):
                try:
                    # Try to get user info
                    user = await bot.get_chat_member(chat_id, uid)
                    name = user.user.first_name
                    mention = f"<a href='tg://user?id={uid}'>{name}</a>"
                except Exception:
                    # Fallback if user info can't be retrieved
                    mention = f"User {uid}"
                
                lines.append(f"{i+1}. {mention}: {cnt} Nachrichten")
            
            # Format message with proper HTML
            text = (
                f"📊 <b>Tagesstatistik {today.isoformat()}</b>\n"
                f"📝 Top {len(lines)} aktive Mitglieder:\n" + "\n".join(lines)
            )
            
            # Only send if we have data
            if lines:
                await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Fehler beim Senden der Tagesstatistik an {chat_id}: {e}")

async def telethon_stats_job(context: ContextTypes.DEFAULT_TYPE):
    if not telethon_client.is_connected():
        await start_telethon()
    # statt statischer Liste: alle registrierten Gruppen abfragen
    for chat_id, _ in get_registered_groups():
        try:
            # über chat_id das Peer-Entity holen
            entity = await telethon_client.get_entity(chat_id)
            # Voll-Info abrufen (funktioniert für Gruppen und Channels)
            full = await telethon_client(GetFullChannelRequest(entity))
            chat = full.chats[0]
            # id bleibt chat_id
            admins = getattr(full.full_chat, "admins_count", 0) or 0
            members = getattr(full.full_chat, "participants_count", 0) or 0

            # Robust topic count
            topics = 0
            try:
                res = await telethon_client(GetForumTopicsRequest(
                    channel=entity, offset_date=None, offset_id=0, offset_topic=0, limit=1
                ))
                topics = getattr(res, "count", 0) or 0
            except Exception:
                forum_info = getattr(full.full_chat, "forum_info", None)
                if forum_info and hasattr(forum_info, "topics"):
                    topics = len(forum_info.topics or [])
                elif forum_info and hasattr(forum_info, "total_count"):
                    topics = forum_info.total_count or 0
                else:
                    topics = 0

            bots = len(getattr(full.full_chat, "bot_info", []) or [])
            description = getattr(full.full_chat, "about", "") or ""
            title = getattr(chat, "title", "–")

            logger.info(f"[telethon_stats_job] Gruppe {chat_id}: topics={topics}")

            conn = _db_pool.getconn()
            conn.autocommit = True
            with conn.cursor() as cur:
                # 1) group_settings updaten (inkl. last_active)
                cur.execute(
                    """
                    UPDATE group_settings
                       SET title = %s,
                           description = %s,
                           member_count  = %s,
                           admin_count   = %s,
                           topic_count   = %s,
                           bot_count     = %s,
                           last_active   = NOW()
                     WHERE chat_id = %s;
                    """,
                    (title, description, members, admins, topics, bots, chat_id)
                )
            _db_pool.putconn(conn)
        except Exception as e:
            logger.error(f"Fehler beim Abfragen von {chat_id}: {e}")

async def purge_members_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        purge_deleted_members()
        logger.info("Purge von gelöschten Mitgliedern abgeschlossen.")
    except Exception as e:
        logger.error(f"Fehler beim Purgen von Mitgliedern: {e}")

async def dev_stats_nightly_job(context: ContextTypes.DEFAULT_TYPE):
    """Sendet das Dev-Dashboard täglich automatisch an alle Developer."""
    end   = datetime.utcnow()
    start = end - timedelta(days=7)
    group_ids = get_all_group_ids()
    if not group_ids:
        return

    output = []
    for chat_id in group_ids:
        meta = await get_group_meta(chat_id)
        telethon_text = ""
        try:
            msg_stats   = await fetch_message_stats(chat_id, 7)
            resp_times  = await compute_response_times(chat_id, 7)
            media_stats = await fetch_media_and_poll_stats(chat_id, 7)
            avg = resp_times.get('average_response_s')
            med = resp_times.get('median_response_s')
            avg_str = f"{avg:.1f}s" if avg is not None else "Keine Daten"
            med_str = f"{med:.1f}s" if med is not None else "Keine Daten"
            telethon_text = (
                f"📡 *Live-Statistiken (Telethon, letzte 7 Tage)*\n"
                f"• Nachrichten gesamt: {msg_stats['total']}\n"
                f"• Top 3 Absender: " + ", ".join(str(u) for u,_ in msg_stats['by_user'].most_common(3)) + "\n"
                f"• Reaktionszeit Ø/Med: {avg_str} / {med_str}\n"
                f"• Medien: " + ", ".join(f"{k}={v}" for k,v in media_stats.items()) + "\n"
            )
        except Exception as e:
            logger.warning(f"Telethon-Stats für {chat_id} fehlgeschlagen: {e}")
            telethon_text = "📡 *Live-Statistiken (Telethon)*: _nicht verfügbar_\n"

        members  = get_member_stats(chat_id, start)
        insights = get_message_insights(chat_id, start, end)
        engage   = get_engagement_metrics(chat_id, start, end)
        trends   = get_trend_analysis(chat_id, periods=4)

        messages_last_week = insights['total']
        new_members = members['new']
        replies = engage['reply_rate_pct'] * messages_last_week / 100
        score = (messages_last_week * 0.5) + (new_members * 2) + (replies * 1)
        update_group_activity_score(chat_id, score)

        db_text = (
            "💾 *Datenbank-Statistiken (letzte 7 Tage)*\n"
            f"🔖 Topics: {meta['topics']}  🤖 Bots: {meta['bots']}\n"
            f"👥 Neue Member: {members['new']}  👋 Left: {members['left']}  💤 Inaktiv: {members['inactive']}\n"
            f"💬 Nachrichten gesamt: {insights['total']}\n"
            f"   • Fotos: {insights['photo']}  Videos: {insights['video']}  Sticker: {insights['sticker']}\n"
            f"   • Voice: {insights['voice']}  Location: {insights['location']}  Polls: {insights['polls']}\n"
            f"⏱️ Antwort-Rate: {engage['reply_rate_pct']} %  Ø-Delay: {engage['avg_delay_s']} s\n"
            "📈 Trend (Woche → Nachrichten):\n"
        )
        for week_start, count in trends.items():
            db_text += f"   – {week_start}: {count}\n"

        text = (
            f"*Gruppe:* {meta['title']} (`{chat_id}`)\n"
            f"📝 Beschreibung: {meta['description']}\n"
            f"👥 Mitglieder: {meta['members']}  👮 Admins: {meta['admins']}\n"
            f"📂 Topics: {meta['topics']}\n\n"
            f"{telethon_text}\n"
            f"{db_text}"
        )
        output.append(text)

    bot = context.bot
    for dev_id in DEVELOPER_IDS:
        for chunk in output:
            try:
                await bot.send_message(dev_id, chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"Dev-Statistik an {dev_id} fehlgeschlagen: {e}")

def register_jobs(app):
    jq = app.job_queue
    jq.run_daily(
        daily_report,
        time(hour=8, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="daily_report"
    )
    jq.run_daily(
        telethon_stats_job,
        time(hour=2, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="telethon_stats"
    )
    jq.run_daily(
        purge_members_job,
        time(hour=3, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="purge_members"
    )
    jq.run_daily(
        dev_stats_nightly_job,
        time(hour=4, minute=0, tzinfo=ZoneInfo(TIMEZONE)),
        name="dev_stats_nightly"
    )
    logger.info("Jobs registriert: daily_report, telethon_stats, purge_members, dev_stats_nightly")