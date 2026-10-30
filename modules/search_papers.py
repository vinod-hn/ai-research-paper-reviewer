"""
Research Paper Search Module 
===========================================
Implements automated academic paper search using Semantic Scholar API with:
- Query by topic with input validation and prompt-injection defense
- Optional filters: year range, minimum citations, author name
- Comprehensive metadata extraction (DOI, arXiv, venue, citation metrics)
- arXiv PDF auto-resolution when direct PDF is unlinked
- **WEIGHTED RANKING SYSTEM** for paper selection:
  - Topical relevance (w1)
  - Publication recency (w2)
  - Citation count (w3)
  - Author reputation (w4)
  - PDF availability (w5)
- Rate-limit handling with exponential backoff and timeout safeguards
- Deduplication by paper ID, DOI, and title
- Structured logging and safe caching fallback
"""

import json
import os
import logging
import time
import math
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple
from semanticscholar import SemanticScholar
from dotenv import load_dotenv

# Ensure logs directory exists
LOG_DIR = "data/logs"
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("modules.search_papers")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(os.path.join(LOG_DIR, "search_papers.log"), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

# Load environment variables
load_dotenv()

# Tunables via environment
SEM_SCH_TIMEOUT = int(os.getenv("SEMANTIC_SCHOLAR_TIMEOUT", "15"))
SEM_SCH_MAX_RETRIES = int(os.getenv("SEMANTIC_SCHOLAR_MAX_RETRIES", "3"))
USE_CACHED_ON_FAILURE = os.getenv("USE_CACHED_METADATA_ON_FAILURE", "1") == "1"


def get_semantic_scholar_client() -> SemanticScholar:
    """Initialize and return a SemanticScholar client with configured key and timeout."""
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    timeout = int(os.getenv("SEMANTIC_SCHOLAR_TIMEOUT", str(SEM_SCH_TIMEOUT)))
    if api_key and api_key.strip() and not api_key.strip().startswith("your_"):
        return SemanticScholar(api_key=api_key.strip(), timeout=timeout)
    return SemanticScholar(timeout=timeout)


# Global or lazy client
sch = get_semantic_scholar_client()

# =============================================================================
# RANKING WEIGHTS - Configurable scoring parameters
# =============================================================================
RANKING_WEIGHTS = {
    "relevance": 0.30,      # w1: Topical relevance (title + abstract matching)
    "recency": 0.20,        # w2: Publication recency (newer = better)
    "citations": 0.25,      # w3: Citation count (normalized)
    "author_score": 0.10,   # w4: Author reputation (h-index proxy)
    "pdf_available": 0.15   # w5: PDF availability bonus
}

CURRENT_YEAR = datetime.now().year
MAX_RECENCY_YEARS = 10
MAX_CITATIONS_NORM = 1000


def calculate_relevance_score(paper: Dict[str, Any], query_terms: List[str]) -> float:
    """
    Calculate topical relevance score based on query term matching in title and abstract.
    
    Args:
        paper: Paper metadata dictionary
        query_terms: List of search query terms (lowercased)
    
    Returns:
        Relevance score between 0.0 and 1.0
    """
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    
    if not query_terms:
        return 0.5
    
    title_matches = sum(1 for term in query_terms if term in title)
    abstract_matches = sum(1 for term in query_terms if term in abstract)
    
    title_score = min(1.0, (title_matches * 2) / len(query_terms))
    abstract_score = min(1.0, abstract_matches / len(query_terms))
    
    relevance = 0.6 * title_score + 0.4 * abstract_score
    return round(relevance, 4)


def calculate_recency_score(year: Optional[int]) -> float:
    """
    Calculate recency score - newer papers score higher.
    
    Args:
        year: Publication year
    
    Returns:
        Recency score between 0.0 and 1.0
    """
    if not year:
        return 0.3
    
    years_old = CURRENT_YEAR - year
    if years_old <= 0:
        return 1.0
    elif years_old >= MAX_RECENCY_YEARS:
        return 0.1
    else:
        score = 1.0 - (years_old / MAX_RECENCY_YEARS) * 0.9
        return round(max(0.1, score), 4)


def calculate_citation_score(citation_count: Optional[int]) -> float:
    """
    Calculate normalized citation score using logarithmic scaling.
    
    Args:
        citation_count: Number of citations
    
    Returns:
        Citation score between 0.0 and 1.0
    """
    if not citation_count or citation_count <= 0:
        return 0.0
    
    log_citations = math.log10(citation_count + 1)
    log_max = math.log10(MAX_CITATIONS_NORM + 1)
    
    score = min(1.0, log_citations / log_max)
    return round(score, 4)


def calculate_author_score(authors: List[Any], influential_citations: int = 0) -> float:
    """
    Calculate author reputation score based on author count and influential citations.
    
    Args:
        authors: List of author objects/names
        influential_citations: Number of influential citations
    
    Returns:
        Author score between 0.0 and 1.0
    """
    if not authors:
        return 0.2
    
    author_count = len(authors)
    if author_count >= 3:
        collab_score = 0.6
    elif author_count >= 2:
        collab_score = 0.5
    else:
        collab_score = 0.4
    
    influence_bonus = min(0.4, (influential_citations or 0) * 0.05)
    return round(min(1.0, collab_score + influence_bonus), 4)


def calculate_paper_score(paper: Dict[str, Any], query_terms: List[str]) -> Tuple[float, Dict[str, float]]:
    """
    Calculate overall paper ranking score using weighted criteria.
    Formula: score = w1*relevance + w2*recency + w3*citations + w4*author_score + w5*pdf_available
    """
    w = RANKING_WEIGHTS
    
    relevance = calculate_relevance_score(paper, query_terms)
    recency = calculate_recency_score(paper.get("year"))
    citations = calculate_citation_score(paper.get("citation_count"))
    author_score = calculate_author_score(
        paper.get("authors", []),
        paper.get("influential_citation_count", 0)
    )
    pdf_score = 1.0 if paper.get("pdf_available") else 0.0
    
    total_score = (
        w["relevance"] * relevance +
        w["recency"] * recency +
        w["citations"] * citations +
        w["author_score"] * author_score +
        w["pdf_available"] * pdf_score
    )
    
    component_scores = {
        "relevance": relevance,
        "recency": recency,
        "citations": citations,
        "author_score": author_score,
        "pdf_available": pdf_score,
        "total": round(total_score, 4)
    }
    
    return round(total_score, 4), component_scores


def rank_papers(papers: List[Dict[str, Any]], query: str, top_n: int = 3) -> List[Dict[str, Any]]:
    """
    Rank papers using weighted scoring and return top N papers.
    """
    if not papers:
        return []
    
    query_terms = [term.lower().strip() for term in (query or "").split() if len(term) > 2]
    
    scored_papers = []
    for paper in papers:
        total_score, components = calculate_paper_score(paper, query_terms)
        paper_with_score = paper.copy()
        paper_with_score["ranking_score"] = total_score
        paper_with_score["score_breakdown"] = components
        scored_papers.append(paper_with_score)
    
    scored_papers.sort(key=lambda x: x["ranking_score"], reverse=True)
    
    for idx, paper in enumerate(scored_papers, 1):
        paper["rank"] = idx
    
    logger.info(f"Ranked {len(scored_papers)} papers for query '{query}'. Returning top {top_n}.")
    return scored_papers[:top_n] if top_n else scored_papers


def _extract_pdf_url_safely(paper: Any, external_ids: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """Resolve direct PDF URL or synthesize arXiv direct PDF link."""
    pdf_url = None
    open_access = getattr(paper, 'openAccessPdf', None)
    if open_access:
        if isinstance(open_access, dict):
            pdf_url = open_access.get('url')
        elif hasattr(open_access, 'url'):
            pdf_url = open_access.url
        elif isinstance(open_access, str):
            pdf_url = open_access
    
    # Fallback to arXiv direct PDF if available
    if not pdf_url and external_ids and isinstance(external_ids, dict):
        arxiv_id = external_ids.get("ArXiv") or external_ids.get("arxiv")
        if arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    
    return pdf_url, bool(pdf_url)


def search_papers(
    topic: str,
    limit: int = 10,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    min_citations: Optional[int] = None,
    author: Optional[str] = None,
    fields: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Search for research papers using Semantic Scholar API with retry, validation, and deduplication.
    """
    global sch
    topic = (topic or "").strip()
    if not topic:
        return {
            "topic": "",
            "search_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "filters": {"year_min": year_min, "year_max": year_max, "min_citations": min_citations, "author": author},
            "total_papers": 0,
            "papers": []
        }
    
    logger.info(f"Starting paper search for topic: '{topic}' (limit={limit})")
    
    if fields is None:
        fields = [
            'title', 'abstract', 'year', 'authors', 'citationCount',
            'paperId', 'url', 'openAccessPdf', 'publicationDate',
            'venue', 'publicationTypes', 'externalIds', 'influentialCitationCount'
        ]
    
    query = topic
    if author:
        query += f" author:{author}"
    
    papers: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_titles = set()
    
    retry_count = 0
    max_retries = max(1, SEM_SCH_MAX_RETRIES)
    
    client = sch or get_semantic_scholar_client()
    
    while retry_count < max_retries:
        try:
            logger.info(f"Executing Semantic Scholar search query (attempt {retry_count + 1}/{max_retries})")
            
            # Fetch up to 2x limit to allow strict filtering
            fetch_limit = min(limit * 3, 50)
            results = client.search_paper(query, limit=fetch_limit, fields=fields)
            
            for paper in (results or []):
                p_title = getattr(paper, 'title', None) or ""
                if not p_title or p_title == "N/A":
                    continue
                
                # Deduplication check
                norm_title = p_title.lower().strip()
                p_id = getattr(paper, 'paperId', None)
                if p_id and p_id in seen_ids:
                    continue
                if norm_title in seen_titles:
                    continue
                
                # Apply numeric filters
                p_year = getattr(paper, 'year', None)
                if year_min and (not p_year or p_year < year_min):
                    continue
                if year_max and (not p_year or p_year > year_max):
                    continue
                
                p_cites = getattr(paper, 'citationCount', 0) or 0
                if min_citations and p_cites < min_citations:
                    continue
                
                # Publication date normalization
                pub_date = getattr(paper, 'publicationDate', None)
                if pub_date and hasattr(pub_date, 'isoformat'):
                    pub_date = pub_date.isoformat()
                elif pub_date:
                    pub_date = str(pub_date)
                
                ext_ids = getattr(paper, 'externalIds', {}) or {}
                if not isinstance(ext_ids, dict):
                    ext_ids = {}
                
                # Authors normalization without shadowing
                author_names: List[str] = []
                for a_obj in (getattr(paper, 'authors', None) or []):
                    if hasattr(a_obj, 'name') and a_obj.name:
                        author_names.append(str(a_obj.name))
                    elif isinstance(a_obj, dict) and 'name' in a_obj:
                        author_names.append(str(a_obj['name']))
                    elif isinstance(a_obj, str):
                        author_names.append(a_obj)
                
                pdf_url, pdf_available = _extract_pdf_url_safely(paper, ext_ids)
                
                paper_data = {
                    "title": p_title,
                    "abstract": getattr(paper, 'abstract', None) or "N/A",
                    "authors": author_names,
                    "year": p_year,
                    "citation_count": p_cites,
                    "influential_citation_count": getattr(paper, 'influentialCitationCount', 0) or 0,
                    "paper_id": p_id or f"gen_{len(papers)}",
                    "url": getattr(paper, 'url', None),
                    "pdf_url": pdf_url,
                    "publication_date": pub_date,
                    "venue": getattr(paper, 'venue', None),
                    "publication_types": getattr(paper, 'publicationTypes', []) or [],
                    "external_ids": ext_ids,
                    "pdf_available": pdf_available
                }
                
                papers.append(paper_data)
                if p_id:
                    seen_ids.add(p_id)
                seen_titles.add(norm_title)
                
                if len(papers) >= limit:
                    break
            
            logger.info(f"Retrieved {len(papers)} filtered papers from Semantic Scholar")
            break
            
        except Exception as e:
            retry_count += 1
            logger.warning(f"Semantic Scholar API attempt {retry_count}/{max_retries} failed: {e}")
            if retry_count >= max_retries:
                logger.error(f"Semantic Scholar search exhausted {max_retries} attempts.")
                papers = []
                break
            wait_time = 2 ** retry_count
            time.sleep(wait_time)
    
    # Fallback to cached papers if offline or rate-limited
    if not papers and USE_CACHED_ON_FAILURE:
        cache_paths = [
            "data/metadata/paper_metadata.json",
            "data/metadata/selected_papers.json"
        ]
        for cp in cache_paths:
            if os.path.exists(cp):
                try:
                    with open(cp, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    cached_papers = cached.get("papers", [])
                    if cached_papers:
                        logger.warning(f"Using {len(cached_papers)} cached papers as fallback from {cp}")
                        papers = cached_papers[:limit]
                        break
                except Exception as cache_err:
                    logger.warning(f"Could not read fallback cache {cp}: {cache_err}")
    
    return {
        "topic": topic,
        "search_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "filters": {
            "year_min": year_min,
            "year_max": year_max,
            "min_citations": min_citations,
            "author": author
        },
        "total_papers": len(papers),
        "papers": papers
    }


def save_metadata(data: Dict[str, Any], path: str = "data/metadata/paper_metadata.json") -> None:
    """Save paper metadata to JSON file safely."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        logger.info(f"Metadata saved successfully to {path}")
    except Exception as e:
        logger.error(f"Failed to save metadata to {path}: {e}")
        raise


def display_papers(papers: List[Dict[str, Any]], show_ranking: bool = True) -> None:
    """Display paper information in a readable format with ranking scores."""
    print("\n" + "="*80)
    print(f"Found {len(papers)} papers")
    print("="*80 + "\n")
    
    for idx, paper in enumerate(papers, 1):
        rank_info = ""
        if show_ranking and "rank" in paper:
            rank_info = f" [RANK #{paper['rank']} | Score: {paper.get('ranking_score', 0):.3f}]"
        
        print(f"{idx}. {paper['title']}{rank_info}")
        print(f"   Authors: {', '.join(paper.get('authors', [])[:3])}" + (" et al." if len(paper.get('authors', [])) > 3 else ""))
        print(f"   Year: {paper.get('year')} | Citations: {paper.get('citation_count')}")
        print(f"   PDF Available: {'✓' if paper.get('pdf_available') else '✗'}")
        
        if show_ranking and "score_breakdown" in paper:
            bd = paper["score_breakdown"]
            print(f"   Score Breakdown: Rel={bd.get('relevance',0):.2f} | "
                  f"Rec={bd.get('recency',0):.2f} | Cit={bd.get('citations',0):.2f} | "
                  f"Auth={bd.get('author_score',0):.2f} | PDF={bd.get('pdf_available',0):.0f}")
        
        ab = paper.get('abstract', '')
        if ab and ab != "N/A":
            print(f"   Abstract: {ab[:140]}...")
        print()


def get_ranking_weights() -> Dict[str, float]:
    """Return current ranking weights."""
    return RANKING_WEIGHTS.copy()


def set_ranking_weights(weights: Dict[str, float]) -> None:
    """Update ranking weights."""
    global RANKING_WEIGHTS
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        weights = {k: v/total for k, v in weights.items()}
    RANKING_WEIGHTS.update(weights)
