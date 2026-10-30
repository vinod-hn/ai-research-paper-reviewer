"""
PDF Text Extraction Module
==========================
Extracts structured academic text from downloaded or uploaded research papers.
Features:
- Robust multi-engine extraction (PyMuPDF / fitz + PyMuPDF4LLM markdown layout)
- Scanned PDF detection with explicit OCR notification
- Multi-strategy academic section segmentation (Abstract, Introduction, Methods, Results, Discussion, Conclusion, References)
- Key findings and contribution extraction
- Text cleaning (de-hyphenation, header/footer stripping, unicode normalization)
- Quality assessment and metadata persistence
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pymupdf4llm
except ImportError:
    pymupdf4llm = None

# Paths
LOG_DIR = "data/logs"
EXTRACTED_DIR = "data/extracted"
METADATA_PATH = "data/metadata/selected_papers.json"
USE_PYMUPDF_LAYOUT = os.getenv("USE_PYMUPDF_LAYOUT", "false").lower() in {"1", "true", "yes"}

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(EXTRACTED_DIR, exist_ok=True)

logger = logging.getLogger("modules.extract_text")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, "extract_text.log"), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)


def sanitize_filename(filename: str) -> str:
    """Return a filesystem-safe filename."""
    invalid_chars = '<>:"/\\|?*\x00-\x1f'
    for char in invalid_chars:
        filename = filename.replace(char, "_")
    return filename.strip(" ._")[:180] or "paper"


def load_dataset(path: str = METADATA_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_dataset(data: Dict[str, Any], path: str = METADATA_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    logger.info("Updated dataset written to %s", path)


def clean_academic_text(text: str) -> str:
    """Clean extracted PDF text: fix line breaks, hyphenated words, excessive whitespace."""
    if not text:
        return ""
    # Fix words split across line breaks (e.g., 'classifi-\ncation' -> 'classification')
    text = re.sub(r'(\b\w+)-\n(\w+\b)', r'\1\2', text)
    # Remove control characters except newlines/tabs
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Replace multiple spaces with a single space
    text = re.sub(r'[ \t]+', ' ', text)
    # Replace more than 3 consecutive newlines with 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_with_fitz(pdf_path: str, max_pages: int = 50) -> Tuple[str, int, bool]:
    """
    Extract text using PyMuPDF (fitz).
    Returns (text, page_count, is_scanned).
    """
    if fitz is None:
        raise ImportError("PyMuPDF (fitz) is not installed")
    
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    
    if page_count == 0:
        doc.close()
        return "", 0, False
    
    # Process up to max_pages
    pages_to_read = min(page_count, max_pages)
    texts: List[str] = []
    total_chars = 0
    
    for i in range(pages_to_read):
        page = doc[i]
        p_text = page.get_text("text") or ""
        total_chars += len(p_text.strip())
        texts.append(p_text)
    
    doc.close()
    
    combined = "\n\n".join(texts)
    combined = clean_academic_text(combined)
    
    # Check if scanned (fewer than 60 chars per page on average)
    avg_chars = total_chars / max(pages_to_read, 1)
    is_scanned = (avg_chars < 60 and page_count > 0)
    
    return combined, page_count, is_scanned


def _extract_with_pymupdf4llm(pdf_path: str) -> str:
    """Extract markdown layout using pymupdf4llm.to_markdown."""
    if pymupdf4llm is None or not hasattr(pymupdf4llm, "to_markdown"):
        raise ImportError("pymupdf4llm.to_markdown not available")
    md_text = pymupdf4llm.to_markdown(pdf_path)
    return clean_academic_text(md_text or "")


def get_extractor_plan(
    use_layout: bool = USE_PYMUPDF_LAYOUT,
    have_pymupdf4llm: Optional[bool] = None,
    have_fitz: Optional[bool] = None
) -> List[str]:
    """Return the ordered extractor plan."""
    has_mupdf4llm = pymupdf4llm is not None if have_pymupdf4llm is None else have_pymupdf4llm
    has_fitz = fitz is not None if have_fitz is None else have_fitz

    plan: List[str] = []
    if use_layout:
        if has_mupdf4llm:
            plan.append("pymupdf4llm")
        if has_fitz:
            plan.append("pymupdf")
    else:
        if has_fitz:
            plan.append("pymupdf")
        if has_mupdf4llm:
            plan.append("pymupdf4llm")
    return plan


def extract_pdf_text(pdf_path: str) -> Tuple[str, str, int, bool]:
    """
    Extract text from a PDF file using the best available method.
    Returns: (text, method_used, total_pages, is_scanned)
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")
    
    plan = get_extractor_plan()
    last_error = None
    
    for method in plan:
        try:
            if method == "pymupdf4llm" and pymupdf4llm is not None:
                text = _extract_with_pymupdf4llm(pdf_path)
                if text and len(text.strip()) > 100:
                    # Get page count via fitz
                    p_count = 0
                    if fitz:
                        try:
                            d = fitz.open(pdf_path)
                            p_count = len(d)
                            d.close()
                        except Exception:
                            pass
                    return text, "pymupdf4llm", p_count, False
            
            elif method == "fitz" and fitz is not None:
                text, p_count, is_scanned = _extract_with_fitz(pdf_path)
                if is_scanned:
                    return text, "fitz_scanned", p_count, True
                if text and len(text.strip()) > 50:
                    return text, "fitz", p_count, False
        except Exception as exc:
            last_error = exc
            logger.warning(f"Extraction with {method} failed for {pdf_path}: {exc}")
    
    # Final fallback attempt with plain fitz
    if fitz is not None:
        try:
            text, p_count, is_scanned = _extract_with_fitz(pdf_path)
            return text, "fitz_fallback", p_count, is_scanned
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"All extraction methods failed for {pdf_path}: {last_error}")


