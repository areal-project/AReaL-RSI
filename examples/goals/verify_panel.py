"""Verify a real persisted Goal in the read-only browser panel, without inference."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from areal_rsi.ui.web import ResearchServer


async def verify(root: Path, run_id: str, expected_state: str, output: Path) -> None:
    server = ResearchServer(root.resolve(), port=0, run_id=run_id)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    preview_origin = f"http://127.0.0.1:{server.preview_server.server_port}"
    errors: list[str] = []
    contexts: dict[int, str] = {}
    exceptions: list[dict[str, str]] = []

    def execution_context(event: dict[str, Any]) -> None:
        context = event["context"]
        contexts[context["id"]] = context.get("origin", "")

    def exception(event: dict[str, Any]) -> None:
        details = event["exceptionDetails"]
        location = contexts.get(details.get("executionContextId"), "") or details.get("url", "")
        message = details.get("exception", {}).get("description", details.get("text", ""))
        exceptions.append({"origin": location, "message": message})

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                cdp = await page.context.new_cdp_session(page)
                cdp.on("Runtime.executionContextCreated", execution_context)
                cdp.on("Runtime.exceptionThrown", exception)
                await cdp.send("Runtime.enable")
                await page.goto(origin + "/#" + run_id)
                await page.locator(".runhead").wait_for(timeout=60000)
                response = await page.request.get(origin + "/api/runs/" + run_id)
                data: dict[str, Any] = await response.json()
                assert data["goal_contract"]["state"] == expected_state
                assert data["goal_contract"]["outcome"] is not None
                text = await page.locator("#main").inner_text()
                final = data["final_acceptance"]
                if final:
                    assert "独立最终验收 · " + ("通过" if final["passed"] else "未通过") in text
                    assert f"{final['candidate']['score'] * 100:.1f}%" in text
                await page.get_by_role("button", name="横轴：迭代轮次").click()
                await page.get_by_role("button", name="横轴：执行秒数").wait_for()
                if final and data.get("final_elapsed_seconds") is not None:
                    ticks = await page.locator("svg.chart text").all_text_contents()
                    assert float(ticks[-1]) >= data["final_elapsed_seconds"] - 0.1
                assert "Research Skills" in text
                panel_errors = [e for e in exceptions if e["origin"] == origin]
                preview_errors = [e for e in exceptions if e["origin"] == preview_origin]
                unknown_errors = [
                    e for e in exceptions if e["origin"] not in {origin, preview_origin}
                ]
                assert not panel_errors, panel_errors
                assert not unknown_errors, unknown_errors
                assert len(exceptions) >= len(errors), "Unattributed browser exception"
                output.parent.mkdir(parents=True, exist_ok=True)
                screenshot = output.with_suffix(".png")
                await page.screenshot(path=str(screenshot), full_page=True)
                report = {
                    "passed": True,
                    "root": str(root),
                    "run_id": run_id,
                    "goal_state": expected_state,
                    "goal_outcome_matches_state": True,
                    "independent_acceptance_passed": final["passed"] if final else None,
                    "chart_axis_toggle": True,
                    "final_time_within_rendered_axis": data.get("final_elapsed_seconds")
                    is not None,
                    "skill_evidence": data["skill_evidence"],
                    "skill_lifecycle_records": len(data["skill_lifecycle"]),
                    "skill_reuse_reports": len(data["skill_reuse"]),
                    "page_errors": panel_errors,
                    "preview_errors": preview_errors,
                    "unattributed_errors": unknown_errors,
                    "all_pageerror_events": errors,
                    "screenshot": str(screenshot),
                }
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(json.dumps({"report": str(output), "passed": True}))
            finally:
                await browser.close()
    finally:
        await asyncio.to_thread(server.shutdown)
        thread.join(timeout=3)
        await asyncio.to_thread(server.server_close)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-state", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.root, args.run_id, args.expected_state, args.output))
