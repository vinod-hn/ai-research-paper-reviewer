"""
Dependency & Environment Checker
================================
Verifies that all required packages, AI SDKs, and document processing tools are installed.
"""

import sys
import os
import importlib

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def check_import(package_name: str, display_name: str = None) -> bool:
    """Check if a package can be imported and print its version."""
    if display_name is None:
        display_name = package_name
    
    try:
        module = importlib.import_module(package_name)
        version = getattr(module, '__version__', 'available')
        print(f"  [OK]   {display_name:28} (v{version})")
        return True
    except ImportError as e:
        print(f"  [FAIL] {display_name:28} - NOT INSTALLED ({e})")
        return False
    except Exception as e:
        print(f"  [WARN] {display_name:28} - Warning ({e})")
        return True


def check_api_keys():
    """Check status of API keys in environment."""
    print("\nAPI Keys & Environment:")
    print("-" * 65)
    from dotenv import load_dotenv
    load_dotenv()
    
    sem_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    
    if sem_key and not sem_key.startswith("your_"):
        print("  [OK]   SEMANTIC_SCHOLAR_API_KEY: Configured")
    else:
        print("  [INFO] SEMANTIC_SCHOLAR_API_KEY: Not set (Public rate limits will apply)")
        
    if openai_key and not openai_key.startswith("your_"):
        print("  [OK]   OPENAI_API_KEY:           Configured")
    else:
        print("  [INFO] OPENAI_API_KEY:           Not set (High-precision offline NLP engine active)")


def main():
    print("=" * 65)
    print("AI RESEARCH PAPER REVIEWER - DEPENDENCY VERIFICATION")
    print("=" * 65 + "\n")
    
    print("Core Dependencies:")
    print("-" * 65)
    core = [
        ('semanticscholar', 'semanticscholar (API)'),
        ('requests', 'requests (HTTP)'),
        ('dotenv', 'python-dotenv (Config)'),
        ('tqdm', 'tqdm (Progress)'),
        ('pydantic', 'pydantic (Validation)'),
        ('gradio', 'gradio (Web UI)'),
    ]
    core_ok = all([check_import(pkg, disp) for pkg, disp in core])
    
    print("\nDocument & PDF Processing:")
    print("-" * 65)
    docs = [
        ('fitz', 'PyMuPDF / fitz (PDF)'),
        ('pymupdf4llm', 'pymupdf4llm (Markdown Layout)'),
        ('docx', 'python-docx (DOCX Export)'),
        ('reportlab', 'reportlab (PDF Export)'),
    ]
    docs_ok = all([check_import(pkg, disp) for pkg, disp in docs])
    
    print("\nAI & Workflow Engines:")
    print("-" * 65)
    ai = [
        ('openai', 'openai (LLM Engine)'),
        ('langgraph', 'langgraph (Workflow Graph)'),
        ('pytest', 'pytest (Test Framework)'),
    ]
    ai_ok = all([check_import(pkg, disp) for pkg, disp in ai])
    
    check_api_keys()
    
    print("\n" + "=" * 65)
    if core_ok and docs_ok and ai_ok:
        print("[SUCCESS] ALL ESSENTIAL DEPENDENCIES ARE PROPERLY INSTALLED")
        print(f"Python Runtime: {sys.version.split()[0]} ({sys.executable})")
    else:
        print("[WARNING] SOME OPTIONAL DEPENDENCIES MISSING")
        print("Run: pip install -r requirements.txt")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
