"""
PDF Download Module 
==================================
Implements robust academic PDF download functionality with:
- Download papers from direct URLs and landing pages
- Automatic arXiv abstract -> PDF resolution
- Comprehensive PDF integrity validation (magic bytes, size, and fitz structure check)
- Duplicate download prevention and MD5 checksumming
- Temporary download file handling with atomic rename
- Configurable timeouts and exponential backoff retry logic
- Non-retryable domain short-circuiting
- Progress tracking and structured audit logging
"""

import os
import json
import logging
import time
import hashlib
from typing import Dict, List, Optional, Any, Set
from urllib.parse import urljoin
import requests
from tqdm import tqdm
from dotenv import load_dotenv

# Ensure logs directory exists
LOG_DIR = "data/logs"
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("modules.download_pdf")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, "download_pdf.log"), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

load_dotenv()

# Constants
PDF_DIR = "data/pdfs"
METADATA_DIR = "data/metadata"
CHUNK_SIZE = 8192
MAX_RETRIES = int(os.getenv("PDF_DOWNLOAD_MAX_RETRIES", "3"))
TIMEOUT = int(os.getenv("PDF_DOWNLOAD_TIMEOUT", "25"))
RETRY_ONLY_ON_RETRYABLE = os.getenv("PDF_RETRY_ONLY_ON_RETRYABLE", "1") == "1"
MAX_HTML_SNIFF = 200000


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to remove invalid characters and limit length."""
    invalid_chars = '<>:"/\\|?*\x00-\x1f'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    filename = filename.strip(' ._')
    return filename[:180] or "paper"


def validate_pdf(filepath: str) -> bool:
    """
    Validate that the file is an intact, non-empty, readable PDF.
    Verifies magic header (%PDF), minimum viable size, and fitz open test.
    """
    try:
        if not os.path.exists(filepath):
            logger.warning(f"File does not exist: {filepath}")
            return False
        
        file_size = os.path.getsize(filepath)
        if file_size < 1024:  # academic papers are practically always > 1KB
            logger.warning(f"File too small to be a valid PDF ({file_size} bytes): {filepath}")
            return False
        
        with open(filepath, 'rb') as f:
            header = f.read(5)
            if not header.startswith(b'%PDF'):
                logger.warning(f"Invalid PDF magic header: {filepath}")
                return False
        
        # Test open with fitz to ensure structure is not corrupted
        try:
            import fitz
            doc = fitz.open(filepath)
            page_count = len(doc)
            doc.close()
            if page_count < 1:
                logger.warning(f"PDF contains 0 pages: {filepath}")
                return False
        except Exception as fitz_err:
            logger.warning(f"Corrupted PDF structure detected by fitz for {filepath}: {fitz_err}")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error validating PDF {filepath}: {e}")
        return False


def _is_probable_pdf_response(response: requests.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type:
        return True
    disp = response.headers.get("content-disposition", "").lower()
    return "pdf" in disp


def _expand_candidate_urls(url: str) -> List[str]:
    """Return an ordered list of candidate direct PDF URLs."""
    candidates = []
    if not url:
        return candidates
    
    url = url.strip()
    candidates.append(url)
    
    # ArXiv abs -> pdf
    if "arxiv.org/abs/" in url:
        pdf_url = url.replace("/abs/", "/pdf/")
        if not pdf_url.lower().endswith(".pdf"):
            pdf_url += ".pdf"
        candidates.append(pdf_url)
    elif "arxiv.org" in url and not url.endswith(".pdf"):
        # e.g. arxiv.org/pdf/2301.02390
        candidates.append(url + ".pdf")
    
    # DOI resolver to Sciencedirect/MDPI/Nature
    if "doi.org" in url:
        pass
    
    # Deduplicate while preserving order
    seen: Set[str] = set()
    ordered: List[str] = []
    for u in candidates:
        if u not in seen:
            ordered.append(u)
            seen.add(u)
    return ordered


def _extract_pdf_link_from_html(html: str, base_url: str) -> Optional[str]:
    """Find a likely direct PDF link in HTML by looking for hrefs containing .pdf."""
    import re
    match = re.search(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', html, re.IGNORECASE)
    if match:
        href = match.group(1)
        return urljoin(base_url, href)
    return None


def download_pdf(
    url: str,
    paper_id: str,
    title: str,
    output_dir: str = PDF_DIR
) -> Optional[str]:
    """
    Download a single PDF from URL with retry logic, temp file integrity check, and validation.
    """
    os.makedirs(output_dir, exist_ok=True)
    
    safe_title = sanitize_filename(title)
    safe_id = sanitize_filename(paper_id)
    filename = f"{safe_id}_{safe_title}.pdf"
    filepath = os.path.join(output_dir, filename)
    
    # Check if already downloaded and valid
    if os.path.exists(filepath) and validate_pdf(filepath):
        logger.info(f"Valid PDF already cached locally: {filepath}")
        return filepath
    
    candidate_urls = _expand_candidate_urls(url)
    tried: Set[str] = set()
    attempt = 0
    temp_filepath = filepath + ".tmp"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }

    while candidate_urls and attempt < MAX_RETRIES:
        current_url = candidate_urls.pop(0)
        if current_url in tried:
            continue
        tried.add(current_url)
        attempt += 1

        try:
            logger.info(f"Downloading PDF (attempt {attempt}/{MAX_RETRIES}): {current_url}")
            response = requests.get(
                current_url,
                stream=True,
                timeout=TIMEOUT,
                headers=headers,
                allow_redirects=True
            )
            response.raise_for_status()

            # If the response is HTML, sniff for fallback PDF link
            if not _is_probable_pdf_response(response):
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type:
                    try:
                        html = response.content[:MAX_HTML_SNIFF].decode(errors="ignore")
                        fallback_pdf = _extract_pdf_link_from_html(html, current_url)
                        if fallback_pdf and fallback_pdf not in tried and fallback_pdf not in candidate_urls:
                            logger.info(f"Discovered fallback PDF link in HTML: {fallback_pdf}")
                            candidate_urls.insert(0, fallback_pdf)
                            continue
                    except Exception as sniff_exc:
                        logger.warning(f"HTML sniff failed for {current_url}: {sniff_exc}")
                logger.warning(f"Response is not a PDF for: {current_url} (content-type: {content_type})")
                continue

            total_size = int(response.headers.get('content-length', 0))
            
            with open(temp_filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
            
            # Validate temp file before committing
            if validate_pdf(temp_filepath):
                if os.path.exists(filepath):
                    os.remove(filepath)
                os.rename(temp_filepath, filepath)
                logger.info(f"Successfully downloaded and verified PDF: {filepath}")
                return filepath
            else:
                logger.warning(f"Downloaded payload failed validation: {current_url}")
                if os.path.exists(temp_filepath):
                    os.remove(temp_filepath)
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Download attempt {attempt} failed: {e}")
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
            if RETRY_ONLY_ON_RETRYABLE:
                non_retryable_hosts = ("ieeexplore.ieee.org", "link.springer.com", "sciencedirect.com")
                if any(h in current_url.lower() for h in non_retryable_hosts):
                    logger.warning("Host is paywalled/restricted; skipping further retries for this URL.")
                    break
        except Exception as e:
            logger.error(f"Unexpected error downloading PDF from {current_url}: {e}")
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)

        if attempt < MAX_RETRIES and candidate_urls:
            time.sleep(2 ** attempt)
        else:
            logger.error(f"Failed to download PDF for paper '{title[:40]}' after {attempt} attempts.")
            
    return None


def download_papers(
    papers: List[Dict[str, Any]],
    output_dir: str = PDF_DIR
) -> List[Dict[str, Any]]:
    """
    Download multiple papers, update download status, and return list of successful records.
    Does not mutate input paper dicts in-place.
    """
    logger.info(f"Starting batch download of {len(papers)} papers into '{output_dir}'")
    downloaded_papers: List[Dict[str, Any]] = []
    
    for idx, orig_paper in enumerate(papers, 1):
        paper = orig_paper.copy()
        title = paper.get('title', 'Unknown')
        paper_id = paper.get('paper_id', f'paper_{idx}')
        pdf_url = paper.get('pdf_url')
        
        # Check if user already provided a local PDF path (e.g. via direct upload)
        existing_path = paper.get('pdf_path')
        if existing_path and os.path.exists(existing_path) and validate_pdf(existing_path):
            paper['download_status'] = 'success'
            paper['download_date'] = paper.get('download_date') or time.strftime("%Y-%m-%d %H:%M:%S")
            downloaded_papers.append(paper)
            continue
        
        if not pdf_url:
            # Fallback to landing page URL
            pdf_url = paper.get('url')
        
        if not pdf_url:
            paper['download_status'] = 'failed'
            paper['download_error'] = 'No download or landing URL available'
            continue
        
        pdf_path = download_pdf(
            url=pdf_url,
            paper_id=paper_id,
            title=title,
            output_dir=output_dir
        )
        
        if pdf_path:
            paper['pdf_path'] = pdf_path
            paper['download_status'] = 'success'
            paper['download_date'] = time.strftime("%Y-%m-%d %H:%M:%S")
            downloaded_papers.append(paper)
        else:
            paper['download_status'] = 'failed'
            paper['download_error'] = 'Download failed or PDF was invalid'
    
    logger.info(f"Batch download complete: {len(downloaded_papers)} / {len(papers)} successful")
    return downloaded_papers


def save_download_metadata(
    papers: List[Dict[str, Any]],
    filename: str = "selected_papers.json"
) -> str:
    """Save metadata of downloaded papers to JSON file."""
    os.makedirs(METADATA_DIR, exist_ok=True)
    filepath = os.path.join(METADATA_DIR, filename)
    
    metadata = {
        "download_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_papers": len(papers),
        "papers": papers
    }
    
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        logger.info(f"Saved download metadata ({len(papers)} papers) to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save metadata to {filepath}: {e}")
        raise


def load_metadata(filepath: str) -> Dict[str, Any]:
    """Load paper metadata safely from JSON file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load metadata from {filepath}: {e}")
        raise


def get_pdf_info(filepath: str) -> Dict[str, Any]:
    """Get file size, checksum, and page count for a PDF."""
    try:
        if not os.path.exists(filepath):
            return {"is_valid": False, "error": "File not found"}
        
        file_size = os.path.getsize(filepath)
        md5_hash = hashlib.md5()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                md5_hash.update(chunk)
        
        page_count = 0
        try:
            import fitz
            doc = fitz.open(filepath)
            page_count = len(doc)
            doc.close()
        except Exception:
            pass
        
        return {
            "file_size_bytes": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "md5_hash": md5_hash.hexdigest(),
            "page_count": page_count,
            "is_valid": validate_pdf(filepath)
        }
    except Exception as e:
        logger.error(f"Error getting PDF info for {filepath}: {e}")
        return {"is_valid": False, "error": str(e)}
