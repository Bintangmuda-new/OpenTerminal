from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from pocket_client._transport import parse_ssid

LOGIN_URL = "https://pocketoption.com/en/login/"


async def capture() -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise RuntimeError("Browser login requires the Playwright Python package") from error
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        page = await (await browser.new_context()).new_page()
        loop = asyncio.get_running_loop()
        frame: asyncio.Future[str] = loop.create_future()

        def on_websocket(websocket) -> None:
            websocket.on("framesent", lambda payload: frame.set_result(payload) if isinstance(payload, str) and payload.startswith("42") and '"auth"' in payload and not frame.done() else None)

        page.on("websocket", on_websocket)
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            ssid = await asyncio.wait_for(frame, timeout=300)
            _, is_demo, _ = parse_ssid(ssid)
            if not is_demo:
                raise RuntimeError("Only a DEMO Pocket Option account is accepted")
            return ssid
        finally:
            await browser.close()


def save(ssid: str) -> None:
    target = Path(os.environ["POCKET_BROWSER_SSID_FILE"])
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".pocket-ssid.", suffix=".tmp")
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(ssid.strip())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


async def main() -> None:
    save(await capture())
    print(json.dumps({"ok": True, "demo": True}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)[:240]}), flush=True)
        raise
