Qubit Evolution

1. Live API 1.0
   • Initial experimental stage of the project.
   • Built around Google's Gemini Live API.
   • Focused on establishing real-time voice interaction.
   • Implemented live audio input and audio responses.
   • Established asynchronous communication with Gemini.
   • Created the basic foundation for a voice-based AI assistant.

2. Nexus 2.0
   • Evolved from a voice interface into a desktop assistant.
   • Introduced Gemini Function Calling for computer operations.
   • Natural-language commands could trigger Python tools.
   • Added application launching and process detection.
   • Added active-window detection and window control.
   • Supported operations such as focus, close, minimize and maximize.
   • Added system utilities such as time, date and battery status.
   • Introduced application aliases for more natural voice commands.
   • Added desktop-entry discovery for installed Linux applications.
   • Established the core pipeline:
       Voice → Gemini → Tool → Computer Action

3. Qubit 3.0
   • Nexus was renamed to Qubit.
   • Focused on improving and stabilizing the desktop assistant.
   • Consolidated application, system and window-management tools.
   • Added support for launching new application instances/windows.
   • Improved application detection and process tracking.
   • Window targets could be identified using:
       App name / Alias / Window title / Window ID
   • Introduced platform-specific window management for:
       KDE and GNOME
   • Continued using Gemini Live for real-time voice interaction.
   • Established a reliable functional baseline for Qubit 4.0.

4. Qubit 4.0
   • Major architectural transition from a tool-based assistant
     toward a general-purpose computer agent.
   • Existing functionality was reorganized into modular layers:
       Agent
       Capabilities
       Computer
       Perception
       Platforms
       Memory
       Security
       Voice
   • Existing runtime behavior was preserved during restructuring.
   • Introduced a centralized Tool Registry for Gemini tools.
   • Separated application, system, window and screen capabilities.
   • Preserved the existing KDE/GNOME window-manager abstraction.

   Screen Awareness
   • Introduced demand-driven screen awareness.
   • Screen capture is performed only when visual information is required.
   • Uses XDG Desktop Portal for screen capture.
   • Supports ScreenCast through PipeWire on Wayland.
   • Added screenshot fallback support.
   • Screenshots are temporarily stored in Qubit's picture directory.

   Screen Perception
   • Added OCR for accurate screen-text extraction.
   • Added Gemini Vision for visual and layout understanding.
   • Combines OCR and visual information into Screen Perception.
   • Screen Perception can provide:
       Active window information
       Visible text
       OCR lines and words
       Detected filenames
   • Screen State maintains the current visual observation
     and determines when a new observation is required.

   Screen Security
   • Introduced a Screen Access Policy for demand-driven access.
   • Prevents unrelated conversations from automatically accessing
     the user's screen.
   • ScreenCast permission management supports persistent
     authorization through restore tokens.
   • Security and screen policy are separated from screen capture itself.

   Window Interaction
   • Window management remains platform-independent at the
     capability level.
   • Added lightweight two-way previous-window switching.
   • Maintains the current and previous Qubit-controlled windows.
   • Allows repeated switching between the last two windows
     without continuous background polling.
