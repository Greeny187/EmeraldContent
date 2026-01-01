"""
Telegram WebApp Authentication for DAO Mini App
Verifies WebApp initData and manages secure user sessions
"""

import logging
import hashlib
import hmac
from typing import Optional, Dict
from datetime import datetime
import json
import os

logger = logging.getLogger(__name__)

class TelegramWebAppAuth:
    """Verify Telegram WebApp initData authenticity"""
    
    def __init__(self, bot_token: str):
        """
        Initialize with bot token for verification
        
        Args:
            bot_token: Telegram Bot API Token
        """
        self.bot_token = bot_token
    
    @staticmethod
    def parse_init_data(init_data_raw: str) -> Dict:
        """
        Parse and validate Telegram WebApp initData
        
        Args:
            init_data_raw: Raw initData string from Telegram
            
        Returns:
            Parsed and validated data dict
        """
        try:
            params = {}
            for pair in init_data_raw.split('&'):
                key, value = pair.split('=', 1)
                params[key] = value
            return params
        except Exception as e:
            logger.error(f"Parse init_data error: {e}")
            return {}
    
    def verify_init_data(self, init_data_raw: str) -> Optional[Dict]:
        """
        Verify authenticity of WebApp initData
        
        Implementation based on Telegram documentation:
        https://core.telegram.org/bots/webapps#validating-data-received-from-the-web-app
        
        Args:
            init_data_raw: Raw initData string from client
            
        Returns:
            Parsed user data if valid, None otherwise
        """
        try:
            params = self.parse_init_data(init_data_raw)
            
            if not params:
                logger.warning("Empty init_data")
                return None
            
            # Extract hash
            hash_value = params.get('hash')
            if not hash_value:
                logger.warning("No hash in init_data")
                return None
            
            # Create verification string
            data_check_string = '\n'.join(
                f"{k}={v}" for k, v in sorted(params.items())
                if k != 'hash'
            )
            
            # Calculate HMAC
            secret_key = hashlib.sha256(
                self.bot_token.encode()
            ).digest()
            
            calculated_hash = hmac.new(
                secret_key,
                data_check_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            # Verify hash matches
            if calculated_hash != hash_value:
                logger.warning(f"Hash mismatch: {calculated_hash} != {hash_value}")
                return None
            
            # Check auth_date not too old (5 minutes)
            auth_date = int(params.get('auth_date', 0))
            current_time = int(datetime.now().timestamp())
            
            if current_time - auth_date > 300:
                logger.warning(f"Auth data too old: {current_time - auth_date}s")
                return None
            
            # Parse user data
            user_data = {}
            if params.get('user'):
                try:
                    user_json = json.loads(
                        params['user'].replace('%20', ' ')
                    )
                    user_data = {
                        'id': user_json.get('id'),
                        'is_bot': user_json.get('is_bot', False),
                        'first_name': user_json.get('first_name'),
                        'last_name': user_json.get('last_name'),
                        'username': user_json.get('username'),
                        'language_code': user_json.get('language_code'),
                        'is_premium': user_json.get('is_premium', False),
                    }
                except json.JSONDecodeError:
                    logger.error("Failed to parse user JSON")
                    return None
            
            return {
                'user': user_data,
                'auth_date': auth_date,
                'chat_instance': params.get('chat_instance'),
                'chat_type': params.get('chat_type', 'private'),
                'valid': True
            }
            
        except Exception as e:
            logger.error(f"Init data verification error: {e}")
            return None


class WebAppSessionManager:
    """Manage secure sessions for WebApp users"""
    
    def __init__(self):
        """Initialize session storage (in production use Redis)"""
        self.sessions = {}  # In production: use Redis
    
    def create_session(self, user_id: int, app_data: Dict) -> str:
        """
        Create new session for user
        
        Args:
            user_id: Telegram user ID
            app_data: Validated WebApp init data
            
        Returns:
            Session token
        """
        import secrets
        session_token = secrets.token_urlsafe(32)
        
        self.sessions[session_token] = {
            'user_id': user_id,
            'created_at': datetime.now().isoformat(),
            'app_data': app_data
        }
        
        logger.info(f"✅ Session created for user {user_id}")
        return session_token
    
    def validate_session(self, session_token: str) -> Optional[Dict]:
        """
        Validate session token
        
        Args:
            session_token: Session token to validate
            
        Returns:
            Session data if valid, None otherwise
        """
        session = self.sessions.get(session_token)
        
        if not session:
            logger.warning(f"Invalid session token: {session_token}")
            return None
        
        # Check session not too old (24 hours)
        created = datetime.fromisoformat(session['created_at'])
        if (datetime.now() - created).seconds > 86400:
            del self.sessions[session_token]
            logger.warning(f"Session expired: {session_token}")
            return None
        
        return session
    
    def invalidate_session(self, session_token: str) -> bool:
        """
        Invalidate/logout session
        
        Args:
            session_token: Session token to invalidate
            
        Returns:
            True if session was removed
        """
        if session_token in self.sessions:
            del self.sessions[session_token]
            logger.info(f"Session invalidated: {session_token}")
            return True
        return False


# Global instances
def get_auth(bot_token: str) -> TelegramWebAppAuth:
    """Get TelegramWebAppAuth instance"""
    return TelegramWebAppAuth(bot_token)


def get_session_manager() -> WebAppSessionManager:
    """Get WebAppSessionManager instance"""
    return WebAppSessionManager()


# Integration with aiohttp handlers
async def handle_webapp_auth(request):
    """
    WebApp Authentication Endpoint
    
    POST /api/auth/telegram
    Body: { initData: string }
    """
    try:
        data = await request.json()
        init_data = data.get('initData')
        
        if not init_data:
            return web.json_response(
                {'success': False, 'error': 'Missing initData'},
                status=400
            )
        
        # Get bot token
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            return web.json_response(
                {'success': False, 'error': 'Bot not configured'},
                status=500
            )
        
        # Verify init data
        auth = get_auth(bot_token)
        verified_data = auth.verify_init_data(init_data)
        
        if not verified_data:
            return web.json_response(
                {'success': False, 'error': 'Invalid initData'},
                status=401
            )
        
        # Create session
        user_id = verified_data['user'].get('id')
        session_mgr = get_session_manager()
        session_token = session_mgr.create_session(user_id, verified_data)
        
        return web.json_response({
            'success': True,
            'session_token': session_token,
            'user': verified_data['user']
        })
        
    except Exception as e:
        logger.error(f"WebApp auth error: {e}")
        return web.json_response(
            {'success': False, 'error': str(e)},
            status=500
        )


async def require_webapp_auth(request):
    """
    Middleware to require valid WebApp authentication
    
    Usage:
        @routes.post('/api/dao/protected')
        async def protected_endpoint(request):
            user_id = request.scope['user_id']
            ...
    """
    session_token = request.headers.get('X-Session-Token')
    
    if not session_token:
        return web.json_response(
            {'success': False, 'error': 'Session required'},
            status=401
        )
    
    session_mgr = get_session_manager()
    session = session_mgr.validate_session(session_token)
    
    if not session:
        return web.json_response(
            {'success': False, 'error': 'Invalid session'},
            status=401
        )
    
    # Add user info to request scope
    request.scope['user_id'] = session['user_id']
    request.scope['user_data'] = session['app_data']['user']
    
    return None
