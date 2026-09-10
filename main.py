import asyncio
import os
import sounddevice as sd
from dotenv import load_dotenv

from gemini_live import GeminiLive
from tools import *
from google.genai import types
from window_manager.manager import get_window_manager

window_manager = get_window_manager()

load_dotenv()

RATE = 16000

audio_input_queue = asyncio.Queue()
video_input_queue = asyncio.Queue()
text_input_queue = asyncio.Queue()

loop = None
is_speaking = False


tool_mapping = {
    #Apps
    "open_app": open_app,
    #System
    "get_current_time": get_current_time,
    "get_current_date": get_current_date,
    "get_battery_status": get_battery_status,
    #Window
    "get_active_window": get_active_window,
    "close_active_window":close_active_window,
    "close_window":close_window,
    "focus_window":focus_window,
    "minimize_window":minimize_window,
    "maximize_window":maximize_window
}



tools = [
    types.Tool(
        function_declarations=[
            
            #App Management
            types.FunctionDeclaration(
                name="open_app",
                description=(
                    "Open an application. Works with any installed application, "
                    "for example: brave, vscode, terminal, notes, dolphin, firefox, "
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
                                "notes, dolphin, firefox, files, calculator."
                            )
                        },
                        "mode": {
                            "type": "STRING",
                            "description": (
                                "Opening mode. Use 'default' for a normal launch and "
                                "'new_window' to open another window."
                            ),
                            "enum": ["default", "new_window"]
                        }
                    },
                    "required": ["app"]
                }
            ),

            #System Functions
            types.FunctionDeclaration(
                name="get_current_time",
                description="Get the current system time"
            ),

            types.FunctionDeclaration(
                name="get_current_date",
                description="Get the current date"
            ),

            types.FunctionDeclaration(
                name="get_battery_status",
                description="Get the battery percentage"
            ),

            #Window Management
            types.FunctionDeclaration(
                name="get_active_window",
                description="Get information about the currently active desktop window including application name and window title."
            ),

            types.FunctionDeclaration(
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
                            )
                        }
                    },
                    "required": ["app"]
                }
            ),

            types.FunctionDeclaration(
                name="close_active_window",
                description="Close the currently active desktop window."
            ),

            types.FunctionDeclaration(
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
                            )
                        }
                    },
                    "required": ["app"]
                }
            ),

            types.FunctionDeclaration(
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
                            )
                        }
                    },
                    "required": ["app"]
                }
            ),

            types.FunctionDeclaration(
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
                            )
                        }
                    },
                    "required": ["app"]
                }
            )

        ]
    )
]


def mic_callback(indata, frames, time, status):
    global is_speaking

    if status:
        print(status)

    # Ignore microphone while Gemini is speaking
    if is_speaking:
        return

    if loop:
        loop.call_soon_threadsafe(
            audio_input_queue.put_nowait,
            bytes(indata)
        )


output_stream = sd.RawOutputStream(
    samplerate=24000,
    channels=1,
    dtype="int16"
)


def play_audio(data):
    global is_speaking

    is_speaking = True

    try:
        output_stream.write(data)
    finally:
        is_speaking = False


async def main():
    global loop

    loop = asyncio.get_running_loop()

    output_stream.start()

    mic_stream = sd.RawInputStream(
        samplerate=RATE,
        blocksize=1024,
        channels=1,
        dtype="int16",
        callback=mic_callback
    )

    mic_stream.start()

    gemini = GeminiLive(
        api_key=os.getenv("GEMINI_API_KEY"),
        model="gemini-3.1-flash-live-preview",
        input_sample_rate=RATE,
        tools=tools,
        tool_mapping=tool_mapping
    )

    print("\nQubit Started...")
    print("Speak Thala!\n")

    async for event in gemini.start_session(
        audio_input_queue=audio_input_queue,
        video_input_queue=video_input_queue,
        text_input_queue=text_input_queue,
        audio_output_callback=play_audio
    ):
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")

        if event_type == "user":
            user_text=event['text']
            print(f"\nYou: {user_text}")

            if user_text.lower() in ["goodbye qubit.","goodbye.","bye qubit.","okay, bye.","okay, stop.","ok, stop."]:
                print("\nGoodbye!!!")
                break   

        elif event_type == "gemini":
            print(f"Qubit: {event['text']}")

        elif event_type == "error":
            print(f"\nError: {event['error']}")


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nGoodbye!!!")