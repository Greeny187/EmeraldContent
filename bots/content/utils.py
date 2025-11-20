import re
import asyncio
import logging
from telegram.error import BadRequest, Forbidden, RetryAfter
from telegram.ext import ExtBot
from telegram import ChatMember, ChatPermissions
from .database import list_members, remove_member

logger = logging.getLogger(__name__)

# Heuristik: "Deleted Account" in verschiedenen Sprachen/Varianten
_DELETED_NAME_RX = re.compile(
    r"(deleted\s+account|gelÃ¶sch(tes|ter)\s+(konto|account)|"
    r"(Ð°ÐºÐºÐ°ÑƒÐ½Ñ‚\s+ÑƒÐ´Ð°Ð»Ñ‘Ð½|ÑƒÐ´Ð°Ð»Ñ‘Ð½Ð½Ñ‹Ð¹\s+Ð°ÐºÐºÐ°ÑƒÐ½Ñ‚)|"
    r"(Ø­Ø³Ø§Ø¨\s+Ù…Ø­Ø°ÙˆÙ)|"
    r"(compte\s+supprimÃ©)|"
    r"(cuenta\s+eliminada)|"
    r"(konto\s+gelÃ¶scht|konto\s+usuniÄ™te)|"
    r"(account\s+cancellato)|"
    r"(å·²åˆ é™¤çš„å¸æˆ·|å·²åˆªé™¤çš„å¸³è™Ÿ)|"
    r"(cont\s+È™ters)|"
    r"(ÑÑ‡ÐµÑ‚\s+ÑƒÐ´Ð°Ð»ÐµÐ½))",
    re.IGNORECASE
)