# =============================================================================
# MULTI-STRATEGY ACADEMIC SECTION EXTRACTION
# =============================================================================

SECTION_HEADERS = {
    "abstract": [
        r"(?i)\babstract\b",
        r"(?i)\bsummary\b"
    ],
    "introduction": [
        r"(?i)(?:^|\n)\s*(?:I|1)\.?\s*INTRODUCTION\b",
        r"(?i)(?:^|\n)\s*INTRODUCTION\b",
        r"(?i)(?:^|\n)\s*(?:I|1)\.?\s*OVERVIEW\b"
    ],
    "methods": [
        r"(?i)(?:^|\n)\s*(?:II|III|2|3|4)\.?\s*(?:METHODOLOGY|METHODS|PROPOSED\s+(?:METHOD|APPROACH|SYSTEM|MODEL)|MATERIALS\s+AND\s+METHODS)\b",
        r"(?i)(?:^|\n)\s*(?:METHODOLOGY|PROPOSED\s+METHOD|PROPOSED\s+APPROACH|SYSTEM\s+ARCHITECTURE|METHOD)\b"
    ],
    "results": [
        r"(?i)(?:^|\n)\s*(?:III|IV|V|3|4|5|6)\.?\s*(?:RESULTS|EXPERIMENTS|EXPERIMENTAL\s+RESULTS|EVALUATION|FINDINGS|PERFORMANCE\s+EVALUATION)\b",
        r"(?i)(?:^|\n)\s*(?:RESULTS\s+AND\s+DISCUSSION|EXPERIMENTAL\s+EVALUATION|EXPERIMENTS|RESULTS)\b"
    ],
    "discussion": [
        r"(?i)(?:^|\n)\s*(?:IV|V|VI|4|5|6|7)\.?\s*(?:DISCUSSION|LIMITATIONS|CHALLENGES)\b",
        r"(?i)(?:^|\n)\s*DISCUSSION\b"
    ],
    "conclusion": [
        r"(?i)(?:^|\n)\s*(?:V|VI|VII|5|6|7|8)\.?\s*(?:CONCLUSION|CONCLUSIONS|CONCLUDING\s+REMARKS|SUMMARY\s+AND\s+CONCLUSIONS)\b",
        r"(?i)(?:^|\n)\s*(?:CONCLUSION|CONCLUSIONS)\b"
    ],
    "references": [
        r"(?i)(?:^|\n)\s*(?:REFERENCES|BIBLIOGRAPHY|LITERATURE\s+CITED)\b"
    ]
}


def find_section_spans(text: str) -> List[Tuple[str, int, int]]:
    """
    Find all academic section start positions and sort them sequentially.
    Returns list of (section_name, match_start, match_end).
    """
    found_headers = []
    
    for section_name, patterns in SECTION_HEADERS.items():
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                found_headers.append((section_name, match.start(), match.end()))
                break  # Take first matching pattern per section
    
    # Sort by start offset in text
    found_headers.sort(key=lambda x: x[1])
    
    # Remove duplicate sections, keeping earliest reasonable match
    unique_sections = []
    seen = set()
    for name, s, e in found_headers:
        if name not in seen:
            unique_sections.append((name, s, e))
            seen.add(name)
            
    return unique_sections


