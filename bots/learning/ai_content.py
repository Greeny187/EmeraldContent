"""Learning Bot - AI Content Generator"""

import logging
import os
import json
from typing import List, Dict, Optional
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

COURSE_DEFINITIONS = {
    "blockchain_basics": {
        "title": "Blockchain Basics",
        "icon": "🔗",
        "level": "beginner",
        "category": "Blockchain",
        "duration": 150,
        "topics": [
            "Was ist Blockchain?",
            "Dezentralisierung erklärt",
            "Kryptographie Grundlagen",
            "Konsens-Mechanismen",
            "Smart Contracts Einführung"
        ]
    },
    "smart_contracts_101": {
        "title": "Smart Contracts 101",
        "icon": "🪙",
        "level": "intermediate",
        "category": "Smart Contracts",
        "duration": 195,
        "topics": [
            "Smart Contract Architektur",
            "Solidity vs TACT",
            "Sicherheit in Smart Contracts",
            "Gas-Optimierung",
            "Deployment und Verifikation"
        ]
    },
    "defi_protocols": {
        "title": "DeFi Protokolle",
        "icon": "🏦",
        "level": "intermediate",
        "category": "DeFi",
        "duration": 240,
        "topics": [
            "DeFi Ökosystem",
            "Automatisierte Market Maker (AMM)",
            "Lending & Borrowing",
            "Yield Farming",
            "Liquidität und Slippage"
        ]
    },
    "trading_strategies": {
        "title": "Trading Strategien",
        "icon": "📈",
        "level": "advanced",
        "category": "Trading",
        "duration": 180,
        "topics": [
            "Technische Analyse",
            "Fundamentalanalyse",
            "Risikomanagement",
            "Portfolio-Diversifikation",
            "Psych ologie beim Trading"
        ]
    },
    "token_economics": {
        "title": "Token Economics",
        "icon": "💱",
        "level": "advanced",
        "category": "Economics",
        "duration": 150,
        "topics": [
            "Token Design",
            "Verteilungsmechanismen",
            "Incentive-Strukturen",
            "Governance Modelle",
            "Sustainability & Long-term Value"
        ]
    }
}


async def generate_quiz_question(topic: str, difficulty: str = "medium", context: str = "") -> Optional[Dict]:
    """Generate AI quiz question for a topic"""
    try:
        prompt = f"""
Du bist ein Blockchain & Krypto-Experte und erstelle hochwertige Quiz-Fragen für ein Lernplattform.

Topic: {topic}
Difficulty: {difficulty}
Context: {context}

Erstelle eine Multiple-Choice Frage mit:
- 4 Antwortoptionen (A, B, C, D)
- Einer korrekten Antwort
- Einer kurzen Erklärung

Antworte nur mit gültigem JSON (kein Markdown):
{{
    "question": "Die Frage",
    "options": {{"A": "Option 1", "B": "Option 2", "C": "Option 3", "D": "Option 4"}},
    "correct_answer": "B",
    "explanation": "Kurze Erklärung warum B korrekt ist"
}}
"""
        
        response = await client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=500
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON
        try:
            data = json.loads(content)
            return {
                "question": data.get("question", ""),
                "options": list(data.get("options", {}).values()),
                "correct_answer": data.get("correct_answer", "A"),
                "explanation": data.get("explanation", "")
            }
        except json.JSONDecodeError:
            # Try to extract JSON from response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(content[start:end])
                return {
                    "question": data.get("question", ""),
                    "options": list(data.get("options", {}).values()),
                    "correct_answer": data.get("correct_answer", "A"),
                    "explanation": data.get("explanation", "")
                }
            return None
    except Exception as e:
        logger.error(f"Error generating quiz: {e}")
        return None


async def generate_module_content(topic: str, module_index: int = 1) -> Optional[Dict]:
    """Generate module content and learning material"""
    try:
        prompt = f"""
Du bist ein Blockchain & Krypto-Pädagoge und erstellst Lernmaterial für Anfänger.

Topic: {topic}
Module Index: {module_index}

Erstelle Lernmaterial für dieses Modul mit:
- Einem aussagekräftigen Titel
- 2-3 Absätzen Erklärung (einfach, verständlich)
- 3 Schlüsselkonzepte zum Merken
- Einen praktischen Anwendungsfall

Antworte nur mit gültigem JSON:
{{
    "title": "Modultitel",
    "description": "Kurzbeschreibung",
    "content": "Vollständiger Erklärungstext (2-3 Absätze)",
    "key_concepts": ["Konzept 1", "Konzept 2", "Konzept 3"],
    "practical_example": "Ein reales Anwendungsbeispiel"
}}
"""
        
        response = await client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON
        try:
            data = json.loads(content)
            return data
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(content[start:end])
            return None
    except Exception as e:
        logger.error(f"Error generating content: {e}")
        return None


