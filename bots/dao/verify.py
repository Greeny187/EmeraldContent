#!/usr/bin/env python3
"""
Emerald DAO - Installation & Configuration Verification Script
Überprüft ob alle Komponenten korrekt installiert und konfiguriert sind
"""

import os
import sys
import json
from pathlib import Path

# Farben für Terminal Output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    END = '\033[0m'

def check(condition, message):
    """Überprüfe und gebe Ergebnis aus"""
    if condition:
        print(f"{Colors.GREEN}✅{Colors.END} {message}")
        return True
    else:
        print(f"{Colors.RED}❌{Colors.END} {message}")
        return False

def verify_environment():
    """Überprüfe Environment Variables"""
    print(f"\n{Colors.BLUE}🔍 Environment Variables{Colors.END}")
    
    required_vars = [
        'TELEGRAM_BOT_TOKEN',
        'DATABASE_URL',
    ]
    
    optional_vars = [
        'DAO_MINIAPP_URL',
        'TONAPI_KEY',
        'DAO_CONTRACT',
        'EMRD_TOKEN',
        'DAO_TREASURY',
    ]
    
    all_good = True
    
    for var in required_vars:
        value = os.getenv(var)
        check(value, f"Required: {var}")
        all_good = all_good and bool(value)
    
    for var in optional_vars:
        value = os.getenv(var)
        status = f"Optional: {var}"
        if value:
            print(f"{Colors.GREEN}✅{Colors.END} {status}")
        else:
            print(f"{Colors.YELLOW}⚠️{Colors.END} {status} (not set)")
    
    return all_good

def verify_files():
    """Überprüfe erforderliche Dateien"""
    print(f"\n{Colors.BLUE}📁 Project Files{Colors.END}")
    
    base_path = Path(__file__).parent
    required_files = [
        'app.py',
        'handlers.py',
        'database.py',
        'miniapp.py',
        'auth.py',
        '__init__.py',
        'requirements.txt',
        'README.md',
        'DEPLOYMENT.md',
        'IMPLEMENTATION_SUMMARY.md',
        'CHANGELOG.md',
    ]
    
    miniapp_files = [
        '../miniapp/appdao.html',
        '../miniapp/story-dao.js',
        '../miniapp/tonconnect-dao.js',
    ]
    
    all_good = True
    
    for file in required_files:
        path = base_path / file
        check(path.exists(), f"Bot file: {file}")
        all_good = all_good and path.exists()
    
    for file in miniapp_files:
        path = base_path / file
        check(path.exists(), f"MiniApp file: {file}")
        all_good = all_good and path.exists()
    
    return all_good

def verify_packages():
    """Überprüfe installierte Packages"""
    print(f"\n{Colors.BLUE}📦 Python Packages{Colors.END}")
    
    required_packages = [
        'telegram',
        'aiohttp',
        'psycopg2',
        'dotenv',
    ]
    
    optional_packages = [
        'redis',
        'sqlalchemy',
        'pytest',
    ]
    
    all_good = True
    
    for package in required_packages:
        try:
            __import__(package)
            check(True, f"Required: {package}")
        except ImportError:
            check(False, f"Required: {package} (run: pip install {package})")
            all_good = False
    
    for package in optional_packages:
        try:
            __import__(package)
            print(f"{Colors.GREEN}✅{Colors.END} Optional: {package}")
        except ImportError:
            print(f"{Colors.YELLOW}⚠️{Colors.END} Optional: {package}")
    
    return all_good

def verify_database():
    """Überprüfe Datenbankverbindung"""
    print(f"\n{Colors.BLUE}🗄️  Database{Colors.END}")
    
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        check(False, "DATABASE_URL not set")
        return False
    
    try:
        import psycopg2
        from psycopg2 import connect
        
        # Parse connection string
        conn = connect(db_url)
        cursor = conn.cursor()
        
        check(True, "Database connection successful")
        
        # Check schemas
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        tables = cursor.fetchall()
        
        expected_tables = [
            'dao_proposals',
            'dao_votes',
            'dao_delegations',
            'dao_treasury',
            'dao_user_voting_power',
            'dao_vote_stats',
        ]
        
        found_tables = [t[0] for t in tables]
        
        for table in expected_tables:
            if table in found_tables:
                check(True, f"Table exists: {table}")
            else:
                check(False, f"Table missing: {table}")
        
        cursor.close()
        conn.close()
        
        return len([t for t in expected_tables if t in found_tables]) == len(expected_tables)
        
    except Exception as e:
        check(False, f"Database connection failed: {str(e)}")
        return False