def extract_all_sections(text: str) -> Dict[str, Optional[str]]:
    """
    Segment the academic paper into structured sections.
    """
    sections: Dict[str, Optional[str]] = {
        "abstract": None,
        "introduction": None,
        "methods": None,
        "results": None,
        "discussion": None,
        "conclusion": None,
        "references": None
    }
    
    if not text:
        return sections
    
    # First: Dedicated Abstract extraction (often appears right at top)
    abs_match = re.search(r"(?i)\babstract\b[\s\:\.\-—\n]*(.*?)(?=\n\s*(?:1\.?|I\.?|index terms|keywords|introduction)|\Z)", text, re.DOTALL)
    if abs_match:
        abs_text = abs_match.group(1).strip()
        if 50 < len(abs_text) < 4000:
            sections["abstract"] = abs_text
    
    # Second: Sequential section segmentation by headers
    spans = find_section_spans(text)
    
    for i, (name, s_start, s_end) in enumerate(spans):
        # Section ends at next header or end of document
        content_start = s_end
        if i + 1 < len(spans):
            content_end = spans[i + 1][1]
        else:
            content_end = len(text)
        
        content = text[content_start:content_end].strip()
        # Clean section content
        content = re.sub(r'^[ \:\.\-\n]+', '', content)
        
        # Keep if valid length
        if len(content) > 60:
            if not sections[name]:  # Don't overwrite abstract if already extracted cleanly
                sections[name] = content[:15000]  # Cap each section to avoid giant memory blobs
    
    # Third: Fallback heuristics for missing essential sections
    if not sections["abstract"]:
        # First 1500 characters usually contain abstract or initial summary
        top_slice = text[:2500]
        # Look for first two paragraphs
        paras = [p.strip() for p in top_slice.split("\n\n") if len(p.strip()) > 80]
        if paras:
            sections["abstract"] = paras[0][:1500]
            
    if not sections["conclusion"]:
        # Check last 10,000 chars for conclusion-like closing paragraphs before references
        ref_idx = text.rfind("References")
        search_region = text[max(0, ref_idx - 6000):ref_idx] if ref_idx > 0 else text[-6000:]
        c_match = re.search(r"(?i)(?:in conclusion|to conclude|we presented|in this paper we|summary)[\s\:\.\-—\n]*(.*?)(?=\n\s*references|\Z)", search_region, re.DOTALL)
        if c_match:
            cand = c_match.group(0).strip()
            if len(cand) > 80:
                sections["conclusion"] = cand[:4000]
    
    # Quality metrics
    found_count = sum(1 for k in ["abstract", "introduction", "methods", "results", "conclusion"] if sections.get(k))
    sections["_extraction_quality"] = {
        "sections_found": found_count,
        "total_sections": 5,
        "quality_score": round(found_count / 5.0, 2)
    }
    
    return sections


# =============================================================================
# KEY FINDINGS EXTRACTION
# =============================================================================

FINDINGS_PATTERNS = [
    r"(?i)(?:we\s+(?:found|demonstrate|show|observed|discover|achieved|propose))\s+(?:that\s+)?([^.\n]{25,250}\.)",
    r"(?i)(?:our\s+results?\s+(?:show|indicate|demonstrate|suggest|reveal))\s+(?:that\s+)?([^.\n]{25,250}\.)",
    r"(?i)(?:experimental\s+results?\s+(?:show|demonstrate|confirm))\s+(?:that\s+)?([^.\n]{25,250}\.)",
    r"(?i)(?:outperforms?|superior\s+to|state-of-the-art|best\s+performance)\s+([^.\n]{25,250}\.)",
    r"(?i)(?:accuracy|f1-score|auc|precision|recall)\s+of\s+([0-9]+(?:\.[0-9]+)?%?[^.\n]{15,150}\.)"
]


def extract_key_findings(text: str, sections: Dict[str, Any], max_findings: int = 6) -> List[str]:
    """Extract verifiable key findings from results, conclusion, abstract or full text."""
    findings: List[str] = []
    
    search_corpora = []
    if sections.get("results"):
        search_corpora.append(sections["results"])
    if sections.get("conclusion"):
        search_corpora.append(sections["conclusion"])
    if sections.get("abstract"):
        search_corpora.append(sections["abstract"])
    search_corpora.append(text[:12000])
    
    for corpus in search_corpora:
        for pat in FINDINGS_PATTERNS:
            for match in re.finditer(pat, corpus):
                cand = match.group(0).strip()
                cand = re.sub(r'\s+', ' ', cand)
                # Filter out generic citations or overly short sentences
                if len(cand) > 35 and not any(cand.lower() in f.lower() or f.lower() in cand.lower() for f in findings):
                    findings.append(cand)
                    if len(findings) >= max_findings:
                        return findings
                        
    return findings[:max_findings]


