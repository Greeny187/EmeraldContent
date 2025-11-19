# payments.py - Zentrale Zahlungsintegration für alle Bots
import os, uuid, time, hmac, hashlib, logging
import asyncio
import httpx
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

try:
    from bots.content.database import (
        ensure_payments_schema, create_payment_order, mark_payment_paid,
        set_pro_until, get_subscription_info
    )
except ImportError:
    # Fallback wenn database nicht verfügbar (z.B. in anderen Bots)
    def ensure_payments_schema(*args, **kwargs): pass
    def create_payment_order(*args, **kwargs): pass
    def mark_payment_paid(*args, **kwargs): return False, None, None
    def set_pro_until(*args, **kwargs): pass
    def get_subscription_info(*args, **kwargs): return {}

WEBSITE  = "https://greeny187.github.io/GreenyManagementBots/"
SUPPORT  = "https://t.me/+DkUfIvjyej8zNGVi"
TON_WALLET = os.getenv("TON_WALLET_ADDRESS", "UQBopac1WFJGC_K48T8T8...")

# Coinbase Commerce API Konfiguration
COINBASE_API_KEY = os.getenv("COINBASE_COMMERCE_API_KEY", "")
COINBASE_API_URL = "https://api.commerce.coinbase.com/charges"
COINBASE_WEBHOOK_SECRET = os.getenv("COINBASE_WEBHOOK_SECRET", "")

# WalletConnect Konfiguration
WALLETCONNECT_PROJECT_ID = os.getenv("WALLETCONNECT_PROJECT_ID", "")
WALLETCONNECT_ICON = "https://emeraldcontent.com/icon.png"  # Bot Icon URL

PROVIDERS = {
    "paypal":     {"label":"PayPal", "link_base": os.getenv("PAYPAL_LINK_BASE")},
    "coinbase":   {"label":"Coinbase Commerce", "api_key": COINBASE_API_KEY, "webhook_secret": COINBASE_WEBHOOK_SECRET},
    "walletconnect_ton": {"label":"TON Wallet (WalletConnect)", "wallet": TON_WALLET},
    "walletconnect_near": {"label":"NEAR Wallet (WalletConnect)", "contract": os.getenv("NEAR_CONTRACT", "emeraldcontent.near")},
    "binance":    {"label":"Binance Pay", "link_base": os.getenv("BINANCE_LINK_BASE")},
    "bybit":      {"label":"Bybit Pay", "link_base": os.getenv("BYBIT_LINK_BASE")},
    "revolut":    {"label":"Revolut Pay", "link_base": os.getenv("REVOLUT_LINK_BASE")},
    "stars":      {"label":"Telegram Stars", "link_base": os.getenv("STARS_DEEPLINK")},
}

PLANS = {
    "pro_monthly": {"label":"Pro (1 Monat)", "months":1, "price_eur": os.getenv("PRO_PRICE_EUR","4.99"), "price_usd": "5.99"},
    "pro_yearly":  {"label":"Pro (12 Monate)", "months":12, "price_eur": os.getenv("PRO_YEAR_EUR","49.00"), "price_usd": "59.99"},
}

def _build_link(link_base:str, order_id:str, price:str)->str:
    """Generic payment link builder."""
    sep = "&" if "?" in (link_base or "") else "?"
    return f"{link_base}{sep}ref={order_id}&amount={price}"

