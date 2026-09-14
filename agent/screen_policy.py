"""Screen Access Policy for Qubit 4.0.

Provides strict, demand-driven access gating for screen capture (ScreenCast,
XDG screenshots, OCR, and Gemini Vision). Screen capture is prevented during
unrelated conversation and only activated when screen information is explicitly
required by the user's request or by a screen-dependent tool/action.
"""

import logging
import re
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Default query time-to-live: queries older than 60s are considered expired
DEFAULT_QUERY_TTL = 60.0

# Pre-registered screen-dependent tools and actions
DEFAULT_SCREEN_DEPENDENT_TOOLS: Set[str] = {
    "click_element",
    "mouse_click",
    "double_click",
    "right_click",
    "drag_and_drop",
    "type_text",
    "find_text",
    "find_element",
    "inspect_ui",
    "inspect_screen",
    "read_screen_text",
    "computer_control",
    "screen_action",
    "ui_action",
}

# Negative patterns: queries that are clearly general conversation / non-visual
GENERAL_CONVERSATION_PATTERNS = [
    r"^(what('s| is) the weather|weather today|how is the weather)",
    r"^(tell me a joke|make me laugh)",
    r"^(who (is|was|are)|what is the capital|tell me about)",
    r"^(how (do|does|can) (i|we|one) (code|write|implement|calculate))",
    r"^(write (a|an) (python|javascript|code|script|essay|email|poem))",
    r"^(calculate|what is \d+|solve)",
    r"^(hello|hi|hey|good morning|good afternoon|good evening|how are you)",
    r"^(what time is it|current time|today('s)? date)",
    r"^(explain (how|why|what is) (quantum|gravity|relativity|ai|machine learning|history))",
]

# Explicit screen observation intent patterns (English and Tamil)
SCREEN_QUERY_PATTERNS = [
    # Explicit screen / window / display references
    r"\b(what('s| is)|what do you see|what are we looking at) (on|in) (my |the )?(screen|display|monitor|window|desktop)\b",
    r"\b(look at|see|check|inspect|view|read|observe)\b.*?\b(screen|display|monitor|window|desktop|code|error|terminal|page|tab|document)\b",
    r"\b(can you see|do you see|tell me what is on)\b.*?\b(screen|display|window|desktop)\b",
    r"\b(read|extract|what does (it|the (screen|window|error|terminal|text)) say)\b",
    r"\b(what error|what('s| is) the error|error on (the |my )?screen|error message)\b",
    r"\b(take a screenshot|capture (the |my )?screen|fresh screenshot)\b",
    r"\b(summarize|summary|describe|explain)\b.*?\b(content|contents)\b.*?\b(active|current)?\s*(screen|window|display|desktop)\b",
    # Visual deictic references ("look at this", "what is this", "see this")
    r"\b(look at this|look here|see this|what is this|what does this say|why is this (red|failing|broken|highlighted))\b",
    # Tamil screen references
    r"\b(screen|window|display)\s*(la|le)?\s*(enna|paaru|paakanum|theriyudha)\b",
    r"\b(idha|ida)\s*(paaru|padi|ennannu sollu)\b",
]

# Screen-dependent action intent patterns (interacting with visible UI elements)
SCREEN_ACTION_PATTERNS = [
    r"\b(click|press|tap|select|choose|double[ -]click|right[ -]click)\b.*?\b(button|icon|link|menu|tab|option|item|checkbox|switch|field|input|dropdown|ui|window)\b",
    r"\b(click|press|tap|select)\s+['\"“]?[a-zA-Z0-9_ -]{2,}['\"”]?\b",
    r"\b(find|locate|where is)\b.*?\b(button|link|icon|text|field|menu)\b",
    r"\b(type|enter|fill)\b.*?\b(into|in the)\b.*?\b(field|box|input|bar)\b",
    r"\b(scroll|swipe)\s+(up|down|left|right)\b",
]


