"""
Security Module - Input Validation, Rate Limiting & Upload Security
===================================================================
Implements OWASP Top-10 protections including:
- Pydantic schema validation with strict types
- IP and user-based token-bucket rate limiting
- Strict PDF upload verification (magic bytes, size caps, extension checks)
- Input sanitization and prompt-injection defense
- Safe error handling without credential leakage
- Audit logging for security monitoring
"""

import logging
import os
import re
import time
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Union
from dataclasses import dataclass, field

# Setup logging for audit trail
LOG_DIR = "data/logs"
os.makedirs(LOG_DIR, exist_ok=True)

audit_handler = logging.FileHandler(os.path.join(LOG_DIR, "security_audit.log"), encoding="utf-8")
audit_handler.setFormatter(logging.Formatter(
    "%(asctime)s - SECURITY - %(levelname)s - %(message)s"
))

audit_logger = logging.getLogger("security.audit")
audit_logger.setLevel(logging.INFO)
if not audit_logger.handlers:
    audit_logger.addHandler(audit_handler)
    audit_logger.addHandler(logging.StreamHandler())


# =============================================================================
# PYDANTIC VALIDATION SCHEMAS
# =============================================================================

try:
    from pydantic import BaseModel, Field, field_validator, ConfigDict
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    audit_logger.warning("Pydantic not installed. Using fallback validation.")


if PYDANTIC_AVAILABLE:
    class SearchRequest(BaseModel):
        """Validated search request schema."""
        model_config = ConfigDict(strict=True, extra="forbid")
        
        topic: str = Field(
            ...,
            min_length=3,
            max_length=200,
            description="Research topic to search"
        )
        limit: int = Field(
            default=10,
            ge=1,
            le=30,
            description="Maximum papers to retrieve"
        )
        year_min: Optional[int] = Field(
            default=None,
            ge=1980,
            le=2035,
            description="Minimum publication year"
        )
        min_citations: Optional[int] = Field(
            default=None,
            ge=0,
            le=1000000,
            description="Minimum citation count"
        )
        top_n: int = Field(
            default=3,
            ge=1,
            le=15,
            description="Number of top papers to select"
        )
        
        @field_validator("topic")
        @classmethod
        def sanitize_topic(cls, v: str) -> str:
            sanitized = re.sub(r'[<>"\';\\`${}|]', '', v)
            sanitized = ' '.join(sanitized.split())
            return sanitized.strip()
    
    
    class PaperMetadata(BaseModel):
        """Validated paper metadata schema."""
        model_config = ConfigDict(extra="ignore")
        
        paper_id: str = Field(..., max_length=150)
        title: str = Field(..., max_length=500)
        authors: List[str] = Field(default_factory=list, max_length=100)
        abstract: Optional[str] = Field(default=None, max_length=25000)
        year: Optional[int] = Field(default=None, ge=1800, le=2035)
        citation_count: int = Field(default=0, ge=0)
        pdf_url: Optional[str] = Field(default=None, max_length=1000)
        pdf_available: bool = Field(default=False)
    
    
    class CritiqueRequest(BaseModel):
        """Validated critique request."""
        model_config = ConfigDict(extra="forbid")
        
        draft_id: Optional[str] = Field(default=None, max_length=150)
        sections: Optional[List[str]] = Field(default=None)
        
        @field_validator("sections")
        @classmethod
        def validate_sections(cls, v):
            if v is None:
                return v
            allowed = {
                "abstract", "introduction", "background", "thematic_organization",
                "methodology_comparison", "methodological_differences", "key_findings",
                "critical_discussion", "research_gaps", "future_directions", "conclusion", "references"
            }
            for section in v:
                if section.lower() not in allowed:
                    raise ValueError(f"Invalid section: {section}")
            return v


# =============================================================================
# RATE LIMITING
# =============================================================================

@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    requests_per_minute: int = 60
    requests_per_hour: int = 500
    burst_limit: int = 20
    cooldown_seconds: int = 60


