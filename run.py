"""
AI Research Paper Reviewer - Main Application CLI Entry Point
============================================================
Run this script to launch the application UI, execute CLI research pipelines,
or run automated verification checks.

Examples:
  python run.py --ui                                    # Launch modern Web UI
  python run.py --check                                 # Verify dependencies & setup
  python run.py --test                                  # Execute automated test suite
  python run.py --run --topic "deep learning NLP"       # Run full end-to-end pipeline
  python run.py --run --topic "medical image" --limit 5 --top-n 3
"""

import os
import sys
import argparse
import subprocess

# Add project root to path
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def run_check_imports():
    """Verify all dependencies, SDKs, and directory structures."""
    print("\n" + "=" * 65)
    print("AI RESEARCH PAPER REVIEWER - SYSTEM DEPENDENCY CHECK")
    print("=" * 65)
    from scripts.check_imports import main
    main()


def run_workflow_test():
    """Execute pytest test suite."""
    print("\n" + "=" * 65)
    print("RUNNING AUTOMATED TEST SUITE")
    print("=" * 65 + "\n")
    cmd = [sys.executable, "-m", "pytest", "-v"]
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print("\n" + "=" * 65)
        print("✅ ALL AUTOMATED TESTS PASSED SUCCESSFULLY")
        print("=" * 65)
    else:
        print("\n" + "=" * 65)
        print("❌ SOME TESTS FAILED")
        print("=" * 65)


def run_full_pipeline(topic: str, limit: int, min_year: int, min_citations: int, top_n: int):
    """Execute the full end-to-end research paper review pipeline from the CLI."""
    print("\n" + "=" * 65)
    print(f"RUNNING RESEARCH PIPELINE: '{topic}'")
    print("=" * 65)
    
    from modules.workflow import run_pipeline
    
    def progress_log(pct: float, stage: str):
        print(f"[{int(pct * 100):3d}%] {stage}")
        
    state = run_pipeline(
        topic=topic,
        limit=limit,
        year_min=min_year,
        min_citations=min_citations,
        top_n=top_n,
        progress_callback=progress_log
    )
    
    print("\n" + "=" * 65)
    print(f"PIPELINE COMPLETED - Status: {state.get('status')}")
    print("=" * 65)
    final_draft = state.get("final_draft", {})
    if final_draft:
        print(f"Papers Reviewed: {final_draft.get('papers_reviewed')}")
        print(f"Quality Grade:   {final_draft.get('quality_grade')} ({final_draft.get('quality_score', 0):.0%})")
        print(f"Total Words:     {final_draft.get('word_count')}")
        print(f"\nFinal literature review saved to data/metadata/drafts.json and critiques.json")
        
        try:
            from ui.app_modern import generate_pdf_export, generate_docx_export, generate_markdown_export
            pdf_p = generate_pdf_export()
            docx_p = generate_docx_export()
            md_p = generate_markdown_export()
            print("\nAutomated Exports:")
            if pdf_p:
                print(f"  [PDF]  {pdf_p}")
            if docx_p:
                print(f"  [DOCX] {docx_p}")
            if md_p:
                print(f"  [MD]   {md_p}")
        except Exception as exp_err:
            print(f"  Export warning: {exp_err}")
    if state.get("errors"):
        print("\nErrors / Warnings:")
        for err in state["errors"]:
            print(f"  - {err}")
            
    return state


def run_ui(port: int = 7860, share: bool = False):
    """Launch the modern research workspace Web UI."""
    print("\n" + "=" * 65)
    print("LAUNCHING AI RESEARCH PAPER REVIEWER WORKSPACE")
    print("=" * 65)
    print(f"\nAccess the application at: http://127.0.0.1:{port}")
    print("Press Ctrl+C to terminate\n")
    
    from ui.app_modern import create_app
    app = create_app()
    app.launch(server_name="127.0.0.1", server_port=port, share=share)


def main():
    parser = argparse.ArgumentParser(
        description="AI Research Paper Reviewer - CLI and Application Entry Point",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py --ui                                    # Launch modern Web UI
  python run.py --check                                 # Verify dependencies & setup
  python run.py --test                                  # Execute automated test suite
  python run.py --run --topic "deep learning NLP"       # Run research pipeline
  python run.py --run --topic "medical image" --limit 5 --top-n 3
        """
    )
    
    parser.add_argument('--ui', action='store_true', help='Launch modern Web UI (Gradio)')
    parser.add_argument('--check', action='store_true', help='Verify dependencies and environment setup')
    parser.add_argument('--test', action='store_true', help='Run automated pytest test suite')
    parser.add_argument('--run', action='store_true', help='Execute complete research paper review pipeline')
    
    # Pipeline Arguments
    parser.add_argument('--topic', type=str, default='deep learning medical image analysis',
                        help='Research query or topic')
    parser.add_argument('--limit', type=int, default=10,
                        help='Maximum search results to retrieve (default: 10)')
    parser.add_argument('--min-year', type=int, default=2020,
                        help='Minimum publication year filter (default: 2020)')
    parser.add_argument('--min-citations', type=int, default=10,
                        help='Minimum citation count filter (default: 10)')
    parser.add_argument('--top-n', type=int, default=3,
                        help='Number of top ranked papers to review (default: 3)')
    parser.add_argument('--port', type=int, default=7860,
                        help='Server port for Web UI (default: 7860)')
    parser.add_argument('--share', action='store_true',
                        help='Create a public shareable Gradio link')
    
    args = parser.parse_args()
    
    if len(sys.argv) == 1:
        # Default with no arguments: show usage and prompt
        parser.print_help()
        print("\nTip: To start the graphical interface, run:")
        print("  python run.py --ui\n")
        return
        
    if args.check:
        run_check_imports()
    elif args.test:
        run_workflow_test()
    elif args.run:
        run_full_pipeline(args.topic, args.limit, args.min_year, args.min_citations, args.top_n)
    elif args.ui:
        run_ui(port=args.port, share=args.share)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