async def _apply_hard_permissions(context, chat_id: int, active: bool):
    """
    Setzt für den Chat harte Schreibsperren (Nachtmodus) an/aus.
    active=True  -> can_send_messages=False
    active=False -> can_send_messages=True
    """
    try:
        if active:
            await context.bot.set_chat_permissions(
                chat_id=chat_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        else:
            await context.bot.set_chat_permissions(
                chat_id=chat_id,
                permissions=ChatPermissions(can_send_messages=True)
            )
    except Exception as e:
        logger.warning(f"Nachtmodus (hard) set_chat_permissions fehlgeschlagen: {e}")

def is_deleted_account(member) -> bool:
    """
    Erkenne gelöschte Accounts anhand der Bot-API-Daten:
    - Telegram ersetzt first_name durch 'Deleted Account'
    - oder entfernt alle Namen/Username bei gelöschten Accounts
    """
    user = member.user
    first = (user.first_name or "").lower()
    # 1) Default-Titel 'Deleted Account' (manchmal variiert: 'Deleted account')
    if first.startswith("deleted account"):
        return True
    # 2) Kein Name, kein Username mehr vorhanden - auch ein Indiz für gelöschten Account
    if not any([user.first_name, user.last_name, user.username]):
        return True
    return False

async def _get_member(bot: ExtBot, chat_id: int, user_id: int) -> ChatMember | None:
    try:
        return await bot.get_chat_member(chat_id, user_id)
    except RetryAfter as e:
        await asyncio.sleep(getattr(e, "retry_after", 1))
        return await bot.get_chat_member(chat_id, user_id)
    except BadRequest as e:
        # z. B. "Chat member not found" â†’ nicht (mehr) Mitglied
        logger.debug(f"get_chat_member({chat_id},{user_id}) -> {e}")
        return None

async def clean_delete_accounts_for_chat(chat_id: int, bot: ExtBot, *,
                                         dry_run: bool = False,
                                         demote_admins: bool = False) -> int:
    """
    Entfernt geloeschte Accounts per ban+unban.
    - Entfernt DB-Eintrag NUR, wenn Kick erfolgreich war ODER der User nicht (mehr) im Chat ist.
    - Optional: demote_admins=True versucht geloeschte Admins zu demoten (erfordert Bot-Recht 'can_promote_members').
    Logging: detailliert alle Operationen und Fehler für Debugging.
    """
    logger.info(f"[clean_delete] Starting cleanup task for chat {chat_id} (dry_run={dry_run}, demote_admins={demote_admins})")
    
    # Permission check at start
    try:
        bot_member = await bot.get_chat_member(chat_id, bot.id)
        logger.debug(f"[clean_delete] Bot permissions: can_restrict={bot_member.can_restrict_members}, can_promote={bot_member.can_promote_members}")
        
        if not bot_member.can_restrict_members or not bot_member.can_promote_members:
            logger.warning(f"[clean_delete] ABORT in {chat_id}: insufficient permissions "
                         f"(can_restrict={bot_member.can_restrict_members}, can_promote={bot_member.can_promote_members})")
            return 0
    except Exception as e:
        logger.error(f"[clean_delete] ABORT in {chat_id}: failed to get permissions: {type(e).__name__}: {e}")
        return 0
    
    user_ids = [uid for uid in list_members(chat_id) if uid and uid > 0]
    logger.info(f"[clean_delete] Scanning {len(user_ids)} members in {chat_id} for deleted accounts...")
    removed = 0

    for uid in user_ids:
        member = await _get_member(bot, chat_id, uid)
        if member is None:
            # Nicht (mehr) im Chat -> DB aufrÃ¤umen
            logger.debug(f"[clean_delete] User {uid} not in chat anymore, cleaning DB...")
            try: 
                remove_member(chat_id, uid)
                logger.debug(f"[clean_delete] Removed {uid} from DB")
            except Exception as e: 
                logger.debug(f"[clean_delete] Failed to remove {uid} from DB: {e}")
            continue

        # Gelöschten Status erkennen
        looks_del = is_deleted_account(member)

        # Admin/Owner-Handhabung
        if member.status in ("administrator", "creator"):
            if not looks_del or not demote_admins or member.status == "creator":
                # Creator (Owner) nie anfassen; Admins nur wenn demote_admins=True
                if looks_del:
                    logger.debug(f"[clean_delete] User {uid} is deleted {member.status} but demote_admins={demote_admins}, skipping")
                continue
            # Demote versuchen (alle Rechte false)
            logger.info(f"[clean_delete] Demoting deleted {member.status} {uid}...")
            try:
                await bot.promote_chat_member(
                    chat_id, uid,
                    can_manage_chat=False, can_post_messages=False, can_edit_messages=False,
                    can_delete_messages=False, can_manage_video_chats=False, can_invite_users=False,
                    can_restrict_members=False, can_pin_messages=False, can_promote_members=False,
                    is_anonymous=False
                )
                # Status neu laden
                member = await _get_member(bot, chat_id, uid)
                logger.info(f"[clean_delete] Successfully demoted {uid}")
            except Exception as e:
                logger.warning(f"[clean_delete] Demote failed for {uid}: {type(e).__name__}: {e}")
                continue  # ohne Demote kein Kick mÃ¶glich

        if not looks_del:
            logger.debug(f"[clean_delete] User {uid} does not look deleted, skipping")
            continue

        if dry_run:
            logger.info(f"[clean_delete] DRY_RUN: Would remove deleted account {uid}")
            removed += 1
            continue

        kicked = False
        logger.info(f"[clean_delete] Banning deleted account {uid}...")
        try:
            await bot.ban_chat_member(chat_id, uid)
            logger.debug(f"[clean_delete] Banned {uid}, now unbanning...")
            try:
                await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
                logger.debug(f"[clean_delete] Unbanned {uid}")
            except BadRequest as ube:
                logger.debug(f"[clean_delete] Unban failed (might be ok): {ube}")
            kicked = True
            removed += 1
            logger.info(f"[clean_delete] Successfully removed deleted account {uid} ({removed} total so far)")
        except Forbidden as e:
            logger.warning(f"[clean_delete] Permission denied removing {uid}: {e}")
        except BadRequest as e:
            logger.warning(f"[clean_delete] Bad request removing {uid}: {e}")

        # DB nur dann aufrÃ¤umen, wenn wirklich drauÃŸen
        try:
            member_after = await _get_member(bot, chat_id, uid)
            if kicked or member_after is None or getattr(member_after, "status", "") in ("left", "kicked"):
                logger.debug(f"[clean_delete] Cleaning DB for {uid} (kicked={kicked}, status after={getattr(member_after, 'status', 'N/A')})")
                remove_member(chat_id, uid)
        except Exception as e:
            logger.debug(f"[clean_delete] Failed to clean DB for {uid}: {e}")

    logger.info(f"[clean_delete] Cleanup complete for {chat_id}: removed {removed} deleted accounts (dry_run={dry_run})")
    return removed