class RateLimiter:
    """Token bucket rate limiter with IP and client tracking."""
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self._buckets: Dict[str, List[float]] = defaultdict(list)
        self._cooldowns: Dict[str, float] = {}
    
    def _get_client_id(self, ip: Optional[str] = None, user_id: Optional[str] = None) -> str:
        identifier = f"{ip or 'unknown'}:{user_id or 'anonymous'}"
        return hashlib.sha256(identifier.encode()).hexdigest()[:16]
    
    def _cleanup_old_requests(self, client_id: str) -> None:
        now = time.time()
        hour_ago = now - 3600
        self._buckets[client_id] = [ts for ts in self._buckets[client_id] if ts > hour_ago]
    
    def check_rate_limit(self, ip: Optional[str] = None, user_id: Optional[str] = None) -> Dict[str, Any]:
        client_id = self._get_client_id(ip, user_id)
        now = time.time()
        
        if client_id in self._cooldowns:
            cooldown_end = self._cooldowns[client_id]
            if now < cooldown_end:
                retry_after = int(cooldown_end - now)
                audit_logger.warning(f"Rate limit: Client {client_id} in cooldown for {retry_after}s")
                return {
                    "allowed": False,
                    "reason": "rate_limit_exceeded",
                    "remaining": 0,
                    "reset_at": datetime.fromtimestamp(cooldown_end).isoformat(),
                    "retry_after": retry_after
                }
            else:
                del self._cooldowns[client_id]
        
        self._cleanup_old_requests(client_id)
        requests = self._buckets[client_id]
        
        minute_ago = now - 60
        requests_last_minute = len([ts for ts in requests if ts > minute_ago])
        
        if requests_last_minute >= self.config.requests_per_minute:
            self._cooldowns[client_id] = now + self.config.cooldown_seconds
            audit_logger.warning(f"Rate limit exceeded: Client {client_id} - {requests_last_minute}/min")
            return {
                "allowed": False,
                "reason": "minute_limit_exceeded",
                "remaining": 0,
                "reset_at": datetime.fromtimestamp(now + 60).isoformat(),
                "retry_after": 60
            }
        
        if len(requests) >= self.config.requests_per_hour:
            audit_logger.warning(f"Rate limit exceeded: Client {client_id} - {len(requests)}/hour")
            return {
                "allowed": False,
                "reason": "hour_limit_exceeded",
                "remaining": 0,
                "reset_at": datetime.fromtimestamp(requests[0] + 3600).isoformat(),
                "retry_after": int(requests[0] + 3600 - now)
            }
        
        ten_seconds_ago = now - 10
        requests_burst = len([ts for ts in requests if ts > ten_seconds_ago])
        
        if requests_burst >= self.config.burst_limit:
            return {
                "allowed": False,
                "reason": "burst_limit_exceeded",
                "remaining": 0,
                "reset_at": datetime.fromtimestamp(now + 10).isoformat(),
                "retry_after": 10
            }
        
        self._buckets[client_id].append(now)
        remaining = self.config.requests_per_minute - requests_last_minute - 1
        
        return {
            "allowed": True,
            "remaining": max(0, remaining),
            "reset_at": datetime.fromtimestamp(now + 60).isoformat(),
            "retry_after": 0
        }


_rate_limiter = RateLimiter()


