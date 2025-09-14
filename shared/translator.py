import os
import logging
import openai
from .database import get_cached_translation, set_cached_translation

logger = logging.getLogger(__name__)

# OpenAI API-Key setzen
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY ist nicht gesetzt. Bitte in den Umgebungsvariablen hinterlegen.")
openai.api_key = OPENAI_API_KEY

# Festes Modell für Übersetzung
TRANSLATION_MODEL = "gpt-3.5-turbo"

def translate_hybrid(text: str, target_lang: str, source_lang: str = 'auto') -> str:
    """
    Übersetzt 'text' ins Zielsprachformat 'target_lang' mit:
    1) Cache-Abfrage
    2) OpenAI API-Call (neues Interface openai.chat.completions.create)
    3) Nur echte Übersetzungen cachen

    Fällt die API aus oder liefert keinen neuen Text, wird der Originaltext zurückgegeben.
    """
    # 1) Cache abfragen
    cached = get_cached_translation(text, target_lang)
    if cached:
        return cached

    # 2) Anfrage an OpenAI mit neuer Schnittstelle
    try:
        prompt = f"Bitte übersetze den folgenden Text von {source_lang} nach {target_lang}: \"{text}\""
        response = openai.chat.completions.create(
            model=TRANSLATION_MODEL,
            messages=[
                {"role": "system", "content": "Du bist ein hilfreicher Übersetzungsassistent."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        translated = response.choices[0].message.content.strip()
        # Falls die API den String in Anführungszeichen zurückgibt, abschneiden:
        translated = translated.strip().strip('"').strip("'")
    except Exception as e:
        logger.warning(f"OpenAI-Übersetzung fehlgeschlagen, Fallback auf Originaltext: {e}")
        translated = text

    # 3) Nur echte Übersetzungen ins Cache schreiben
    if translated and translated != text:
        try:
            set_cached_translation(text, target_lang, translated)
        except Exception as e:
            logger.error(f"Konnte Übersetzung nicht cachen: {e}")

    return translated
