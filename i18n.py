from typing import Dict
from database import get_group_setting, set_group_language

# Übersetzungs-Dictionary
TRANSLATIONS = {
    'de': {
        'WELCOME_SET':       '✅ Begrüßung gesetzt.',
        'ERROR_NO_TOPIC':    '⚠️ Kein Thema gefunden. Bitte in einem Forum posten.',
        'ERROR_NO_INPUT':    '⚠️ Kein Text angegeben.',
        'TOPIC_SET':         '✅ Thema gesetzt.',
        'CHANNEL_ADDED':     '✅ Kanal hinzugefügt.',
        'CHANNEL_REMOVED':   '✅ Kanal entfernt.',
        'RULES_NONE':        '⚠️ Keine Regeln gesetzt.',
        'RULES_SET':         '✅ Regeln gespeichert.',
        'RSS_URL_PROMPT':    '➡ Bitte sende die RSS-URL.',
        'RSS_TOPIC_SET':     '✅ RSS-Thema gesetzt.',
        'LANG_SELECT_PROMPT':'Bitte wähle deine Sprache:',
        'LANGUAGE_SET':      '✅ Sprache gespeichert: {lang}',
        'LANGUAGE_CURRENT':  'Aktuelle Sprache: {lang}',
        'ERROR_PRIV_CMD':    '⚠️ Nur in Gruppen nutzbar.',
        'ERROR_ADMIN_CMD':   '❌ Nur Admins dürfen das tun.',
        'ERROR_USAGE_LANG':  'Verwendung: /setlanguage de|en|fr|ru',
        'MENU_HEADER':       '🔧 Hauptmenü',
    },
    'en': {
        'WELCOME_SET':       '✅ Welcome message set.',
        'ERROR_NO_TOPIC':    '⚠️ No topic found. Post in a forum.',
        'ERROR_NO_INPUT':    '⚠️ No text provided.',
        'TOPIC_SET':         '✅ Topic set.',
        'CHANNEL_ADDED':     '✅ Channel added.',
        'CHANNEL_REMOVED':   '✅ Channel removed.',
        'RULES_NONE':        '⚠️ No rules defined.',
        'RULES_SET':         '✅ Rules saved.',
        'RSS_URL_PROMPT':    '➡ Please send the RSS URL.',
        'RSS_TOPIC_SET':     '✅ RSS topic set.',
        'LANG_SELECT_PROMPT':'Please select your language:',
        'LANGUAGE_SET':      '✅ Language saved: {lang}',
        'LANGUAGE_CURRENT':  'Current language: {lang}',
        'ERROR_PRIV_CMD':    '⚠️ Only available in groups.',
        'ERROR_ADMIN_CMD':   '❌ Only admins allowed.',
        'ERROR_USAGE_LANG':  'Usage: /setlanguage de|en|fr|ru',
        'MENU_HEADER':       '🔧 Main menu',
    },
    'fr': {
        'WELCOME_SET':       '✅ Message de bienvenue défini.',
        'ERROR_NO_TOPIC':    '⚠️ Aucun sujet trouvé. Postez dans un forum.',
        'ERROR_NO_INPUT':    '⚠️ Aucun texte fourni.',
        'TOPIC_SET':         '✅ Sujet défini.',
        'CHANNEL_ADDED':     '✅ Canal ajouté.',
        'CHANNEL_REMOVED':   '✅ Canal supprimé.',
        'RULES_NONE':        '⚠️ Aucune règle définie.',
        'RULES_SET':         '✅ Règles enregistrées.',
        'RSS_URL_PROMPT':    '➡ Veuillez envoyer l’URL RSS.',
        'RSS_TOPIC_SET':     '✅ Sujet RSS défini.',
        'LANG_SELECT_PROMPT':'Veuillez choisir votre langue :',
        'LANGUAGE_SET':      '✅ Langue enregistrée : {lang}',
        'LANGUAGE_CURRENT':  'Langue actuelle : {lang}',
        'ERROR_PRIV_CMD':    '⚠️ Disponible uniquement en groupe.',
        'ERROR_ADMIN_CMD':   '❌ Seuls les admins.',
        'ERROR_USAGE_LANG':  'Utilisation : /setlanguage de|en|fr|ru',
        'MENU_HEADER':       '🔧 Menu principal',
    },
    'ru': {
        'WELCOME_SET':       '✅ Приветственное сообщение установлено.',
        'ERROR_NO_TOPIC':    '⚠️ Тема не найдена. Публикуйте в форуме.',
        'ERROR_NO_INPUT':    '⚠️ Текст не указан.',
        'TOPIC_SET':         '✅ Тема установлена.',
        'CHANNEL_ADDED':     '✅ Канал добавлен.',
        'CHANNEL_REMOVED':   '✅ Канал удалён.',
        'RULES_NONE':        '⚠️ Правила не заданы.',
        'RULES_SET':         '✅ Правила сохранены.',
        'RSS_URL_PROMPT':    '➡ Пожалуйста, отправьте RSS-URL.',
        'RSS_TOPIC_SET':     '✅ Тема RSS установлена.',
        'LANG_SELECT_PROMPT':'Выберите язык:',
        'LANGUAGE_SET':      '✅ Язык сохранён : {lang}',
        'LANGUAGE_CURRENT':  'Текущий язык : {lang}',
        'ERROR_PRIV_CMD':    '⚠️ Только в группах.',
        'ERROR_ADMIN_CMD':   '❌ Только для админов.',
        'ERROR_USAGE_LANG':  'Использование: /setlanguage de|en|fr|ru',
        'MENU_HEADER':       '🔧 Главное меню',
    },
}

def t(chat_id: int, key: str) -> str:
    """
    Übersetzung anhand der in der Datenbank gespeicherten Sprache des Chats.
    """
    setting = get_group_setting(chat_id)
    lang = setting.language if setting and setting.language in TRANSLATIONS else 'de'
    return TRANSLATIONS[lang].get(key, TRANSLATIONS['de'].get(key, key))


def set_language_callback(update, context):
    from telegram.ext import CallbackContext
    query = update.callback_query
    chat_id = query.message.chat.id
    _, lang = query.data.split('_', 1)
    set_group_language(chat_id, lang)
    query.answer()
    query.edit_message_text(text=t(chat_id, 'LANGUAGE_SET'))