import asyncio
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus


SCREENSHOT_DIR = Path.home() / "Pictures" / "Qubit"

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENSHOT_INTERFACE = "org.freedesktop.portal.Screenshot"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"


async def screenshot():
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    bus = await MessageBus(bus_type=BusType.SESSION).connect()

    token = f"qubit_{uuid.uuid4().hex}"

    options = {
        "handle_token": Variant("s", token),
    }

    result = await bus.call(
        Message(
            destination=PORTAL_SERVICE,
            path=PORTAL_PATH,
            interface=SCREENSHOT_INTERFACE,
            member="Screenshot",
            signature="sa{sv}",
            body=["", options],
        )
    )

    if result.message_type == MessageType.ERROR:
        bus.disconnect()
        raise RuntimeError(result.body)

    request_path = result.body[0]

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def message_handler(message):
        if (
            message.message_type == MessageType.SIGNAL
            and message.path == request_path
            and message.interface == REQUEST_INTERFACE
            and message.member == "Response"
        ):
            if not future.done():
                future.set_result(message.body)

    bus.add_message_handler(message_handler)

    response_code, response = await future

    bus.disconnect()

    if response_code != 0:
        raise RuntimeError(f"Screenshot request failed: {response}")

    uri_variant = response.get("uri")

    if uri_variant is None:
        raise RuntimeError(f"Screenshot URI missing: {response}")

    uri = uri_variant.value

    if not uri.startswith("file://"):
        raise RuntimeError(f"Invalid screenshot URI: {uri}")

    source = Path(unquote(uri[7:]))

    if not source.exists():
        raise FileNotFoundError(f"Screenshot not found: {source}")

    destination = SCREENSHOT_DIR / f"screenshot_{uuid.uuid4().hex}.png"

    shutil.move(str(source), str(destination))

    return destination


def delete_screenshot(path):
    path = Path(path).resolve()
    screenshot_dir = SCREENSHOT_DIR.resolve()

    if path.parent != screenshot_dir:
        raise ValueError("Path is outside Qubit screenshot directory")

    if path.exists():
        path.unlink()


def cleanup_screenshots():
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    for path in SCREENSHOT_DIR.glob("screenshot_*.png"):
        try:
            path.unlink()
        except OSError:
            pass


def read_screenshot(path):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Screenshot not found: {path}")

    return path.read_bytes()