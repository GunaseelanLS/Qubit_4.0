"""Screen awareness and observation state management for Qubit 4.0.

Maintains the currently observed screen state (active window identity,
timestamp, validity, observation context) and decides whether an existing
visual observation can be reused or if a new screenshot must be captured.
"""

import asyncio
from dataclasses import dataclass, field
import inspect
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SCREENCAST_IDLE_TIMEOUT = float(os.getenv("QUBIT_SCREENCAST_IDLE_TIMEOUT", 30.0))


@dataclass
class ScreenObservation:
    """Represents a visual screen observation sent to Gemini Live."""

    window_info: Optional[Dict[str, Any]] = None
    timestamp: float = 0.0
    is_valid: bool = False
    context: Dict[str, Any] = field(default_factory=dict)
    ocr_data: Optional[Dict[str, Any]] = None
    perception: Optional[Any] = None

    @property
    def app(self) -> Optional[str]:
        if self.window_info:
            return self.window_info.get("app")
        return None

    @property
    def caption(self) -> Optional[str]:
        if self.window_info:
            return self.window_info.get("caption")
        return None

    @property
    def uuid(self) -> Optional[str]:
        if self.window_info:
            return self.window_info.get("uuid")
        return None

    def age(self, current_time: Optional[float] = None) -> float:
        """Return age of this observation in seconds."""
        now = current_time if current_time is not None else time.time()
        return max(0.0, now - self.timestamp)


