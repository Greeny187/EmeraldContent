import os
import logging
import datetime as dt
from datetime import date, time, datetime, timedelta
from zoneinfo import ZoneInfo
from telegram.ext import ContextTypes, Application
from telethon_client import telethon_client, start_telethon
from telethon.tl.functions.channels import GetFullChannelRequest, GetForumTopicsRequest
from database import (_db_pool, get_registered_groups, is_daily_stats_enabled, prune_pending_inputs_older_than, 
                    purge_deleted_members, get_group_stats, get_night_mode) # <-- HIER HINZUGEFÜGT
from statistic import (
    DEVELOPER_IDS, get_all_group_ids, get_group_meta, fetch_message_stats,
    compute_response_times, fetch_media_and_poll_stats, get_member_stats, 
    get_message_insights, get_engagement_metrics, get_trend_analysis, update_group_activity_score, 
    migrate_stats_rollup, compute_agg_group_day, upsert_agg_group_day, get_group_language
)
from telegram.constants import ParseMode
from translator import translate_hybrid as tr

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
            lang = get_group_language(chat_id) or 'de'
            top3 = get_group_stats(chat_id, today) or []
            
            if top3:
                lines = []
                for i, (uid, cnt) in enumerate(top3):
                    try:
                        user = await bot.get_chat_member(chat_id, uid)
                        name = user.user.first_name
                        mention = f"<a href='tg://user?id={uid}'>{name}</a>"
                    except:
                        mention = f"User {uid}"
                    lines.append(f"{i+1}. {mention}: {cnt} Nachrichten")
                
                text = (
                    f"📊 <b>Tagesstatistik {today.isoformat()}</b>\n\n"
                    f"📝 Top {len(lines)} aktive Mitglieder:\n" + "\n".join(lines)
                )
            else:
                # Auch bei 0 Aktivität senden
                text = (
                    f"📊 <b>Tagesstatistik {today.isoformat()}</b>\n\n"
                    f"💤 Keine Aktivität in der Gruppe."
                )
            
            await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            
        except Exception as e:
            logger.error(f"Tagesstatistik-Fehler für {chat_id}: {e}")

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
    low_activity = []  # für Alerts, wenn Scores mehrere Tage niedrig sind
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

        mflow    = get_member_stats(chat_id, start)
        insights = get_message_insights(chat_id, start, end)
        engage   = get_engagement_metrics(chat_id, start, end)
        trends   = get_trend_analysis(chat_id, periods=4)

        messages_last_week = insights['total']
        new_members = mflow['new']
        replies = engage['reply_rate_pct'] * messages_last_week / 100
        # --- Score: Normalisierung & Decay ---
        base_score = (messages_last_week * 0.5) + (new_members * 2) + (replies * 1)
        mem_count = meta.get('members') or 1
        norm = max(mem_count, 1) / 1000.0
        normalized = base_score / norm
        # Decay: 90% Altwert + 10% Heute
        # Holt bisherigen Score aus group_settings (get_group_meta liefert ihn mit)
        prev = meta.get('activity_score') or meta.get('group_activity_score') or 0
        score = prev * 0.9 + normalized * 0.1
        update_group_activity_score(chat_id, score)
        if score < 25:  # Schwelle anpassbar
            low_activity.append((chat_id, meta.get('title'), round(score, 1)))

        db_text = (
            "💾 *Datenbank-Statistiken (letzte 7 Tage)*\n"
            f"🔖 Topics: {meta['topics']}  🤖 Bots: {meta['bots']}\n"
            f"👥 Neue Member: {mflow['new']}  👋 Left: {mflow['left']}  💤 Inaktiv: {mflow['inactive']}\n"
            f"💬 Nachrichten gesamt: {insights['total']}\n"
            f"   • Fotos: {insights['photo']}  Videos: {insights['video']}  Sticker: {insights['sticker']}\n"
            f"   • Voice: {insights['voice']}  Location: {insights['location']}  Polls: {insights['polls']}\n"
            f"🔢 Aktivitäts-Score (norm.+Decay): {score:.1f}\n"
            f"⏱️ Antwort-Rate: {engage['reply_rate_pct']} %  Ø-Delay: {engage['avg_delay_s']} s\n"
            "📈 Trend (Woche → Nachrichten):\n"
        )
        for week_start, count in trends.items():
            db_text += f"   – {week_start}: {count}\n"

        text = (
            f"*Gruppe:* {meta['title']} (`{chat_id}`)\n"
            f"📝 Beschreibung: {meta['description']}\n"
            f"👥 Mitglieder: {mem_count}  👮 Admins: {meta['admins']}\n"
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

    # --- Alert bei niedriger Aktivität (optional) ---
    if low_activity:
        low_activity.sort(key=lambda x: x[2])  # nach Score asc
        lines = [f"• {title} (`{cid}`): {sc}" for cid, title, sc in low_activity[:10]]
        note = "⚠️ Niedrige Aktivität erkannt (Score < 25):\n" + "\n".join(lines)
        for dev_id in DEVELOPER_IDS:
            try:
                await bot.send_message(dev_id, note)
            except Exception:
                pass

async def rollup_yesterday(context):
    migrate_stats_rollup()
    tz = ZoneInfo("Europe/Berlin")
    today = datetime.now(tz).date()
    target_day = today - timedelta(days=1)

    try:
        chat_ids = [cid for (cid, _) in get_registered_groups()]  # ← FIX
    except Exception:
        chat_ids = []

    for cid in chat_ids:
        try:
            payload = compute_agg_group_day(cid, target_day)
            upsert_agg_group_day(cid, target_day, payload)
        except Exception as e:
            print(f"[rollup] Fehler bei chat {cid}: {e}")
            
# Rate-Limiting: Warte kurz zwischen Nachrichten in verschiedenen Chats
import asyncio

async def night_mode_job(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    now_utc = datetime.now(dt.timezone.utc)

    for chat_id, _ in get_registered_groups():
        enabled, start_minute, end_minute, del_non, _, tz_str, hard, override = get_night_mode(chat_id)
        if not enabled:
            continue

        group_tz = ZoneInfo(tz_str or TIMEZONE)
        now_local = now_utc.astimezone(group_tz)
        
        start_time = dt.time(hour=start_minute // 60, minute=start_minute % 60)
        end_time = dt.time(hour=end_minute // 60, minute=end_minute % 60)
        now_time = now_local.time()

        is_active = False
        if start_time < end_time:
            is_active = start_time <= now_time < end_time
        else:
            is_active = now_time >= start_time or now_time < end_time

        # KORREKTUR: start_dt und end_dt definieren
        current_date = now_local.date()
        start_dt = datetime.combine(current_date, start_time, tzinfo=group_tz)
        end_dt = datetime.combine(current_date, end_time, tzinfo=group_tz)

        # Korrektur für Zeiträume über Mitternacht
        if start_time > end_time:
            if now_local.time() < end_time:
                # Wir sind nach Mitternacht, Start war am Vortag
                start_dt -= timedelta(days=1)
            else:
                # Wir sind vor Mitternacht, Ende ist am nächsten Tag
                end_dt += timedelta(days=1)

        # Prüfen, ob der Status sich gerade geändert hat
        # KORREKTUR: Direkter Zugriff auf das chat_data-Dictionary für die jeweilige ID.
        nm_key = f"nm_status_{chat_id}"  # Eindeutiger Schlüssel pro Chat
        last_status = context.bot_data.get(nm_key, is_active)

        if is_active and not last_status:
            # Nachtmodus wurde gerade AKTIVIERT => "bis hh:mm (TZ)"
            lang = get_group_language(chat_id) or 'de'
            # Ende-Zeitpunkt berechnen:
            end_local = dt.datetime.combine(now_local.date(), end_time, tzinfo=group_tz)
            if start_time > end_time and now_local.time() >= start_time:
                # über Mitternacht -> Ende am nächsten Tag
                end_local += dt.timedelta(days=1)
            human = end_local.strftime("%H:%M")
            await bot.send_message(chat_id, tr("🌙 Nachtmodus aktiv bis", lang) + f" {human} ({group_tz.key}).")
            context.bot_data[nm_key] = True
            
        elif not is_active and last_status:
            # Nachtmodus wurde gerade DEAKTIVIERT
            lang = get_group_language(chat_id) or 'de'
            await bot.send_message(chat_id, tr("☀️ Der Nachtmodus ist beendet. Alle können wieder schreiben.", lang))
            # KORREKTUR: In bot_data speichern statt chat_data
            context.bot_data[nm_key] = False

        # Nachrichten im Nachtmodus löschen, wenn aktiviert
        if is_active and del_non:
            # Diese Schleife ist konzeptionell fehlerhaft in einem Job,
            # da sie keine neuen Nachrichten abfängt.
            # Sie wird hier auskommentiert, um Fehler zu vermeiden.
            # try:
            #     # Annahme: Sie wollen Nachrichten von Nicht-Admins löschen.
            #     # Dies erfordert einen MessageHandler, keinen Job.
            #     pass
            # except Exception as e:
            #     logger.error(f"Fehler beim Löschen von Nachrichten in {chat_id}: {e}")
            pass # Platzhalter, um den Block syntaktisch korrekt zu halten

        try:
            # Pro Chat in einem eigenen Try-Block, damit ein Fehler
            # nicht alle anderen Chats blockiert
            # ...bestehender Code zur Statusprüfung...
            
            # Nach jedem Chat kurz warten, um API-Limits zu vermeiden
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Fehler im Night-Mode für Chat {chat_id}: {e}")
            continue

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
    
    tz = ZoneInfo("Europe/Berlin")
    first = dt.datetime.now(tz).replace(hour=2, minute=15, second=0, microsecond=0)
    if first < dt.datetime.now(tz):
        first += dt.timedelta(days=1)
    app.job_queue.run_repeating(rollup_yesterday, interval=dt.timedelta(days=1), first=first, name="rollup_yesterday")
    
    # NEU: night_mode_job registrieren, damit er jede Minute läuft
    jq.run_repeating(night_mode_job, interval=60, first=10, name="night_mode_job")
    # Pending-Inputs aufräumen (alle 24h)
    from database import prune_pending_inputs_older_than
    async def _prune(_):
        try:
            prune_pending_inputs_older_than(48)
        except Exception as e:
            logger.warning(f"pending_inputs prune failed: {e}")
    jq.run_repeating(_prune, interval=86400, first=300, name="pending_inputs_prune")
    logger.info("Jobs registriert: daily_report, telethon_stats, purge_members, dev_stats_nightly, rollup_yesterday, night_mode_job")