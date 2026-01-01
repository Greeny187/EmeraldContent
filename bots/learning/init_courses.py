"""Learning Bot - Initialization Script for Courses and AI Content"""

import asyncio
import logging
import sys
sys.path.insert(0, '/path/to/Emerald_Bots')

from bots.learning import database, ai_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Course Definitions
COURSES = [
    {
        "title": "Blockchain Basics",
        "description": "Lerne die Grundlagen der Blockchain-Technologie, Dezentralisierung und wie Kryptographie funktioniert.",
        "level": "beginner",
        "duration_minutes": 150,
        "category": "Blockchain",
        "icon": "🔗",
        "reward_points": 100,
        "modules": [
            {"title": "Was ist Blockchain?", "content": "Die Grundlagen der Blockchain-Technologie", "duration": 30},
            {"title": "Dezentralisierung", "content": "Wie dezentrale Systeme funktionieren", "duration": 35},
            {"title": "Kryptographie", "content": "Sicherheit durch Verschlüsselung", "duration": 40},
            {"title": "Konsens-Mechanismen", "content": "PoW, PoS und andere Mechanismen", "duration": 30},
            {"title": "Praktische Anwendungen", "content": "Blockchain in der realen Welt", "duration": 25}
        ]
    },
    {
        "title": "Smart Contracts 101",
        "description": "Verstehe Smart Contracts, Solidity, Sicherheit und Best Practices bei der Entwicklung.",
        "level": "intermediate",
        "duration_minutes": 195,
        "category": "Smart Contracts",
        "icon": "🪙",
        "reward_points": 150,
        "modules": [
            {"title": "Smart Contract Architektur", "content": "Aufbau und Design von Smart Contracts", "duration": 35},
            {"title": "Solidity vs TACT", "content": "Vergleich verschiedener Programmiersprachen", "duration": 40},
            {"title": "Sicherheit", "content": "Häufige Sicherheitsprobleme und Lösungen", "duration": 45},
            {"title": "Gas-Optimierung", "content": "Kosteneffizienz in Smart Contracts", "duration": 35},
            {"title": "Deployment", "content": "Bereitstellung und Verifikation", "duration": 40}
        ]
    },
    {
        "title": "DeFi Protokolle",
        "description": "Erkunde dezentralisierte Finanzprotokolle, AMMs, Lending und Yield Farming.",
        "level": "intermediate",
        "duration_minutes": 240,
        "category": "DeFi",
        "icon": "🏦",
        "reward_points": 200,
        "modules": [
            {"title": "DeFi Ökosystem", "content": "Überblick über das DeFi-Ökosystem", "duration": 40},
            {"title": "Automated Market Makers", "content": "Wie AMMs funktionieren", "duration": 50},
            {"title": "Lending & Borrowing", "content": "Kreditprotokolle in DeFi", "duration": 45},
            {"title": "Yield Farming", "content": "Renditen in DeFi erzielen", "duration": 50},
            {"title": "Liquidität & Slippage", "content": "Liquiditätsmanagement verstehen", "duration": 55}
        ]
    },
    {
        "title": "Trading Strategien",
        "description": "Lerne technische und fundamentale Analyse, Risikomanagement und Portfolio-Management.",
        "level": "advanced",
        "duration_minutes": 180,
        "category": "Trading",
        "icon": "📈",
        "reward_points": 250,
        "modules": [
            {"title": "Technische Analyse", "content": "Charts, Patterns und Indikatoren", "duration": 40},
            {"title": "Fundamentalanalyse", "content": "Bewertung von Projekten und Tokens", "duration": 40},
            {"title": "Risikomanagement", "content": "Schutz vor Verlusten", "duration": 35},
            {"title": "Portfolio-Strategie", "content": "Diversifikation und Asset Allocation", "duration": 35},
            {"title": "Psychologie", "content": "Emotionale Aspekte beim Trading", "duration": 30}
        ]
    },
    {
        "title": "Token Economics",
        "description": "Verstehe Token-Design, Verteilung, Incentives und langfristige Nachhaltigkeit.",
        "level": "advanced",
        "duration_minutes": 150,
        "category": "Economics",
        "icon": "💱",
        "reward_points": 200,
        "modules": [
            {"title": "Token Design", "content": "Grundprinzipien des Token-Designs", "duration": 30},
            {"title": "Verteilungsmechanismen", "content": "Faire und effektive Verteilung", "duration": 35},
            {"title": "Incentive-Strukturen", "content": "Anreize für Teilnehmer", "duration": 30},
            {"title": "Governance", "content": "Dezentrale Entscheidungsfindung", "duration": 30},
            {"title": "Sustainability", "content": "Langfristige Wertschöpfung", "duration": 25}
        ]
    }
]

async def init_courses():
    """Initialize all courses with modules"""
    logger.info("Starting course initialization...")
    
    # Initialize database
    database.init_all_schemas()
    
    for course_data in COURSES:
        logger.info(f"Creating course: {course_data['title']}")
        
        # Create course
        course_id = database.add_course(
            title=course_data['title'],
            description=course_data['description'],
            level=course_data['level'],
            duration=course_data['duration_minutes'],
            category=course_data['category'],
            icon=course_data['icon'],
            reward_points=course_data['reward_points']
        )
        
        if not course_id:
            logger.error(f"Failed to create course: {course_data['title']}")
            continue
        
        logger.info(f"Course created with ID: {course_id}")
        
        # Create modules
        for idx, module_data in enumerate(course_data['modules'], 1):
            logger.info(f"Creating module: {module_data['title']}")
            
            module_id = database.add_module(
                course_id=course_id,
                title=module_data['title'],
                content=module_data['content'],
                order_index=idx,
                duration=module_data['duration']
            )
            
            if not module_id:
                logger.error(f"Failed to create module: {module_data['title']}")
                continue
            
            # Generate AI quizzes for this module
            logger.info(f"Generating AI quizzes for module: {module_data['title']}")
            await generate_module_quizzes(module_id, module_data['title'])
    
    logger.info("✅ Course initialization completed!")


async def generate_module_quizzes(module_id: int, module_title: str):
    """Generate AI quizzes for a module"""
    try:
        # Generate 3-4 questions per module
        num_questions = 3
        
        for i in range(num_questions):
            logger.info(f"Generating question {i+1}/{num_questions} for {module_title}")
            
            question_data = await ai_content.generate_quiz_question(
                topic=module_title,
                difficulty="medium"
            )
            
            if question_data:
                quiz_id = database.add_quiz(
                    module_id=module_id,
                    topic=module_title,
                    question=question_data['question'],
                    options=question_data['options'],
                    correct_answer=question_data['correct_answer'],
                    explanation=question_data['explanation'],
                    difficulty="medium",
                    points=10
                )
                
                if quiz_id:
                    logger.info(f"✅ Quiz created with ID: {quiz_id}")
                else:
                    logger.error(f"Failed to create quiz for module {module_id}")
            else:
                logger.error(f"Failed to generate question for {module_title}")
            
            # Add small delay to avoid API rate limits
            await asyncio.sleep(1)
        
        logger.info(f"✅ Quizzes generated for module: {module_title}")
    except Exception as e:
        logger.error(f"Error generating quizzes: {e}")


async def main():
    """Main initialization function"""
    logger.info("=" * 50)
    logger.info("EMERALD ACADEMY - COURSE INITIALIZATION")
    logger.info("=" * 50)
    
    try:
        await init_courses()
        logger.info("=" * 50)
        logger.info("✅ INITIALIZATION SUCCESSFUL!")
        logger.info("=" * 50)
    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        logger.exception(e)


if __name__ == "__main__":
    asyncio.run(main())