class ScreenState:
    """Manages the visual observation lifecycle and reuse decisions."""

    def __init__(
        self,
        default_ttl_seconds: float = 30.0,
        screencast_idle_timeout: float = DEFAULT_SCREENCAST_IDLE_TIMEOUT,
    ):
        self.default_ttl_seconds = default_ttl_seconds
        self.screencast_idle_timeout = screencast_idle_timeout
        self._current_observation: Optional[ScreenObservation] = None
        self._screencast_session: Optional[Any] = None
        self._last_screencast_use: float = 0.0
        self._idle_timer_task: Optional[asyncio.Task] = None

    def get_screencast_session(self) -> Optional[Any]:
        """Retrieve the currently active ScreenCast session, if any."""
        return self._screencast_session

    def is_screencast_active(self) -> bool:
        """Check whether an on-demand ScreenCast session is currently running."""
        return bool(self._screencast_session and getattr(self._screencast_session, "is_active", False))

    def register_screencast_session(self, session: Any) -> None:
        """Register a new ScreenCast session and arm the idle timeout timer."""
        self._screencast_session = session
        self.touch_screencast()

    def touch_screencast(self) -> None:
        """Record activity on the ScreenCast session and restart the idle timeout timer."""
        self._last_screencast_use = time.time()
        if self._idle_timer_task and not self._idle_timer_task.done():
            self._idle_timer_task.cancel()

        try:
            loop = asyncio.get_running_loop()
            self._idle_timer_task = loop.create_task(
                self._screencast_idle_worker(self.screencast_idle_timeout)
            )
        except RuntimeError:
            self._idle_timer_task = None

    async def _screencast_idle_worker(self, timeout: float) -> None:
        """Background coroutine that waits for idle timeout before releasing ScreenCast resources."""
        try:
            await asyncio.sleep(timeout)
            logger.info(
                f"ScreenCast idle timeout ({timeout}s) expired without activity. Stopping session."
            )
            await self.stop_screencast(reason="idle_timeout")
        except asyncio.CancelledError:
            pass

    async def stop_screencast(self, reason: str = "manual_stop") -> None:
        """Stop any active ScreenCast session and release PipeWire/portal resources."""
        if self._idle_timer_task and not self._idle_timer_task.done():
            self._idle_timer_task.cancel()
            self._idle_timer_task = None

        session = self._screencast_session
        self._screencast_session = None

        if session:
            try:
                if hasattr(session, "stop") and inspect.iscoroutinefunction(session.stop):
                    await session.stop()
                elif hasattr(session, "stop"):
                    session.stop()
                logger.info(f"ScreenCast session stopped cleanly (reason='{reason}').")
            except Exception as e:
                logger.warning(f"Error during ScreenCast session teardown: {e}")

    def get_current_observation(self) -> Optional[ScreenObservation]:
        """Retrieve the currently active visual observation, if any."""
        return self._current_observation

    def record_observation(
        self,
        window_info: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
        ocr_data: Optional[Dict[str, Any]] = None,
        perception: Optional[Any] = None,
    ) -> ScreenObservation:
        """Record a successful screen capture transmission to Gemini with OCR and perception."""
        obs = ScreenObservation(
            window_info=dict(window_info) if window_info else None,
            timestamp=timestamp if timestamp is not None else time.time(),
            is_valid=True,
            context=dict(context) if context else {},
            ocr_data=ocr_data,
            perception=perception,
        )
        self._current_observation = obs
        logger.info(
            f"Recorded screen observation: window={obs.window_info}, timestamp={obs.timestamp}, has_ocr={bool(ocr_data)}"
        )
        return obs

    def invalidate(self, reason: str = "manual_invalidation") -> None:
        """Invalidate the current visual observation.

        Called when window focus changes, UI actions modify the screen,
        or an explicit reset occurs.
        """
        if self._current_observation and self._current_observation.is_valid:
            logger.info(f"Invalidating screen observation: reason='{reason}'")
            self._current_observation.is_valid = False
            self._current_observation.context["invalidation_reason"] = reason

    def clear(self) -> None:
        """Completely reset the observation state and active ScreenCast reference."""
        self._current_observation = None
        if self._idle_timer_task and not self._idle_timer_task.done():
            self._idle_timer_task.cancel()
        self._idle_timer_task = None
        self._screencast_session = None

    def is_stale(
        self,
        observation: Optional[ScreenObservation] = None,
        max_age_seconds: Optional[float] = None,
    ) -> bool:
        """Check if an observation has exceeded the allowable freshness window."""
        obs = observation or self._current_observation
        if not obs or not obs.is_valid:
            return True
        ttl = max_age_seconds if max_age_seconds is not None else self.default_ttl_seconds
        return obs.age() > ttl

    @staticmethod
    def is_same_window(
        window_a: Optional[Dict[str, Any]],
        window_b: Optional[Dict[str, Any]],
    ) -> bool:
        """Determine if two window dictionaries describe the exact same window."""
        if not window_a or not window_b:
            return False

        app_a = (window_a.get("app") or "").strip().lower()
        app_b = (window_b.get("app") or "").strip().lower()

        if app_a != app_b:
            return False

        # Compare window UUID if both present
        uuid_a = window_a.get("uuid")
        uuid_b = window_b.get("uuid")
        if uuid_a and uuid_b and uuid_a != uuid_b:
            return False

        # Compare window caption/title
        caption_a = (window_a.get("caption") or "").strip()
        caption_b = (window_b.get("caption") or "").strip()
        if caption_a != caption_b:
            return False

        return True

    def should_capture(
        self,
        force: bool = False,
        current_window: Optional[Dict[str, Any]] = None,
        max_age_seconds: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Evaluate whether a new screenshot is required or can be reused.

        Returns:
            Tuple[bool, str]: (needs_capture, reason)
        """
        if force:
            return True, "explicit_fresh_request"

        obs = self._current_observation
        if obs is None:
            return True, "no_observation"

        if not obs.is_valid:
            return True, "observation_invalid"

        if self.is_stale(obs, max_age_seconds):
            return True, "observation_stale"

        if current_window is not None:
            if not self.is_same_window(current_window, obs.window_info):
                return True, "window_changed"

        return False, "observation_reusable"


# Global singleton instance
screen_state = ScreenState(default_ttl_seconds=30.0)