def write_text_file(text: str, paper_id: str, title: str, output_dir: str = EXTRACTED_DIR) -> str:
    """Save extracted text to text file."""
    os.makedirs(output_dir, exist_ok=True)
    safe_title = sanitize_filename(title or "untitled")
    safe_id = sanitize_filename(paper_id or "paper")
    filename = f"{safe_id}_{safe_title}.txt"
    output_path = os.path.join(output_dir, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)
    return output_path


def extract_paper_text(paper: Dict[str, Any], output_dir: str = EXTRACTED_DIR) -> Dict[str, Any]:
    """
    Extract text for a single paper, parse structured sections, identify findings,
    and report scanned status.
    """
    res = paper.copy()
    pdf_path = res.get("pdf_path")
    
    if not pdf_path or not os.path.exists(pdf_path):
        res["extraction_status"] = "missing_pdf"
        res["text_path"] = None
        res["extraction_error"] = f"PDF file not found at {pdf_path}"
        return res

    try:
        text, method, page_count, is_scanned = extract_pdf_text(pdf_path)
        
        if is_scanned:
            res["extraction_status"] = "scanned_pdf"
            res["is_scanned"] = True
            res["ocr_needed"] = True
            res["page_count"] = page_count
            res["extraction_error"] = "Scanned document detected with no embedded text layer. OCR required."
            logger.warning(f"Paper '{res.get('title')}' is a scanned PDF with no text layer.")
            return res
        
        if not text or len(text.strip()) < 100:
            res["extraction_status"] = "empty_text"
            res["extraction_error"] = "Extracted text is empty or too short."
            return res

        text_path = write_text_file(text, res.get("paper_id", "paper"), res.get("title", "untitled"), output_dir)
        sections = extract_all_sections(text)
        key_findings = extract_key_findings(text, sections)
        
        res["text_path"] = text_path
        res["extraction_status"] = "success"
        res["extraction_method"] = method
        res["page_count"] = page_count
        res["is_scanned"] = False
        res["extraction_date"] = time.strftime("%Y-%m-%d %H:%M:%S")
        res["text_characters"] = len(text)
        res["sections"] = {k: v for k, v in sections.items() if not k.startswith("_")}
        res["sections_quality"] = sections.get("_extraction_quality", {})
        res["key_findings"] = key_findings
        res.pop("extraction_error", None)
        
        quality = res["sections_quality"].get("quality_score", 0)
        logger.info(f"Extracted {len(text)} chars from {pdf_path} (Method: {method}, Quality: {quality:.2f})")
        
    except Exception as exc:
        res["text_path"] = None
        res["extraction_status"] = "failed"
        res["extraction_error"] = str(exc)
        res["sections"] = {}
        res["key_findings"] = []
        logger.error(f"Failed to extract text for '{res.get('title')}': {exc}")
        
    return res


def extract_text_for_papers(papers: List[Dict[str, Any]], output_dir: str = EXTRACTED_DIR) -> List[Dict[str, Any]]:
    """Extract text for a batch of papers."""
    updated: List[Dict[str, Any]] = []
    total = len(papers)
    logger.info(f"Starting text extraction for {total} papers")
    
    for idx, paper in enumerate(papers, 1):
        updated.append(extract_paper_text(paper, output_dir))
    
    success_count = sum(1 for p in updated if p.get("extraction_status") == "success")
    logger.info(f"Text extraction complete: {success_count} / {total} succeeded")
    return updated


def process_dataset(
    metadata_path: str = METADATA_PATH,
    output_dir: str = EXTRACTED_DIR,
) -> Dict[str, Any]:
    """Load dataset, extract text for each paper, and save results."""
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")
    
    dataset = load_dataset(metadata_path)
    papers = dataset.get("papers", [])
    if not papers:
        logger.warning("No papers in dataset for extraction.")
        return {"total": 0, "success": 0, "failed": 0}
    
    updated_papers = extract_text_for_papers(papers, output_dir)
    dataset["papers"] = updated_papers
    save_dataset(dataset, metadata_path)
    
    return {
        "total": len(updated_papers),
        "success": sum(1 for p in updated_papers if p.get("extraction_status") == "success"),
        "failed": sum(1 for p in updated_papers if p.get("extraction_status") in ("failed", "missing_pdf")),
        "scanned": sum(1 for p in updated_papers if p.get("extraction_status") == "scanned_pdf"),
    }
