"""Verify the readonly panel and isolated, functional Artifact preview in Chromium."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from pathlib import Path

from playwright.async_api import async_playwright

from areal_rsi.base.storage import write_json
from areal_rsi.ui.web import ResearchServer


async def inspect(root: Path) -> None:
    server = ResearchServer(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                origin = f"http://127.0.0.1:{server.server_port}"
                await page.goto(origin)
                await page.locator("#axis").wait_for()
                assert await page.locator("h1").inner_text()
                await page.get_by_role("button", name="横轴：迭代轮次").click()
                await page.get_by_role("button", name="横轴：执行秒数").wait_for()
                api = await page.request.get(origin + "/api/runs")
                run_id = (await api.json())["runs"][0]["run_id"]
                detail = await page.request.get(origin + "/api/runs/" + run_id)
                run = await detail.json()
                screenshot = root / "panel-desktop.png"
                await page.screenshot(path=str(screenshot), full_page=True)
                preview_verified = False
                if run["target"] == "artifact":
                    solution_id = run["best_solution_id"]
                    preview = await browser.new_page()
                    await preview.goto(origin + f"/preview/{run_id}/{solution_id}/index.html")
                    assert str(server.preview_server.server_port) in preview.url
                    assert (
                        await preview.evaluate(
                            "localStorage.setItem('preview-probe','ok');localStorage.getItem('preview-probe')"
                        )
                        == "ok"
                    )
                    # An embedded document may execute, but cannot read the panel's parent DOM.
                    await page.evaluate(
                        """url => {
                        const frame=document.createElement('iframe');
                        frame.sandbox='allow-scripts allow-same-origin';frame.src=url;
                        frame.id='isolation-probe';document.body.append(frame);
                    }""",
                        preview.url,
                    )
                    frame = page.frame_locator("#isolation-probe")
                    await frame.locator("body").wait_for()
                    blocked = await frame.locator("body").evaluate("""() => {
                        try { void parent.document.body; return false; }
                        catch (error) { return error.name === 'SecurityError'; }
                    }""")
                    assert blocked
                    preview_verified = True
                    await preview.close()
                assert not errors, errors
                result = {
                    "passed": True,
                    "run_id": run_id,
                    "chart_axis_toggle": True,
                    "preview_origin_isolated": preview_verified,
                    "page_errors": errors,
                    "screenshot": str(screenshot),
                }
                write_json(root / "web-integration-report.json", result)
                print(json.dumps(result), flush=True)
            finally:
                await browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(inspect(args.root.resolve()))
