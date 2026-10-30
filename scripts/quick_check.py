"""
Quick System Integrity & File Structure Verification
====================================================
Verifies that all required application files, directories, and modules are present.
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def check_files():
    """Check existence of all project core files."""
    print("Verifying core project files:")
    files = {
        "run.py": "Main CLI and App Launcher",
        "requirements.txt": "Package Dependencies",
        "README.md": "Documentation",
        ".env.example": "Configuration Template",
        "modules/search_papers.py": "Paper Search & Ranking Module",
        "modules/download_pdf.py": "PDF Downloader Module",
        "modules/extract_text.py": "PDF Text Extraction Module",
        "modules/analyze_text.py": "Academic Analysis & Matrix Module",
        "modules/generate_draft.py": "Literature Review Generator",
        "modules/critique_draft.py": "Critique & Revision Engine",
        "modules/workflow.py": "Workflow Orchestration Engine",
        "modules/security.py": "Input Validation & Security Guard",
        "ui/app_modern.py": "Research Productivity UI",
        "tests/test_all.py": "Automated Test Suite"
    }
    
    all_ok = True
    for filepath, desc in files.items():
        full_path = os.path.join(ROOT, filepath)
        if os.path.exists(full_path):
            print(f"  [OK]   {filepath:30} - {desc}")
        else:
            print(f"  [FAIL] {filepath:30} - MISSING ({desc})")
            all_ok = False
            
    return all_ok


def check_folders():
    """Check and ensure required directories exist."""
    print("\nVerifying working storage directories:")
    folders = [
        "data",
        "data/pdfs",
        "data/extracted",
        "data/metadata",
        "data/logs",
        "modules",
        "ui",
        "scripts",
        "tests"
    ]
    for folder in folders:
        full_path = os.path.join(ROOT, folder)
        if os.path.exists(full_path):
            print(f"  [OK]   {folder}")
        else:
            print(f"  [MKDIR] Creating missing folder: {folder}")
            os.makedirs(full_path, exist_ok=True)


def main():
    print("=" * 65)
    print("AI RESEARCH PAPER REVIEWER - INTEGRITY CHECK")
    print("=" * 65 + "\n")
    
    files_ok = check_files()
    check_folders()
    
    print("\n" + "=" * 65)
    if files_ok:
        print("[SUCCESS] PROJECT STRUCTURE VERIFIED: Ready to run.")
        print("To launch the web interface:  python run.py --ui")
        print("To run the test suite:       python run.py --test")
    else:
        print("[WARNING] SOME PROJECT FILES ARE MISSING.")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