def verify_api():
    """Überprüfe API Endpoints"""
    print(f"\n{Colors.BLUE}🔌 API Endpoints{Colors.END}")
    
    endpoints = [
        '/api/dao/proposals',
        '/api/dao/proposal',
        '/api/dao/proposal/create',
        '/api/dao/vote',
        '/api/dao/vote/stats',
        '/api/dao/vote/user',
        '/api/dao/voting-power',
        '/api/dao/voting-power/update',
        '/api/dao/delegate',
        '/api/dao/delegations',
        '/api/dao/treasury/balance',
        '/api/dao/treasury/transactions',
        '/api/dao/treasury/create-tx',
    ]
    
    # Check miniapp.py for endpoint registrations
    miniapp_file = Path(__file__).parent / 'miniapp.py'
    
    try:
        with open(miniapp_file, 'r') as f:
            content = f.read()
        
        all_good = True
        for endpoint in endpoints:
            if endpoint in content:
                check(True, f"Endpoint defined: {endpoint}")
            else:
                check(False, f"Endpoint missing: {endpoint}")
                all_good = False
        
        return all_good
    except Exception as e:
        check(False, f"Could not verify endpoints: {str(e)}")
        return False

def verify_handlers():
    """Überprüfe Bot Handlers"""
    print(f"\n{Colors.BLUE}🤖 Bot Handlers{Colors.END}")
    
    handlers = [
        'cmd_start',
        'cmd_proposals',
        'cmd_voting_power',
        'cmd_treasury',
        'cmd_help',
        'button_callback',
    ]
    
    handlers_file = Path(__file__).parent / 'handlers.py'
    
    try:
        with open(handlers_file, 'r') as f:
            content = f.read()
        
        all_good = True
        for handler in handlers:
            if handler in content:
                check(True, f"Handler defined: {handler}")
            else:
                check(False, f"Handler missing: {handler}")
                all_good = False
        
        return all_good
    except Exception as e:
        check(False, f"Could not verify handlers: {str(e)}")
        return False

def verify_frontend():
    """Überprüfe Frontend"""
    print(f"\n{Colors.BLUE}🎨 Frontend{Colors.END}")
    
    html_file = Path(__file__).parent / '../miniapp/appdao.html'
    js_file = Path(__file__).parent / '../miniapp/story-dao.js'
    
    try:
        # Check HTML
        with open(html_file, 'r') as f:
            html_content = f.read()
        
        html_checks = [
            ('Emerald Color', '#00D084' in html_content),
            ('Tabs', 'data-tab' in html_content),
            ('Forms', '<form' in html_content or '<input' in html_content),
            ('CSS Variables', '--emerald-primary' in html_content),
        ]
        
        all_good = True
        for check_name, result in html_checks:
            check(result, f"HTML: {check_name}")
            all_good = all_good and result
        
        # Check JavaScript
        with open(js_file, 'r') as f:
            js_content = f.read()
        
        js_checks = [
            ('API Functions', 'async function' in js_content or 'apiCall' in js_content),
            ('State Management', 'const state' in js_content),
            ('Render Functions', 'function render' in js_content),
            ('Event Handlers', 'addEventListener' in js_content or 'onclick' in html_content),
        ]
        
        for check_name, result in js_checks:
            check(result, f"JavaScript: {check_name}")
            all_good = all_good and result
        
        return all_good
        
    except Exception as e:
        check(False, f"Could not verify frontend: {str(e)}")
        return False

def main():
    """Führe alle Überprüfungen durch"""
    print(f"\n{Colors.BLUE}{'='*50}")
    print(f"Emerald DAO - Installation Verification")
    print(f"{'='*50}{Colors.END}\n")
    
    results = []
    
    results.append(('Environment', verify_environment()))
    results.append(('Files', verify_files()))
    results.append(('Packages', verify_packages()))
    results.append(('Database', verify_database()))
    results.append(('API Endpoints', verify_api()))
    results.append(('Bot Handlers', verify_handlers()))
    results.append(('Frontend', verify_frontend()))
    
    # Summary
    print(f"\n{Colors.BLUE}{'='*50}")
    print(f"Verification Summary")
    print(f"{'='*50}{Colors.END}\n")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = f"{Colors.GREEN}✅ PASS{Colors.END}" if result else f"{Colors.RED}❌ FAIL{Colors.END}"
        print(f"{status} - {name}")
    
    print(f"\n{Colors.BLUE}Result: {passed}/{total} checks passed{Colors.END}\n")
    
    if passed == total:
        print(f"{Colors.GREEN}✅ All checks passed! DAO is ready to go.{Colors.END}\n")
        print(f"Next steps:")
        print(f"  1. Start bot: python main.py")
        print(f"  2. Open mini app: http://localhost:3000/miniapp/appdao.html")
        print(f"  3. Create test proposal: /proposals command")
        return 0
    else:
        print(f"{Colors.RED}❌ Some checks failed. Please fix the issues above.{Colors.END}\n")
        print(f"See docs:")
        print(f"  - README.md for features")
        print(f"  - DEPLOYMENT.md for setup")
        print(f"  - QUICKSTART.md for quick setup")
        return 1

if __name__ == '__main__':
    sys.exit(main())
