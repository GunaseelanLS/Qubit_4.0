"""Tool Registry for managing Gemini tool declarations and Python callables."""

from typing import Any, Callable, Dict, List
from google.genai import types

from capabilities.applications import open_app

from capabilities.system import (
    get_current_time,
    get_current_date,
    get_battery_status,
)

from capabilities.windows import (
    get_active_window,
    close_active_window,
    focus_window,
    focus_previous_window,
    close_window,
    minimize_window,
    maximize_window,
)

from capabilities.screen import screenshot


class ToolRegistry:
    """Registry maintaining Gemini FunctionDeclarations and Python callable mappings."""

    def __init__(self):
        self._declarations: Dict[str, types.FunctionDeclaration] = {}
        self._mapping: Dict[str, Callable] = {}

    def register(self, name: str, func: Callable, declaration: types.FunctionDeclaration):
        """Register a tool callable along with its Gemini FunctionDeclaration."""
        self._declarations[name] = declaration
        self._mapping[name] = func

    def get_declarations(self) -> List[types.FunctionDeclaration]:
        """Get all registered function declarations."""
        return list(self._declarations.values())

    def get_tools(self) -> List[types.Tool]:
        """Get the list of Tool objects ready for Gemini Live API."""
        return [types.Tool(function_declarations=self.get_declarations())]

    @property
    def tools(self) -> List[types.Tool]:
        """Property shortcut for get_tools()."""
        return self.get_tools()

    def get_tool_mapping(self) -> Dict[str, Callable]:
        """Get the mapping of tool names to Python callables."""
        return dict(self._mapping)

    @property
    def tool_mapping(self) -> Dict[str, Callable]:
        """Property shortcut for get_tool_mapping()."""
        return self.get_tool_mapping()


def create_default_registry() -> ToolRegistry:
    """Build and return a ToolRegistry populated with all baseline Qubit capabilities."""
    registry = ToolRegistry()

    # Application Capabilities
    registry.register(
        name="open_app",
        func=open_app,
        declaration=types.FunctionDeclaration(
            name="open_app",
            description=(
                "Open an application. Works with any installed application, "
                "for example: brave, vscode, terminal, notes, dolphin, "
                "files, calculator, vlc, intellij idea. If the application is "
                "already running and the user requests another instance or a new "
                "window, open a new window instead."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app": {
                        "type": "STRING",
                        "description": (
                            "The application name. Examples: brave, vscode, terminal, "
                            "notes, dolphin, files, calculator."
                        ),
                    },
                    "mode": {
                        "type": "STRING",
                        "description": (
                            "Opening mode. Use 'default' for a normal launch and "
                            "'new_window' to open another window."
                        ),
                        "enum": ["default", "new_window"],
                    },
                },
                "required": ["app"],
            },
        ),
    )

    # System Capabilities
    registry.register(
        name="get_current_time",
        func=get_current_time,
        declaration=types.FunctionDeclaration(
            name="get_current_time",
            description="Get the current system time",
        ),
    )

    registry.register(
        name="get_current_date",
        func=get_current_date,
        declaration=types.FunctionDeclaration(
            name="get_current_date",
            description="Get the current date",
        ),
    )

    registry.register(
        name="get_battery_status",
        func=get_battery_status,
        declaration=types.FunctionDeclaration(
            name="get_battery_status",
            description="Get the battery percentage",
        ),
    )

    # Window Management Capabilities
    registry.register(
        name="get_active_window",
        func=get_active_window,
        declaration=types.FunctionDeclaration(
            name="get_active_window",
            description="Get information about the currently active desktop window including application name and window title.",
        ),
    )

    registry.register(
        name="focus_window",
        func=focus_window,
        declaration=types.FunctionDeclaration(
            name="focus_window",
            description="Focus a window by target. Target can be an app name, alias, window title, or window ID.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app": {
                        "type": "STRING",
                        "description": (
                            "Window target: an application name or alias (e.g. vscode, brave, terminal), "
                            "the full window title, or the window ID/UUID."
                        ),
                    }
                },
                "required": ["app"],
            },
        ),
    )

    registry.register(
        name="close_active_window",
        func=close_active_window,
        declaration=types.FunctionDeclaration(
            name="close_active_window",
            description="Close the currently active desktop window.",
        ),
    )

    registry.register(
        name="close_window",
        func=close_window,
        declaration=types.FunctionDeclaration(
            name="close_window",
            description="""
            Close a window by target. Target can be an app name, alias, window title, or window ID.
            Examples:
            - Close Brave
            - Close VS Code
            - Close Notes
            - Close Dolphin
            """,
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app": {
                        "type": "STRING",
                        "description": (
                            "Window target: an application name or alias, the full window title, "
                            "or the window ID/UUID."
                        ),
                    }
                },
                "required": ["app"],
            },
        ),
    )

    registry.register(
        name="minimize_window",
        func=minimize_window,
        declaration=types.FunctionDeclaration(
            name="minimize_window",
            description="Minimize a window by target. Target can be an app name, alias, window title, or window ID.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app": {
                        "type": "STRING",
                        "description": (
                            "Window target: an application name or alias, the full window title, "
                            "or the window ID/UUID."
                        ),
                    }
                },
                "required": ["app"],
            },
        ),
    )

    registry.register(
        name="maximize_window",
        func=maximize_window,
        declaration=types.FunctionDeclaration(
            name="maximize_window",
            description="Maximize a window by target. Target can be an app name, alias, window title, or window ID.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "app": {
                        "type": "STRING",
                        "description": (
                            "Window target: an application name or alias, the full window title, "
                            "or the window ID/UUID."
                        ),
                    }
                },
                "required": ["app"],
            },
        ),
    )

    registry.register(
        name="focus_previous_window",
        func=focus_previous_window,
        declaration=types.FunctionDeclaration(
            name="focus_previous_window",
            description="Switch to the previously focused window.",
        ),
    )
    
    # Screen / Perception Capabilities
    registry.register(
        name="screenshot",
        func=screenshot,
        declaration=types.FunctionDeclaration(
            name="screenshot",
            description=(
                "Demand-driven screen observation: observe the current desktop screen ONLY when the user explicitly "
                "asks about what is on their screen/window/display, asks to look at or read something visible, or when "
                "a screen-dependent action (such as clicking or finding an on-screen element) requires visual context. "
                "DO NOT call this tool for general conversational queries (e.g., weather, general knowledge, explanations, chit-chat). "
                "Qubit maintains screen awareness: if the current window was already captured and is unchanged, the existing "
                "observation is reused automatically. Set force=true if the user explicitly requests a fresh screenshot, "
                "says something changed on screen, or after an action."
            ),
            parameters={
                "type": "OBJECT",
                "properties": {
                    "force": {
                        "type": "BOOLEAN",
                        "description": (
                            "Set to true to force capturing a fresh screenshot, bypassing the existing observation "
                            "(e.g. when the user explicitly asks for a fresh/current screenshot, says something changed, or after a screen-altering action)."
                        ),
                    }
                },
            },
        ),
    )

    return registry


# Singleton default registry instance
tool_registry = create_default_registry()