def rate_limit(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        ip = kwargs.pop("client_ip", None)
        user_id = kwargs.pop("user_id", None)
        result = _rate_limiter.check_rate_limit(ip=ip, user_id=user_id)
        if not result["allowed"]:
            raise RateLimitError(
                f"Rate limit exceeded. Retry after {result['retry_after']} seconds.",
                retry_after=result["retry_after"]
            )
        return func(*args, **kwargs)
    return wrapper


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = 429


# =============================================================================
# INPUT SANITIZATION & UPLOAD SECURITY
# =============================================================================

def sanitize_string(value: str, max_length: int = 1000, allow_html: bool = False) -> str:
    """Sanitize string input."""
    if not isinstance(value, str):
        value = str(value)
    value = value[:max_length]
    value = value.replace('\x00', '')
    if not allow_html:
        value = re.sub(r'<[^>]+>', '', value)
        value = value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    return value.strip()


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal."""
    filename = os.path.basename(filename)
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
    filename = filename.strip(' ._')
    return filename[:180] or "paper"


def validate_file_upload(file_path: str, max_size_mb: float = 40.0) -> Dict[str, Any]:
    """
    Validate uploaded research paper file for security:
    - Path traversal prevention
    - File size constraints
    - Double extension mitigation
    - Magic header verification (%PDF)
    """
    if not file_path or not os.path.exists(file_path):
        return {"valid": False, "error": "Uploaded file does not exist on disk."}
    
    norm_path = os.path.abspath(file_path)
    base_name = os.path.basename(norm_path).lower()
    
    dangerous_exts = [".exe", ".bat", ".cmd", ".sh", ".py", ".js", ".vbs", ".dll", ".ps1"]
    if any(base_name.endswith(ext) or ext + "." in base_name for ext in dangerous_exts):
        audit_logger.warning(f"Security Alert: Malicious file upload rejected: {base_name}")
        return {"valid": False, "error": "Invalid file type. Only academic PDF documents are allowed."}
        
    if not base_name.endswith(".pdf"):
        return {"valid": False, "error": "Only .pdf files are accepted."}
        
    file_size = os.path.getsize(norm_path)
    if file_size > max_size_mb * 1024 * 1024:
        return {"valid": False, "error": f"File exceeds maximum permissible size of {max_size_mb} MB."}
    if file_size < 100:
        return {"valid": False, "error": "File is empty or corrupted."}
        
    try:
        with open(norm_path, "rb") as f:
            header = f.read(5)
            if not header.startswith(b"%PDF"):
                audit_logger.warning(f"Magic header check failed for {base_name}")
                return {"valid": False, "error": "File is not a valid PDF document."}
    except Exception as e:
        return {"valid": False, "error": f"Error inspecting file: {str(e)}"}
        
    audit_logger.info(f"File upload validated: {base_name} ({file_size} bytes)")
    return {"valid": True, "file_path": norm_path, "file_size": file_size}


def validate_search_input(
    topic: str,
    limit: int = 10,
    year_min: Optional[int] = None,
    min_citations: Optional[int] = None,
    top_n: int = 3
) -> Dict[str, Any]:
    """Validate and sanitize search input."""
    if PYDANTIC_AVAILABLE:
        try:
            req = SearchRequest(
                topic=topic,
                limit=limit,
                year_min=year_min,
                min_citations=min_citations,
                top_n=top_n
            )
            audit_logger.info(f"Input validated: topic='{req.topic[:50]}'")
            return req.model_dump()
        except Exception as e:
            audit_logger.warning(f"Validation failed: {str(e)}")
            raise ValueError(f"Invalid input: {str(e)}")
    else:
        if not topic or len(topic) < 3:
            raise ValueError("Topic must be at least 3 characters")
        if len(topic) > 200:
            raise ValueError("Topic must be at most 200 characters")
        
        topic = sanitize_string(topic, max_length=200)
        limit = max(1, min(30, int(limit or 10)))
        top_n = max(1, min(15, int(top_n or 3)))
        
        if year_min is not None:
            year_min = max(1980, min(2035, int(year_min)))
        if min_citations is not None:
            min_citations = max(0, min(1000000, int(min_citations)))
            
        return {
            "topic": topic,
            "limit": limit,
            "year_min": year_min,
            "min_citations": min_citations,
            "top_n": top_n
        }


# =============================================================================
# PROMPT INJECTION DEFENSE
# =============================================================================

INJECTION_PATTERNS = [
    r"ignore\s+previous\s+instructions",
    r"disregard\s+all\s+prior",
    r"system\s*:\s*",
    r"<\s*script",
    r"\{\{.*\}\}",
    r"\$\{.*\}",
    r"exec\s*\(",
    r"eval\s*\(",
    r"__import__",
    r"os\.system",
    r"subprocess",
]


def detect_prompt_injection(text: str) -> bool:
    """Detect prompt injection payloads in user-submitted strings."""
    if not text:
        return False
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            audit_logger.warning(f"Prompt injection pattern detected: '{pattern}'")
            return True
    return False


def safe_format(template: str, **kwargs) -> str:
    """Safely format template string preventing format-string injection attacks."""
    result = template
    for key, value in kwargs.items():
        placeholder = "{" + key + "}"
        safe_value = sanitize_string(str(value))
        result = result.replace(placeholder, safe_value)
    return result


# =============================================================================
# SECRETS HANDLING
# =============================================================================

def get_api_key(key_name: str, required: bool = False) -> Optional[str]:
    """Retrieve API key from environment variables without logging key values."""
    value = os.environ.get(key_name)
    if required and not value:
        audit_logger.error(f"Required environment variable missing: {key_name}")
        raise EnvironmentError(f"Required environment variable {key_name} is not set.")
    if value:
        audit_logger.info(f"API key accessed: {key_name}")
    return value


def mask_sensitive(value: str, visible_chars: int = 4) -> str:
    """Mask sensitive string for audit logs."""
    if not value or len(value) <= visible_chars:
        return "****"
    return value[:visible_chars] + "*" * max(4, len(value) - visible_chars)


# =============================================================================
# SAFE ERROR HANDLING
# =============================================================================

class SafeError(Exception):
    def __init__(self, message: str, code: str = "UNKNOWN_ERROR", status: int = 500):
        super().__init__(message)
        self.code = code
        self.status = status
        self.timestamp = datetime.now().isoformat()


def handle_error_safely(error: Exception, context: str = "") -> Dict[str, Any]:
    """Handle errors securely without leaking internal traceback or credentials."""
    audit_logger.error(f"Error in {context}: {type(error).__name__}: {str(error)}")
    
    if isinstance(error, SafeError):
        return {"error": True, "code": error.code, "message": str(error), "status": error.status}
    elif isinstance(error, RateLimitError):
        return {"error": True, "code": "RATE_LIMIT_EXCEEDED", "message": "Too many requests. Please wait.", "status": 429, "retry_after": error.retry_after}
    elif isinstance(error, ValueError):
        return {"error": True, "code": "VALIDATION_ERROR", "message": str(error), "status": 400}
    else:
        return {"error": True, "code": "INTERNAL_ERROR", "message": "An internal error occurred during research processing.", "status": 500}


def log_access(action: str, user_id: Optional[str] = None, ip: Optional[str] = None, details: Optional[Dict] = None):
    audit_logger.info(f"ACCESS: {action} by {user_id or 'anonymous'} from {ip or 'local'}")


def log_security_event(event_type: str, severity: str, details: Dict):
    audit_logger.warning(f"SECURITY [{severity.upper()}]: {event_type} - {details}")


def security_middleware(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            rate_result = _rate_limiter.check_rate_limit()
            if not rate_result["allowed"]:
                return {"error": True, "message": f"Rate limit reached. Retry in {rate_result['retry_after']}s"}
            log_access(func.__name__)
            return func(*args, **kwargs)
        except Exception as e:
            return handle_error_safely(e, context=func.__name__)
    return wrapper


__all__ = [
    "SearchRequest",
    "PaperMetadata",
    "CritiqueRequest",
    "RateLimiter",
    "RateLimitError",
    "rate_limit",
    "sanitize_string",
    "sanitize_filename",
    "validate_file_upload",
    "validate_search_input",
    "detect_prompt_injection",
    "safe_format",
    "get_api_key",
    "mask_sensitive",
    "SafeError",
    "handle_error_safely",
    "log_access",
    "log_security_event",
    "security_middleware"
]