async def generate_course_overview(course_key: str) -> Optional[Dict]:
    """Generate comprehensive course overview"""
    try:
        course = COURSE_DEFINITIONS.get(course_key)
        if not course:
            return None
        
        prompt = f"""
Du bist ein Blockchain-Kurs-Designer und erstellst ansprechende Kursbeschreibungen.

Course: {course['title']}
Level: {course['level']}
Topics: {', '.join(course['topics'])}

Erstelle eine detaillierte Kursbeschreibung mit:
- Was Teilnehmer lernen (5 Punkte)
- Zielgruppe
- Voraussetzungen
- Lernziele
- Praktische Skills

Antworte nur mit gültigem JSON:
{{
    "learning_outcomes": ["Punkt 1", "Punkt 2", "Punkt 3", "Punkt 4", "Punkt 5"],
    "target_audience": "Zielgruppe Beschreibung",
    "prerequisites": ["Voraussetzung 1", "Voraussetzung 2"],
    "goals": ["Ziel 1", "Ziel 2", "Ziel 3"],
    "skills_gained": ["Skill 1", "Skill 2", "Skill 3"]
}}
"""
        
        response = await client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1000
        )
        
        content = response.choices[0].message.content.strip()
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(content[start:end])
            return None
    except Exception as e:
        logger.error(f"Error generating overview: {e}")
        return None


async def initialize_course_quizzes(course_key: str, from_db=None) -> bool:
    """Initialize all quizzes for a course"""
    try:
        course = COURSE_DEFINITIONS.get(course_key)
        if not course:
            return False
        
        if not from_db:
            return False
        
        # Get course ID
        courses = from_db.get_all_courses()
        course_data = next((c for c in courses if c.get('title') == course['title']), None)
        
        if not course_data:
            return False
        
        course_id = course_data['id']
        
        # Get modules for course
        modules = from_db.get_course_modules(course_id)
        
        # Generate quizzes for each module
        for module in modules:
            # Generate 3-4 questions per module
            for i in range(3):
                question_data = await generate_quiz_question(
                    topic=course['topics'][min(i, len(course['topics'])-1)],
                    difficulty="medium"
                )
                
                if question_data:
                    from_db.add_quiz(
                        module_id=module['id'],
                        topic=course['topics'][min(i, len(course['topics'])-1)],
                        question=question_data['question'],
                        options=question_data['options'],
                        correct_answer=question_data['correct_answer'],
                        explanation=question_data['explanation'],
                        difficulty="medium",
                        points=10
                    )
        
        logger.info(f"Initialized quizzes for course: {course['title']}")
        return True
    except Exception as e:
        logger.error(f"Error initializing quizzes: {e}")
        return False


async def get_difficulty_for_user(user_stats: Dict) -> str:
    """Determine quiz difficulty based on user performance"""
    if not user_stats:
        return "medium"
    
    completed = user_stats.get('completed_courses', 0)
    
    if completed == 0:
        return "easy"
    elif completed < 3:
        return "medium"
    else:
        return "hard"


async def generate_adaptive_content(user_id: int, weak_areas: List[str]) -> Optional[Dict]:
    """Generate personalized content based on weak areas"""
    try:
        if not weak_areas:
            return None
        
        prompt = f"""
Du bist ein personalisierter Lerncoach und erstellst zusätzliches Lernmaterial.

Weak Areas: {', '.join(weak_areas)}

Erstelle fokussiertes Zusatzmaterial mit:
- 3 einfache Erklärvideos/Konzepte
- 2 praktische Übungen
- 1 Zusammenfassung

Antworte nur mit gültigem JSON:
{{
    "concept_1": "Detaillierte Erklärung 1",
    "concept_2": "Detaillierte Erklärung 2",
    "concept_3": "Detaillierte Erklärung 3",
    "exercise_1": "Praktische Übung 1",
    "exercise_2": "Praktische Übung 2",
    "summary": "Zusammenfassung der Konzepte"
}}
"""
        
        response = await client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1500
        )
        
        content = response.choices[0].message.content.strip()
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(content[start:end])
            return None
    except Exception as e:
        logger.error(f"Error generating adaptive content: {e}")
        return None
