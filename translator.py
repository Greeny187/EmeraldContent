import os
import logging
import requests
from database import get_cached_translation, set_cached_translation

logger = logging.getLogger(__name__)

# LibreTranslate-Endpunkt und API-Key
LIBRE_URL     = os.getenv('LIBRE_URL', 'https://libretranslate.com')
LIBRE_API_KEY = os.getenv('LIBRE_API_KEY', None)

def translate_hybrid(text: str, target_lang: str, source_lang: str = 'auto') -> str:
    """
    Übersetzt 'text' ins Zielsprachformat 'target_lang' mit:
    1) Cache-Abfrage
    2) API-Call zu LibreTranslate
    3) Zwischenspeichern im Cache

    Fällt die API aus, wird der Originaltext zurückgegeben.
    """
    # 1) Cache abfragen
    cached = get_cached_translation(text, target_lang)
    if cached:
        return cached

    # 2) Anfrage an LibreTranslate
    payload = {
        'q': text,
        'source': source_lang,
        'target': target_lang
    }
    if LIBRE_API_KEY:
        payload['api_key'] = LIBRE_API_KEY

    try:
        resp = requests.post(f"{LIBRE_URL}/translate", data=payload, timeout=10)
        resp.raise_for_status()
        translated = resp.json().get('translatedText', text)
    except Exception as e:
        logger.warning(f"Übersetzung fehlgeschlagen, fallback auf Originaltext: {e}")
        translated = text

    # 3) In Cache speichern
    try:
        set_cached_translation(text, target_lang, translated)
    except Exception as e:
        logger.error(f"Konnte Übersetzung nicht cachen: {e}")

    return translated