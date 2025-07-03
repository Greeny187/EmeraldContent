# Modul zur Übersetzungsverwaltung (DE/EN)
from typing import Dict
from database import get_group_setting

# Übersetzungs-Dictionary
TRANSLATIONS: Dict[str, Dict[str, str]] = {
    'de': {
        'WELCOME_SET': '✅ Begrüßung gesetzt.',
        'ERROR_NO_TOPIC': '⚠️ Kein Thema gefunden. Bitte stelle sicher, dass du in einem Forum postest.',
        'CHANNEL_ADDED': '✅ Kanal hinzugefügt.',
        'CHANNEL_REMOVED': '✅ Kanal entfernt.',
        # hier weitere Keys...
    },
    'en': {
        'WELCOME_SET': '✅ Welcome message set.',
        'ERROR_NO_TOPIC': '⚠️ No topic found. Please make sure you are posting in a forum.',
        'CHANNEL_ADDED': '✅ Channel added.',
        'CHANNEL_REMOVED': '✅ Channel removed.',
        # add more keys as needed...
    }
}


def t(chat_id: int, key: str) -> str:
    """
    Übersetzung anhand der in der Datenbank gespeicherten Sprache des Chats.
    :param chat_id: ID der Gruppe oder des Kanals
    :param key: Schlüssel für den gewünschten Text
    :return: Übersetzter Text
    """
    # Gruppeneinstellungen auslesen
    setting = get_group_setting(chat_id)
    lang = setting.language if setting and setting.language in TRANSLATIONS else 'de'
    return TRANSLATIONS[lang].get(key, TRANSLATIONS['de'].get(key, key))
