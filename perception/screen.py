"""Screen Perception layer combining window context and structured OCR data."""

from dataclasses import dataclass, field
import re
import time
from typing import Any, Dict, List, Optional


@dataclass
class ScreenPerception:
    """Combines active window context and structured OCR results into a unified perception."""

    window_info: Optional[Dict[str, Any]] = None
    ocr_data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def app(self) -> Optional[str]:
        return (self.window_info or {}).get("app")

    @property
    def caption(self) -> Optional[str]:
        return (self.window_info or {}).get("caption")

    @property
    def uuid(self) -> Optional[str]:
        return (self.window_info or {}).get("uuid")

    @property
    def full_text(self) -> str:
        return self.ocr_data.get("full_text", "")

    @property
    def lines(self) -> List[Dict[str, Any]]:
        return self.ocr_data.get("lines", [])

    @property
    def words(self) -> List[Dict[str, Any]]:
        return self.ocr_data.get("words", [])

    def find_text(self, query: str, case_sensitive: bool = False) -> List[Dict[str, Any]]:
        """Find lines or words containing query."""
        results = []
        target = query if case_sensitive else query.lower()

        for item in self.lines + self.words:
            t = item["text"] if case_sensitive else item["text"].lower()
            if target in t:
                results.append(item)

        return results

    def extract_filenames(self) -> List[str]:
        """Extract detected filenames and file extensions (e.g. .py, .txt, .json, .md)."""
        pattern = re.compile(r"[\w\-\.]+\.(?:py|txt|json|md|js|ts|tsx|html|css|cpp|h|sh)", re.IGNORECASE)
        found = set()
        for word in self.words:
            matches = pattern.findall(word["text"])
            for m in matches:
                # clean leading/trailing punctuation
                clean = m.strip("()[]{},;:'\"")
                if "." in clean and not clean.startswith("."):
                    found.add(clean)
        return sorted(found)

    def format_gemini_context(self, max_lines: int = 25) -> str:
        """Format a concise, high-value text summary for Gemini Live reasoning."""
        parts = []

        # 1. Active Window Metadata
        app_name = self.app or "Unknown App"
        title = self.caption or "Untitled"
        parts.append(f"[Active Window] {title} ({app_name})")

        # 2. Key Filenames / Identifiers if detected
        filenames = self.extract_filenames()
        if filenames:
            parts.append(f"[Detected Files/Tabs] {', '.join(filenames)}")

        # 3. Formatted Lines (deduplicated, non-empty, limited)
        clean_lines = []
        seen = set()
        for line in self.lines:
            txt = line["text"].strip()
            if txt and len(txt) > 2 and txt not in seen:
                seen.add(txt)
                clean_lines.append(txt)

        if clean_lines:
            sample = clean_lines[:max_lines]
            parts.append("[Screen Text Content (OCR)]\n" + "\n".join(f"- {l}" for l in sample))
            if len(clean_lines) > max_lines:
                parts.append(f"... ({len(clean_lines) - max_lines} more lines)")

        return "\n\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize perception data to dictionary."""
        return {
            "window_info": self.window_info,
            "timestamp": self.timestamp,
            "ocr_data": self.ocr_data,
            "filenames": self.extract_filenames(),
        }


def create_screen_perception(
    window_info: Optional[Dict[str, Any]],
    ocr_data: Dict[str, Any],
    timestamp: Optional[float] = None,
) -> ScreenPerception:
    """Factory helper to build a ScreenPerception instance."""
    return ScreenPerception(
        window_info=window_info,
        ocr_data=ocr_data,
        timestamp=timestamp if timestamp is not None else time.time(),
    )