async def create_coinbase_charge(order_id: str, price_eur: str, description: str, webhook_url: str) -> dict:
    """
    Erstellt eine Coinbase Commerce Charge für Zahlungen.
    Returns: {"charge_id": "...", "hosted_url": "...", "error": None} oder {"error": "..."}
    """
    if not COINBASE_API_KEY:
        logger.warning("[coinbase] API key not configured")
        return {"error": "Coinbase not configured"}
    
    headers = {
        "X-CC-Api-Key": COINBASE_API_KEY,
        "X-CC-Version": "2018-03-22",
        "Content-Type": "application/json"
    }
    
    # Konvertiere EUR zu USD (vereinfachte Annahme: 1 EUR ≈ 1.1 USD)
    price_usd = str(float(price_eur) * 1.1)[:5]
    
    payload = {
        "name": "Emerald PRO Subscription",
        "description": description,
        "pricing_type": "fixed_price",
        "local_price": {
            "amount": price_usd,
            "currency": "USD"
        },
        "metadata": {
            "order_id": order_id,
            "service": "emerald_pro"
        },
        "redirect_url": webhook_url,
        "cancel_url": f"{webhook_url}?cancelled=1"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(COINBASE_API_URL, json=payload, headers=headers, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            
            charge = data.get("data", {})
            logger.info(f"[coinbase] Created charge {order_id}: {charge.get('id')}")
            
            return {
                "charge_id": charge.get("id"),
                "hosted_url": charge.get("hosted_url"),
                "error": None
            }
    except Exception as e:
        logger.error(f"[coinbase] Failed to create charge: {e}")
        return {"error": str(e)}

def build_walletconnect_uri(provider: str, amount_eur: str, order_id: str) -> str:
    """
    Erstelle WalletConnect URI für TON/NEAR Wallets.
    Format für WalletConnect Deep Link:
    - TON: tonclient://transfer?address=<addr>&amount=<nano>&text=<text>
    - Allgemein: wc://...<project_id>
    """
    if provider == "walletconnect_ton":
        # TON nanograms (1 TON = 1e9 nano)
        ton_amount = str(int(float(amount_eur) * 4))  # Vereinfachte Konvertierung
        return f"ton://transfer?address={TON_WALLET}&amount={ton_amount}&text=Order%20{order_id}"
    
    elif provider == "walletconnect_near":
        near_amount = amount_eur  # In NEAR (approx. 1 EUR ≈ 0.25 NEAR, aber anpassen)
        return f"near://call?contract={PROVIDERS['walletconnect_near']['contract']}&method=claim_pro&args=" \
               f"{{'amount':'{near_amount}','order_id':'{order_id}'}}"
    
    return ""

def create_checkout(chat_id: int, provider: str, plan_key: str, user_id: int, webhook_url: str = "") -> dict:
    """
    Erstelle einen Checkout für verschiedene Payment Provider.
    webhook_url: für Coinbase Callback
    """
    ensure_payments_schema()
    plan = PLANS.get(plan_key, {})
    if not plan:
        return {"error": f"Unknown plan: {plan_key}"}
    
    order_id = str(uuid.uuid4())
    price_eur = str(plan.get("price_eur", "4.99"))
    months = int(plan.get("months", 1))
    
    # Speichere Order
    try:
        create_payment_order(order_id, chat_id, provider, plan_key, price_eur, months, user_id)
    except Exception as e:
        logger.warning(f"[checkout] Failed to create payment order: {e}")
    
    # Provider-spezifische Links
    if provider == "coinbase":
        # Hinweis: Coinbase ist async, sollte separat aufgerufen werden
        return {
            "provider": "coinbase",
            "order_id": order_id,
            "price": price_eur,
            "months": months,
            "action": "create_charge",  # Signal für Caller: async create_coinbase_charge aufrufen
            "webhook_url": webhook_url or WEBSITE
        }
    
    elif provider in ("walletconnect_ton", "walletconnect_near"):
        uri = build_walletconnect_uri(provider, price_eur, order_id)
        return {
            "provider": provider,
            "order_id": order_id,
            "price": price_eur,
            "months": months,
            "uri": uri,  # Deep Link für Wallet
            "label": PROVIDERS[provider]["label"]
        }
    
    elif provider == "paypal":
        pb = PROVIDERS.get(provider, {})
        link_base = pb.get("link_base")
        if link_base:
            link = _build_link(link_base, order_id, price_eur)
        else:
            link = WEBSITE
        return {
            "provider": provider,
            "url": link,
            "order_id": order_id,
            "price": price_eur,
            "months": months
        }
    
    elif provider == "stars":
        # Telegram Stars - Deep Link
        return {
            "provider": "stars",
            "order_id": order_id,
            "price": price_eur,
            "months": months,
            "action": "telegram_stars"  # App sollte Telegram Stars API aufrufen
        }
    
    else:
        # Fallback für andere Provider
        pb = PROVIDERS.get(provider, {})
        link_base = pb.get("link_base")
        if link_base:
            link = _build_link(link_base, order_id, price_eur)
        else:
            link = WEBSITE
        return {
            "provider": provider,
            "url": link,
            "order_id": order_id,
            "price": price_eur,
            "months": months
        }

def build_pro_menu(chat_id: int) -> InlineKeyboardMarkup:
    """Erstelle PRO Membership Menü."""
    sub = get_subscription_info(chat_id) if callable(get_subscription_info) else {}
    kb = []
    
    for key, plan in PLANS.items():
        row = []
        for prov_key, meta in PROVIDERS.items():
            # Skip provider ohne konfiguration
            if prov_key in ("coinbase", "walletconnect_ton", "walletconnect_near"):
                # Diese brauchen spezielle Behandlung
                if prov_key == "coinbase" and not COINBASE_API_KEY:
                    continue
                row.append(InlineKeyboardButton(f"{plan['label']} • {meta['label']}", 
                                              callback_data=f"pay:{prov_key}:{key}"))
            elif prov_key in ("stars", "paypal"):
                # Direct links
                row.append(InlineKeyboardButton(f"{plan['label']} • {meta['label']}", 
                                              callback_data=f"pay:{prov_key}:{key}"))
            elif meta.get("link_base"):
                row.append(InlineKeyboardButton(f"{plan['label']} • {meta['label']}", 
                                              callback_data=f"pay:{prov_key}:{key}"))
        if row:
            kb.extend([row])
    
    kb.append([InlineKeyboardButton("🔎 Status prüfen", callback_data="pay:status")])
    return InlineKeyboardMarkup(kb)

def verify_coinbase_webhook(request_body: str, signature: str) -> bool:
    """
    Verify Coinbase Commerce webhook signature.
    signature: from X-CC-Webhook-Signature header
    """
    if not COINBASE_WEBHOOK_SECRET:
        logger.warning("[coinbase] Webhook secret not configured")
        return False
    
    expected_sig = hmac.new(
        COINBASE_WEBHOOK_SECRET.encode(),
        request_body.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_sig, signature)

def handle_webhook(provider: str, data: dict) -> bool:
    """
    Handle payment provider webhooks.
    Returns True if payment was successfully marked.
    """
    logger.info(f"[webhook] Processing {provider} webhook: {data}")
    
    # Extrahiere Order ID (unterschiedliche Namensgebung je Provider)
    order_id = data.get("order_id") or data.get("ref") or data.get("metadata", {}).get("order_id")
    if not order_id:
        logger.warning(f"[webhook] No order_id found in {provider} webhook")
        return False
    
    # Unterschiedliche Status-Namen
    status = data.get("status", "").lower()
    
    # Coinbase nutzt "COMPLETED"
    if provider == "coinbase" and status != "completed":
        logger.info(f"[webhook] Coinbase charge not completed: {status}")
        return False
    
    # Markiere als bezahlt
    ok, chat_id, months = mark_payment_paid(order_id, provider)
    if ok:
        until = datetime.now(ZoneInfo("UTC")) + timedelta(days=30*months)
        set_pro_until(chat_id, until, tier="pro")
        logger.info(f"[webhook] Payment {order_id} marked successful for chat {chat_id}, {months} months")
    else:
        logger.warning(f"[webhook] Failed to mark payment {order_id} as paid")
    
    return ok
