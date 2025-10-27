"""
Crossposter Bot-Paket.

ENV-Konvention:
  BOT4_TOKEN  -> Crossposter-Bot-Token (wird von bot.py gelesen)
  BOT4_KEY    -> "crossposter" (empfohlen), ergibt Route /webhook/crossposter

Dieses Paket stellt zwei Module bereit:
  - app.py      -> registriert die PTB-Handler (MessageHandler, /crossposter)
  - miniapp.py  -> bindet die MiniApp-API unter /miniapi an den AIOHTTP-Server
"""
__all__ = ["app", "miniapp"]
