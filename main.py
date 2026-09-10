import asyncio
import os
import sounddevice as sd
from dotenv import load_dotenv

from gemini_live import GeminiLive
from agent.tool_registry import tool_registry

load_dotenv()

RATE = 16000

audio_input_queue = asyncio.Queue()
video_input_queue = asyncio.Queue()
text_input_queue = asyncio.Queue()

loop = None
is_speaking = False


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
        tools=tool_registry.get_tools(),
        tool_mapping=tool_registry.get_tool_mapping()
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