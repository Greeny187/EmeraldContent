"""Learning Bot - Database"""

import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

def get_db_connection():
    try:
        return psycopg2.connect(os.getenv("DATABASE_URL"))
    except Exception as e:
        logger.error(f"DB error: {e}")
        return None


def init_all_schemas():
    """Initialize Learning database schemas"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # Courses
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_courses (
                id SERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                description TEXT,
                level VARCHAR(50),
                duration_minutes INTEGER,
                category VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Course Modules
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_modules (
                id SERIAL PRIMARY KEY,
                course_id INTEGER REFERENCES learning_courses(id) ON DELETE CASCADE,
                title VARCHAR(255),
                order_index INTEGER,
                content TEXT,
                video_url VARCHAR(500),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # User Enrollments
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_enrollments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                course_id INTEGER REFERENCES learning_courses(id),
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """)
        
        # Module Progress
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_progress (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                module_id INTEGER REFERENCES learning_modules(id),
                completed_at TIMESTAMP
            )
        """)
        
        # Quizzes
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_quizzes (
                id SERIAL PRIMARY KEY,
                module_id INTEGER REFERENCES learning_modules(id),
                question TEXT,
                options JSONB,
                correct_answer VARCHAR(500),
                points INTEGER DEFAULT 10
            )
        """)
        
        # Quiz Results
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_quiz_results (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                quiz_id INTEGER REFERENCES learning_quizzes(id),
                answer TEXT,
                is_correct BOOLEAN,
                points_earned INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Certificates
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_certificates (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                course_id INTEGER REFERENCES learning_courses(id),
                issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                certificate_hash VARCHAR(255) UNIQUE
            )
        """)
        
        # Rewards & Progress
        cur.execute("""
            CREATE TABLE IF NOT EXISTS learning_rewards (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                course_id INTEGER REFERENCES learning_courses(id),
                points_earned INTEGER,
                emrd_earned NUMERIC(18,8),
                claimed_at TIMESTAMP
            )
        """)
        
        conn.commit()
        logger.info("Learning schemas initialized")
    except Exception as e:
        logger.error(f"Schema error: {e}")
        conn.rollback()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def enroll_course(user_id: int, course_id: int) -> bool:
    """Enroll user in course"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO learning_enrollments (user_id, course_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, course_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error enrolling: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_user_courses(user_id: int) -> list:
    """Get user's enrolled courses"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT c.* FROM learning_courses c
            JOIN learning_enrollments e ON c.id = e.course_id
            WHERE e.user_id = %s
        """, (user_id,))
        return cur.fetchall()
    except Exception as e:
        logger.error(f"Error fetching courses: {e}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def mark_module_complete(user_id: int, module_id: int) -> bool:
    """Mark module as completed"""
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO learning_progress (user_id, module_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, module_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error marking complete: {e}")
        return False
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def issue_certificate(user_id: int, course_id: int) -> Optional[str]:
    """Issue course certificate"""
    import hashlib
    
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cert_hash = hashlib.sha256(f"{user_id}_{course_id}_{int(__import__('time').time())}".encode()).hexdigest()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO learning_certificates (user_id, course_id, certificate_hash) VALUES (%s, %s, %s)",
            (user_id, course_id, cert_hash)
        )
        conn.commit()
        return cert_hash
    except Exception as e:
        logger.error(f"Error issuing certificate: {e}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