class ScreenAccessPolicy:
    """Manages strictly demand-driven permission gating for screen capture."""

    def __init__(self, query_ttl: float = DEFAULT_QUERY_TTL):
        self.query_ttl = query_ttl
        self._last_user_query: Optional[str] = None
        self._last_user_query_time: float = 0.0
        self._interactive_mode: bool = False
        self._test_mode: bool = False

        # Registered screen-dependent tool names
        self._screen_dependent_tools: Set[str] = set(DEFAULT_SCREEN_DEPENDENT_TOOLS)

        # Active programmatic grants: list of dicts with {"requester", "reason", "expires_at"}
        self._active_grants: List[Dict[str, Any]] = []

        # Currently executing tool context
        self._current_tool_name: Optional[str] = None
        self._current_tool_args: Optional[Dict[str, Any]] = None

    def set_interactive_mode(self, enabled: bool) -> None:
        """Enable or disable interactive conversation mode.

        In interactive mode, all screen captures must have valid justification
        (user intent, screen-dependent tool, or programmatic grant).
        """
        self._interactive_mode = enabled
        logger.debug(f"ScreenAccessPolicy interactive_mode set to {enabled}")

    @property
    def is_interactive(self) -> bool:
        """Return whether interactive mode is currently active."""
        return self._interactive_mode

    def set_test_mode(self, enabled: bool) -> None:
        """Enable or disable test bypass mode for unit tests."""
        self._test_mode = enabled

    def record_user_query(self, query: str) -> None:
        """Record the latest user utterance/query with the current timestamp."""
        if query and query.strip():
            self._last_user_query = query.strip()
            self._last_user_query_time = time.time()
            logger.debug(f"Recorded user query in screen policy: '{self._last_user_query}'")

    def get_last_user_query(self) -> Optional[Tuple[str, float]]:
        """Return the latest user query and timestamp if still within TTL."""
        if not self._last_user_query:
            return None
        elapsed = time.time() - self._last_user_query_time
        if elapsed > self.query_ttl:
            logger.debug(f"User query expired ({elapsed:.1f}s > {self.query_ttl}s)")
            return None
        return (self._last_user_query, self._last_user_query_time)

    def clear_user_query(self) -> None:
        """Clear the stored user query."""
        self._last_user_query = None
        self._last_user_query_time = 0.0

    def register_screen_dependent_tool(self, tool_name: str) -> None:
        """Register a tool name as screen-dependent."""
        self._screen_dependent_tools.add(tool_name.lower().strip())
        logger.debug(f"Registered screen-dependent tool: '{tool_name}'")

    def unregister_screen_dependent_tool(self, tool_name: str) -> None:
        """Remove a tool from screen-dependent registration."""
        self._screen_dependent_tools.discard(tool_name.lower().strip())

    def is_tool_screen_dependent(self, tool_name: Optional[str]) -> bool:
        """Check if a tool is registered as screen-dependent."""
        if not tool_name:
            return False
        return tool_name.lower().strip() in self._screen_dependent_tools

    @contextmanager
    def active_tool_context(self, tool_name: str, args: Optional[Dict[str, Any]] = None):
        """Context manager tracking the currently executing tool."""
        prev_tool = self._current_tool_name
        prev_args = self._current_tool_args
        self._current_tool_name = tool_name
        self._current_tool_args = args or {}
        try:
            yield
        finally:
            self._current_tool_name = prev_tool
            self._current_tool_args = prev_args

    @contextmanager
    def grant_access(self, requester: str, reason: str, ttl_seconds: float = 30.0):
        """Context manager granting temporary screen access for a block of code."""
        grant = {
            "requester": requester,
            "reason": reason,
            "expires_at": time.time() + ttl_seconds,
        }
        self._active_grants.append(grant)
        logger.debug(f"Screen access granted to '{requester}' ({reason}) for {ttl_seconds}s")
        try:
            yield grant
        finally:
            if grant in self._active_grants:
                self._active_grants.remove(grant)
            logger.debug(f"Screen access grant released for '{requester}'")

    def request_screen_access(self, requester: str, reason: str, ttl_seconds: float = 10.0) -> None:
        """Grant a temporary screen access lease for the specified TTL."""
        grant = {
            "requester": requester,
            "reason": reason,
            "expires_at": time.time() + ttl_seconds,
        }
        self._active_grants.append(grant)
        logger.debug(f"Explicit screen access requested and granted: {requester} ({reason})")

    def _cleanup_expired_grants(self) -> None:
        """Remove any grants whose TTL has elapsed."""
        now = time.time()
        self._active_grants = [g for g in self._active_grants if g["expires_at"] > now]

    def is_screen_query(self, query: str) -> Tuple[bool, str]:
        """Evaluate whether a query represents a visual screen observation request.

        Returns:
            Tuple[bool, str]: (is_match, reason)
        """
        if not query:
            return False, "Empty query"

        clean = query.strip().lower()

        # Check negative exclusion patterns first
        for pat in GENERAL_CONVERSATION_PATTERNS:
            if re.search(pat, clean, re.IGNORECASE):
                return False, f"Matched general conversation pattern: {pat}"

        # Check positive screen query patterns
        for pat in SCREEN_QUERY_PATTERNS:
            if re.search(pat, clean, re.IGNORECASE):
                return True, f"Matched screen observation pattern: {pat}"

        return False, "Query does not indicate screen observation intent"

    def is_screen_action(self, query: str) -> Tuple[bool, str]:
        """Evaluate whether a query represents an on-screen element action intent.

        Returns:
            Tuple[bool, str]: (is_match, reason)
        """
        if not query:
            return False, "Empty query"

        clean = query.strip().lower()

        for pat in SCREEN_ACTION_PATTERNS:
            if re.search(pat, clean, re.IGNORECASE):
                return True, f"Matched screen action pattern: {pat}"

        return False, "Query does not indicate screen action intent"

    def evaluate_intent(self, query: str) -> Tuple[bool, str]:
        """Multi-factor intent evaluation combining query, deictic, and action analysis.

        Returns:
            Tuple[bool, str]: (is_allowed, detailed_reason)
        """
        if not query:
            return False, "No query text provided"

        # 1. Direct screen inspection query
        is_query, q_reason = self.is_screen_query(query)
        if is_query:
            return True, f"Screen observation intent: {q_reason}"

        # 2. Screen-dependent action query (e.g. 'Click Settings button')
        is_action, a_reason = self.is_screen_action(query)
        if is_action:
            return True, f"Screen-dependent action intent: {a_reason}"

        return False, "Query represents general conversation without visual screen dependency"

    def evaluate_access(
        self,
        user_query: Optional[str] = None,
        requester: Optional[str] = None,
        action: Optional[str] = None,
        force: bool = False,
    ) -> Tuple[bool, str]:
        """Master policy gate determining whether screen capture is permitted.

        Screen capture is allowed ONLY IF:
        1. Test mode is enabled.
        2. An active programmatic grant exists (e.g. from grant_access or request_screen_access).
        3. The currently executing tool is registered as screen-dependent.
        4. The requester/action parameter explicitly identifies a screen-dependent caller.
        5. The user's query (passed or recently recorded) represents a screen observation
           request or an on-screen action intent.
        6. Not in interactive mode and no query recorded (allows low-level developer/test usage).

        Returns:
            Tuple[bool, str]: (allowed, reason)
        """
        # 1. Test mode override
        if self._test_mode:
            return True, "Allowed: test mode enabled"

        # 2. Check active programmatic grants
        self._cleanup_expired_grants()
        if self._active_grants:
            active = self._active_grants[-1]
            return True, f"Allowed: programmatic grant active for '{active['requester']}' ({active['reason']})"

        # 3. Check currently executing tool
        if self._current_tool_name and self.is_tool_screen_dependent(self._current_tool_name):
            return True, f"Allowed: current executing tool '{self._current_tool_name}' is screen-dependent"

        # 4. Check explicit requester or action parameters
        if requester and (self.is_tool_screen_dependent(requester) or requester in ("test", "cli", "script")):
            return True, f"Allowed: authorized requester '{requester}'"

        if action and (self.is_tool_screen_dependent(action) or "click" in action.lower() or "screen" in action.lower()):
            return True, f"Allowed: authorized screen action '{action}'"

        # 5. Check user query intent
        query_to_check = user_query
        if not query_to_check:
            recent = self.get_last_user_query()
            if recent:
                query_to_check = recent[0]

        if query_to_check:
            is_intent_allowed, intent_reason = self.evaluate_intent(query_to_check)
            if is_intent_allowed:
                return True, f"Allowed: {intent_reason} from query '{query_to_check}'"
            else:
                # Query was evaluated and determined to be non-screen related!
                logger.info(
                    f"Screen capture denied by policy for query '{query_to_check}': {intent_reason}"
                )
                return False, f"Denied: user query does not require screen context ({intent_reason})"

        # 6. If interactive mode is active but NO query was recorded or provided
        if self._interactive_mode:
            return (
                False,
                "Denied: interactive mode is active but no screen-related user request or action was recorded",
            )

        # 7. Non-interactive fallback (e.g. standalone test or direct script without interactive session)
        return True, "Allowed: non-interactive invocation without restrictive query"

    def clear(self) -> None:
        """Reset policy state for session end."""
        self.clear_user_query()
        self._active_grants.clear()
        self._current_tool_name = None
        self._current_tool_args = None
        self._interactive_mode = False


# Global singleton instance
screen_policy = ScreenAccessPolicy()
