"""Database Initialization Script for Affiliate Bot"""

import os
import logging
from bots.affliate.database import (
    init_all_schemas,
    get_connection
)

logger = logging.getLogger(__name__)


def init_database():
    """Initialize database schema"""
    logger.info("🚀 Initializing Affiliate Bot Database...")
    
    if not init_all_schemas():
        logger.error("❌ Failed to initialize schemas")
        return False
    
    logger.info("✅ Database schemas initialized successfully")
    
    # Add sample data (optional)
    conn = get_connection()
    if conn:
        try:
            cur = conn.cursor()
            
            # Create a sample affiliate commission record
            cur.execute("""
                INSERT INTO aff_commissions (referrer_id, total_earned, pending, tier)
                VALUES (123456789, 2450.00, 1250.00, 'gold')
                ON CONFLICT DO NOTHING
            """)
            
            conn.commit()
            logger.info("✅ Sample data created")
        except Exception as e:
            logger.error(f"❌ Sample data error: {e}")
            conn.rollback()
        finally:
            cur.close()
            conn.close()
    
    return True


def create_indexes():
    """Create database indexes for performance"""
    logger.info("📊 Creating indexes...")
    
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        # Referrals indexes
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_referrals_referrer
            ON aff_referrals(referrer_id)
        """)
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_referrals_status
            ON aff_referrals(status)
        """)
        
        # Conversions indexes
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversions_referrer
            ON aff_conversions(referrer_id)
        """)
        
        # Payouts indexes
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payouts_referrer
            ON aff_payouts(referrer_id)
        """)
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payouts_status
            ON aff_payouts(status)
        """)
        
        conn.commit()
        logger.info("✅ Indexes created successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Index creation error: {e}")
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()


def backup_database():
    """Backup database (recommended before migrations)"""
    logger.info("💾 Creating database backup...")
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        logger.error("❌ DATABASE_URL not set")
        return False
    
    # Extract credentials
    import urllib.parse
    parsed = urllib.parse.urlparse(db_url)
    
    backup_file = f"backup_aff_{int(__import__('time').time())}.sql"
    
    try:
        import subprocess
        cmd = [
            "pg_dump",
            f"--host={parsed.hostname}",
            f"--port={parsed.port or 5432}",
            f"--username={parsed.username}",
            f"--database={parsed.path.lstrip('/')}",
            f"--file={backup_file}"
        ]
        
        result = subprocess.run(cmd, env={"PGPASSWORD": parsed.password or ""})
        
        if result.returncode == 0:
            logger.info(f"✅ Backup created: {backup_file}")
            return True
        else:
            logger.error("❌ Backup failed")
            return False
    except Exception as e:
        logger.error(f"❌ Backup error: {e}")
        return False


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    # Check DATABASE_URL
    if not os.getenv("DATABASE_URL"):
        print("❌ Error: DATABASE_URL environment variable not set")
        sys.exit(1)
    
    # Run initialization
    if not init_database():
        sys.exit(1)
    
    if not create_indexes():
        sys.exit(1)
    
    print()
    print("✅ Affiliate Bot Database Ready!")
    print()
    print("📋 Next Steps:")
    print("1. Set environment variables (see .env.example)")
    print("2. Start the affiliate bot: python -m bots.affliate")
    print("3. Open the dashboard: /dashboard in Telegram")
