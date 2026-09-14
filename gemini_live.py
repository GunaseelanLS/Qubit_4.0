import asyncio
import inspect
import logging
import traceback
import json

logger = logging.getLogger(__name__)
from google import genai
from google.genai import types

class GeminiLive:
    """
    Handles the interaction with the Gemini Live API.
    """
    def __init__(self, api_key, model, input_sample_rate, tools=None, tool_mapping=None):
        """
        Initializes the GeminiLive client.

        Args:
            api_key (str): The Gemini API Key.
            model (str): The model name to use.
            input_sample_rate (int): The sample rate for audio input.
            tools (list, optional): List of tools to enable. Defaults to None.
            tool_mapping (dict, optional): Mapping of tool names to functions. Defaults to None.
        """
        self.api_key = api_key
        self.model = model
        self.input_sample_rate = input_sample_rate
        self.client = genai.Client(api_key=api_key)

        with open("config.json", "r") as f:
            self.config_data = json.load(f)

        self.tools = tools or []
        self.tool_mapping = tool_mapping or {}
        self._session = None

    async def send_image(self, data: bytes, mime_type: str = "image/png"):
        """Send an image to the active Gemini Live session."""
        if not self._session:
            raise RuntimeError("No active Gemini Live session")
        logger.info(f"Sending image to Gemini Live: {len(data)} bytes, mime_type={mime_type}")
        await self._session.send_realtime_input(
            video=types.Blob(data=data, mime_type=mime_type)
        )

    async def start_session(self, audio_input_queue, video_input_queue, text_input_queue, audio_output_callback, audio_interrupt_callback=None):
        identity = self.config_data["identity"]
        personality = self.config_data["personality"]
        behavior = self.config_data["behavior"]
        user = self.config_data["user_profile"]

        instruction = f"""
        You are {identity['name']}.
        Identity:
        - Role: {identity['role']}
        - Purpose: {identity['purpose']}

        Personality:
        - Tone: {personality['tone']}
        - Style: {personality['style']}
        - Character: {personality['character']}
        - Traits: {', '.join(personality['traits'])}

        Behavior Rules:
        - You are running on a Linux desktop.
        - Response Style: {behavior['response_style']}
        - Prioritize accuracy: {behavior['prioritize_accuracy']}
        - Provide complete code: {behavior['provide_complete_code']}
        - Avoid unnecessary explanations: {behavior['avoid_unnecessary_explanations']}
        - Admit uncertainty when needed: {behavior['admit_uncertainty']}
        - Understand Indian English: {behavior['understand_indian_english']}
        - Understand Tamil-accented English: {behavior['understand_tamil_accent']}
        - Maximum response length: {behavior['max_response_length']} words unless the user explicitly asks for more details.

        User Profile:
        - Name: {user['name']}
        - Preferred Name: {user['surname']}
        - Role: {user['role']}
        - Interests: {', '.join(user['interests'])}

        Additional Instructions:
        - Address the user as {user['surname']} unless instructed otherwise.
        - Be professional, calm, logical, and efficient.
        - Act as a personal AI operating system named Qubit. 
        - Help with coding, Linux, AI, academics, productivity, career guidance, and general knowledge.
        - Developed by Gunaseelan LS using Google-genai SDK.
        - Do not invent facts.
        - If unsure, clearly say you do not know.
        - Prefer practical solutions over theoretical discussion.
        - Screen Awareness: Strictly demand-driven. ONLY invoke the screenshot tool when the user explicitly asks about what is on their screen/window/display, asks you to look at something visible, or when executing a screen-dependent action (such as clicking or finding an on-screen element). NEVER invoke the screenshot tool for general conversation, weather, factual queries, chit-chat, or questions unrelated to current screen contents. Once an image is received into your session, visual context of that screen/window remains available to you for follow-up questions without needing repeated screenshots if the window has not changed. If the user explicitly asks for a fresh or current screenshot, or indicates that something on the screen has changed, call the screenshot tool with force=true.
        - Use English in american accent unless i speak in tamil.
        - Also while using tamil, be natural, casual and human like fluency. Not too local or too formal tamil.
        - Use tamil words and phrases that are commonly used in everyday conversation among tamil speakers, especially in the context of technology and coding. Avoid using overly literary or classical tamil that might not fit the casual and approachable tone of Qubit.
        """

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(
                parts=[
                    types.Part(text=instruction)
                ]
        ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Pegasus"     #Aoede,Charon,Fenrir,Kronos,Atlas,Nova,Orion
                    )
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                turn_coverage="TURN_INCLUDES_ONLY_ACTIVITY",
            ),
            tools=self.tools,
        )
        
        logger.info(f"Connecting to Gemini Live with model={self.model}")
        try:
          async with self.client.aio.live.connect(model=self.model, config=config) as session:
            self._session = session
            try:
                from capabilities.screen import set_image_sender
                set_image_sender(self.send_image)
                from agent.screen_policy import screen_policy
                screen_policy.set_interactive_mode(True)
            except ImportError:
                pass
            logger.info("Gemini Live session opened successfully")
            
            async def send_audio():
                try:
                    while True:
                        chunk = await audio_input_queue.get()
                        await session.send_realtime_input(
                            audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={self.input_sample_rate}")
                        )
                except asyncio.CancelledError:
                    logger.debug("send_audio task cancelled")
                except Exception as e:
                    logger.error(f"send_audio error: {e}\n{traceback.format_exc()}")

            async def send_video():
                try:
                    while True:
                        chunk = await video_input_queue.get()
                        logger.info(f"Sending video frame to Gemini: {len(chunk)} bytes")
                        mime_type = "image/png" if chunk.startswith(b"\x89PNG") else "image/jpeg"
                        await session.send_realtime_input(
                            video=types.Blob(data=chunk, mime_type=mime_type)
                        )
                except asyncio.CancelledError:
                    logger.debug("send_video task cancelled")
                except Exception as e:
                    logger.error(f"send_video error: {e}\n{traceback.format_exc()}")

            async def send_text():
                try:
                    while True:
                        text = await text_input_queue.get()
                        logger.info(f"Sending text to Gemini: {text}")
                        try:
                            from agent.screen_policy import screen_policy
                            screen_policy.record_user_query(text)
                        except Exception:
                            pass
                        await session.send_realtime_input(text=text)
                except asyncio.CancelledError:
                    logger.debug("send_text task cancelled")
                except Exception as e:
                    logger.error(f"send_text error: {e}\n{traceback.format_exc()}")

            event_queue = asyncio.Queue()

            async def receive_loop():
                try:
                    while True:
                        async for response in session.receive():
                            logger.debug(f"Received response from Gemini: {response}")
                            
                            # Log the raw response type for debugging
                            if response.go_away:
                                logger.warning(f"Received GoAway from Gemini: {response.go_away}")
                            if response.session_resumption_update:
                                logger.info(f"Session resumption update: {response.session_resumption_update}")
                            
                            server_content = response.server_content
                            tool_call = response.tool_call
                            
                            if server_content:
                                if server_content.model_turn:
                                    for part in server_content.model_turn.parts:
                                        if part.inline_data:
                                            if inspect.iscoroutinefunction(audio_output_callback):
                                                await audio_output_callback(part.inline_data.data)
                                            else:
                                                audio_output_callback(part.inline_data.data)
                                
                                if server_content.input_transcription and server_content.input_transcription.text:
                                    u_text = server_content.input_transcription.text
                                    try:
                                        from agent.screen_policy import screen_policy
                                        screen_policy.record_user_query(u_text)
                                    except Exception:
                                        pass
                                    await event_queue.put({"type": "user", "text": u_text})
                                
                                if server_content.output_transcription and server_content.output_transcription.text:
                                    await event_queue.put({"type": "gemini", "text": server_content.output_transcription.text})
                                
                                if server_content.turn_complete:
                                    await event_queue.put({"type": "turn_complete"})
                                
                                if server_content.interrupted:
                                    if audio_interrupt_callback:
                                        if inspect.iscoroutinefunction(audio_interrupt_callback):
                                            await audio_interrupt_callback()
                                        else:
                                            audio_interrupt_callback()
                                    await event_queue.put({"type": "interrupted"})

                            if tool_call:
                                function_responses = []
                                for fc in tool_call.function_calls:
                                    func_name = fc.name
                                    args = fc.args or {}
                                    
                                    if func_name in self.tool_mapping:
                                        try:
                                            tool_func = self.tool_mapping[func_name]
                                            from agent.screen_policy import screen_policy
                                            with screen_policy.active_tool_context(func_name, args):
                                                if inspect.iscoroutinefunction(tool_func):
                                                    result = await tool_func(**args)
                                                else:
                                                    loop = asyncio.get_running_loop()
                                                    result = await loop.run_in_executor(None, lambda: tool_func(**args))
                                        except Exception as e:
                                            result = f"Error: {e}"
                                        
                                        function_responses.append(types.FunctionResponse(
                                            name=func_name,
                                            id=fc.id,
                                            response={"result": result}
                                        ))
                                        await event_queue.put({"type": "tool_call", "name": func_name, "args": args, "result": result})
                                
                                await session.send_tool_response(function_responses=function_responses)
                        
                        # session.receive() iterator ended (e.g. after turn_complete) — re-enter to keep listening
                        logger.debug("Gemini receive iterator completed, re-entering receive loop")

                except asyncio.CancelledError:
                    logger.debug("receive_loop task cancelled")
                except Exception as e:
                    logger.error(f"receive_loop error: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                    await event_queue.put({"type": "error", "error": f"{type(e).__name__}: {e}"})
                finally:
                    logger.info("receive_loop exiting")
                    await event_queue.put(None)

            send_audio_task = asyncio.create_task(send_audio())
            send_video_task = asyncio.create_task(send_video())
            send_text_task = asyncio.create_task(send_text())
            receive_task = asyncio.create_task(receive_loop())

            try:
                while True:
                    event = await event_queue.get()
                    if event is None:
                        break
                    if isinstance(event, dict) and event.get("type") == "error":
                        # Just yield the error event, don't raise to keep the stream alive if possible or let caller handle
                        yield event
                        break 
                    yield event
            finally:
                logger.info("Cleaning up Gemini Live session tasks")
                try:
                    from capabilities.screen import set_image_sender
                    set_image_sender(None)
                except ImportError:
                    pass
                try:
                    from agent.screen_state import screen_state
                    screen_state.invalidate(reason="session_ended")
                    await screen_state.stop_screencast(reason="gemini_session_ended")
                except Exception as e:
                    logger.warning(f"Error stopping screencast on session cleanup: {e}")
                try:
                    from agent.screen_policy import screen_policy
                    screen_policy.clear()
                except Exception:
                    pass
                self._session = None
                send_audio_task.cancel()
                send_video_task.cancel()
                send_text_task.cancel()
                receive_task.cancel()
        except Exception as e:
            logger.error(f"Gemini Live session error: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            raise
        finally:
            self._session = None
            logger.info("Gemini Live session closed")
