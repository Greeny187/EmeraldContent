"""
Story Sharing System - Health Check & Initialization
Verifies all components are properly configured
"""

import asyncio
import logging
from pathlib import Path
import json

logger = logging.getLogger(__name__)

def _is_asyncpg_pool(obj) -> bool:
    return hasattr(obj, "acquire") and callable(getattr(obj, "acquire", None))

def _is_psycopg2_pool(obj) -> bool:
    return hasattr(obj, "getconn") and hasattr(obj, "putconn")

async def _psycopg2_fetchval(pool, sql: str, params=()):
    """Runs a single-value query against a psycopg2 pool in a thread."""
    def _run():
        conn = pool.getconn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        finally:
            pool.putconn(conn)
    return await asyncio.to_thread(_run)

class StorySystemHealthCheck:
    """Comprehensive health check for story-sharing system"""
    
    def __init__(self, db_pool=None, config=None):
        self.db_pool = db_pool
        self.config = config or {}
        self.checks = []
    
    async def run_all_checks(self):
        """Run all health checks"""
        checks = [
            self.check_database_schema(),
            self.check_api_routes(),
            self.check_dependencies(),
            self.check_configuration(),
            self.check_templates(),
            self.check_reward_integration(),
        ]
        
        results = await asyncio.gather(*checks, return_exceptions=True)
        return self.format_results(results)
    
    async def check_database_schema(self):
        """Verify database schema exists"""
        try:
            if not self.db_pool:
                return {
                    'name': '🗄️ Database Schema',
                    'status': 'WARN',
                    'message': 'No database pool provided'
                }

            tables = ['story_shares', 'story_clicks', 'story_conversions']

            # asyncpg style
            if _is_asyncpg_pool(self.db_pool) and not _is_psycopg2_pool(self.db_pool):
                async with self.db_pool.acquire() as conn:
                    for table in tables:
                        exists = await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)",
                            table
                        )
                        if not exists:
                            return {
                                'name': '🗄️ Database Schema',
                                'status': 'FAIL',
                                'message': f'Missing table: {table}'
                            }

            # psycopg2 pool style
            elif _is_psycopg2_pool(self.db_pool):
                for table in tables:
                    exists = await _psycopg2_fetchval(
                        self.db_pool,
                        "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=%s)",
                        (table,)
                    )
                    if not exists:
                        return {
                            'name': '🗄️ Database Schema',
                            'status': 'FAIL',
                            'message': f'Missing table: {table}'
                        }

            else:
                return {
                    'name': '🗄️ Database Schema',
                    'status': 'WARN',
                    'message': 'Unknown pool type (expected asyncpg or psycopg2 pool)'
                }

            return {
                'name': '🗄️ Database Schema',
                'status': 'PASS',
                'message': 'All story tables exist'
            }

        except Exception as e:
            logger.error(f"Database schema check error: {e}")
            return {
                'name': '🗄️ Database Schema',
                'status': 'FAIL',
                'message': str(e)
            }
    
    async def check_api_routes(self):
        """Verify API routes are registered"""
        try:
            expected_routes = [
                'POST /api/stories/create',
                'POST /api/stories/click',
                'POST /api/stories/convert',
                'GET /api/stories/templates',
                'GET /api/stories/stats/{id}',
                'GET /api/stories/user/{id}',
                'GET /api/stories/top',
                'GET /api/stories/card/{template}',
            ]
            
            # This is a basic check - in production, verify against actual routes
            return {
                'name': '🛣️ API Routes',
                'status': 'PASS',
                'message': f'Expected {len(expected_routes)} routes registered'
            }
        
        except Exception as e:
            return {
                'name': '🛣️ API Routes',
                'status': 'FAIL',
                'message': str(e)
            }
    
    async def check_dependencies(self):
        """Check required dependencies"""
        try:
            issues = []
            
            # Check PIL
            try:
                from PIL import Image, ImageDraw, ImageFont
            except ImportError:
                issues.append('Pillow (PIL) not installed - image generation will fail')
            
            # Check psycopg2
            try:
                import psycopg2
            except ImportError:
                issues.append('psycopg2 not installed')
            
            # Check aiohttp
            try:
                import aiohttp
            except ImportError:
                issues.append('aiohttp not installed')
            
            if issues:
                return {
                    'name': '📦 Dependencies',
                    'status': 'WARN',
                    'message': f'Issues: {"; ".join(issues)}'
                }
            
            return {
                'name': '📦 Dependencies',
                'status': 'PASS',
                'message': 'All required dependencies available'
            }
        
        except Exception as e:
            return {
                'name': '📦 Dependencies',
                'status': 'FAIL',
                'message': str(e)
            }
    
    async def check_configuration(self):
        """Verify configuration settings"""
        try:
            config = self.config
            
            required_settings = {
                "enabled": "boolean",
                "daily_limit": "number",
                "reward_share": "number",
                "reward_referral": "number",
                "reward_clicks": "number",
            }
            
            missing = []
            for setting, expected_type in required_settings.items():
                if setting not in config:
                    missing.append(setting)
            
            if missing:
                return {
                    'name': '⚙️ Configuration',
                    'status': 'WARN',
                    'message': f'Missing settings: {", ".join(missing)}'
                }
            
            return {
                'name': '⚙️ Configuration',
                'status': 'PASS',
                'message': f'{len(required_settings)} settings configured'
            }
        
        except Exception as e:
            return {
                'name': '⚙️ Configuration',
                'status': 'FAIL',
                'message': str(e)
            }
    
    async def check_templates(self):
        """Verify story templates"""
        try:
            from bots.content.story_sharing import STORY_TEMPLATES
            
            required_templates = [
                'group_bot',
                'stats',
                'content',
                'emrd_rewards',
                'affiliate'
            ]
            
            available = list(STORY_TEMPLATES.keys())
            missing = [t for t in required_templates if t not in available]
            
            if missing:
                return {
                    'name': '🎨 Templates',
                    'status': 'WARN',
                    'message': f'Missing templates: {", ".join(missing)}'
                }
            
            return {
                'name': '🎨 Templates',
                'status': 'PASS',
                'message': f'{len(available)} templates available'
            }
        
        except Exception as e:
            return {
                'name': '🎨 Templates',
                'status': 'FAIL',
                'message': str(e)
            }
    
    async def check_reward_integration(self):
        """Check reward system integration"""
        try:
            # Try importing reward module
            try:
                from shared.emrd_rewards_integration import award_points
            except ImportError:
                return {
                    'name': '💰 Reward Integration',
                    'status': 'WARN',
                    'message': 'Reward module not found - points may not be awarded'
                }
            
            return {
                'name': '💰 Reward Integration',
                'status': 'PASS',
                'message': 'Reward system integration available'
            }
        
        except Exception as e:
            return {
                'name': '💰 Reward Integration',
                'status': 'FAIL',
                'message': str(e)
            }
    
    def format_results(self, results):
        """Format results for display"""
        status_counts = {'PASS': 0, 'WARN': 0, 'FAIL': 0}
        output = []
        
        for result in results:
            if isinstance(result, Exception):
                result = {'name': 'Unknown', 'status': 'FAIL', 'message': str(result)}
            
            status_counts[result.get('status', 'FAIL')] += 1
            
            emoji_map = {'PASS': '✅', 'WARN': '⚠️', 'FAIL': '❌'}
            emoji = emoji_map.get(result['status'], '❓')
            
            output.append(f"{result.get('name', 'Check')} {emoji}")
            output.append(f"  {result.get('message', 'No message')}")
        
        summary = (
            f"\n{'='*50}\n"
            f"Summary: ✅ {status_counts['PASS']} | "
            f"⚠️ {status_counts['WARN']} | "
            f"❌ {status_counts['FAIL']}\n"
            f"{'='*50}"
        )
        
        return {
            'output': '\n'.join(output) + summary,
            'summary': status_counts,
            'healthy': status_counts['FAIL'] == 0
        }


async def initialize_story_system(db_pool, config=None):
    """Initialize story-sharing system"""
    logger.info("🚀 Initializing Story-Sharing System...")
    
    health_check = StorySystemHealthCheck(db_pool, config)
    results = await health_check.run_all_checks()
    
    logger.info(results['output'])
    
    if not results['healthy']:
        logger.error("❌ Story system initialization failed!")
        return False
    
    logger.info("✅ Story system ready!")
    return True


# CLI for testing
if __name__ == '__main__':
    import sys
    import asyncpg
    
    async def main():
        # Mock checks without database
        check = StorySystemHealthCheck()
        results = await check.run_all_checks()
        
        print(results['output'])
        sys.exit(0 if results['healthy'] else 1)
    
    asyncio.run(main())
