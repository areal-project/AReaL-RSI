"""Static web Artifact execution with real, isolated browser interactions."""

from __future__ import annotations

import asyncio
import functools
import json
import threading
from collections.abc import Sequence
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    BenchmarkSplit,
    JsonObject,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    VerificationRequest,
    VerificationResult,
)
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, new_id, now

CHECKS = (
    "add",
    "edit",
    "toggle",
    "filter",
    "persistence",
    "empty_input",
    "boundary",
    "delete",
    "layout",
    "runtime",
)

BOOKMARK_CHECKS = (*CHECKS, "url", "invalid_url")


class BrowserBenchmark:
    def __init__(self, name: str, *, seed: int | None = None):
        if name not in {"task-web-v1", "bookmark-web-v1"}:
            raise ValueError("Artifact browser benchmark must be task-web-v1 or bookmark-web-v1")
        self.name = name
        self.seed = seed

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        scenario = split.value if self.seed is None else f"{split.value}-{self.seed}"
        return [
            Task(
                f"{self.name}-{scenario}-{width}",
                {
                    "kind": "bookmarks" if self.name == "bookmark-web-v1" else "tasks",
                    "width": width,
                    "height": 900 if width > 600 else 812,
                    "scenario": scenario,
                    "contract": "data-testid CRUD interface v1; see examples/goals/README.md",
                },
            )
            for width in (1280, 375)
        ]


class BrowserArtifactRuntime:
    def __init__(
        self,
        content: FileContentStore,
        trajectories: FileTrajectoryStore,
        workspaces: Path,
        *,
        artifact_name: str,
    ):
        self.content, self.trajectories, self.workspaces = content, trajectories, workspaces
        self.artifact_name = artifact_name

    async def run(
        self, solution: SolutionVersion, task: Task, *, config: JsonObject | None = None
    ) -> TaskResult:
        try:
            from playwright.async_api import Error as PlaywrightError
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise FileNotFoundError("Install areal-rsi[browser] and playwright chromium") from exc
        artifact = next(a for a in solution.artifacts if a.name == self.artifact_name)
        workspace = self.workspaces / new_id("browser")
        site = await self.content.materialize(artifact.content, workspace / "site")
        if not (site / "index.html").is_file():
            raise ValueError("Static web Artifact must contain index.html")

        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(site))
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        data: Any = task.input
        checks: dict[str, float] = {}
        failures: dict[str, str] = {}
        screenshots = []
        try:
            async with async_playwright() as playwright:
                try:
                    browser = await playwright.chromium.launch(args=["--no-sandbox"])
                except PlaywrightError as exc:
                    if "Executable doesn't exist" in str(exc):
                        raise FileNotFoundError(
                            "Chromium is unavailable; run python -m playwright install chromium "
                            "or restore PLAYWRIGHT_BROWSERS_PATH"
                        ) from exc
                    raise
                try:
                    context = await browser.new_context(
                        viewport={"width": data["width"], "height": data["height"]}
                    )
                    await context.route(
                        "**/*",
                        lambda route: (
                            route.continue_()
                            if route.request.url.startswith(origin + "/")
                            else route.abort()
                        ),
                    )
                    page = await context.new_page()
                    page.set_default_timeout(1800)
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.goto(origin, wait_until="networkidle")
                    prefix = data["scenario"] + (
                        "-bookmark" if data["kind"] == "bookmarks" else "-task"
                    )
                    title, renamed = prefix + " alpha", prefix + " updated"
                    bookmark_url = "https://example.org/" + data["scenario"]
                    items = page.get_by_test_id("item")

                    async def add(value: str) -> None:
                        await page.get_by_test_id("title-input").fill(value)
                        if data["kind"] == "bookmarks":
                            await page.get_by_test_id("url-input").fill(bookmark_url)
                        await page.get_by_test_id("add").click()

                    async def check(name: str, operation: Any) -> None:
                        try:
                            await operation()
                            checks[name] = 1.0
                        except Exception as exc:
                            checks[name] = 0.0
                            failures[name] = str(exc)[:1400]

                    async def add_check() -> None:
                        await add(title)
                        assert await items.count() == 1 and title in await items.first.inner_text()

                    async def assert_url(expected: str) -> None:
                        links = items.first.locator("a[href]")
                        assert await links.count() >= 1, "Bookmark requires a usable link"
                        assert await links.first.get_attribute("href") == expected

                    async def edit_check() -> None:
                        await items.first.get_by_test_id("edit").click()
                        await items.first.get_by_test_id("edit-input").fill(renamed)
                        await items.first.get_by_test_id("save").click()
                        assert renamed in await items.first.inner_text()
                        if data["kind"] == "bookmarks":
                            await assert_url(bookmark_url)

                    async def toggle_check() -> None:
                        await items.first.get_by_test_id("toggle").check()
                        assert await items.first.get_by_test_id("toggle").is_checked()

                    async def filter_check() -> None:
                        await page.get_by_test_id("filter-active").click()
                        assert await items.count() == 0
                        await page.get_by_test_id("filter-completed").click()
                        assert await items.count() == 1
                        await page.get_by_test_id("filter-all").click()

                    async def persistence_check() -> None:
                        await page.reload(wait_until="networkidle")
                        assert await items.count() == 1
                        assert renamed in await items.first.inner_text()
                        if data["kind"] == "bookmarks":
                            await assert_url(bookmark_url)
                        assert await items.first.get_by_test_id("toggle").is_checked()

                    async def url_check() -> None:
                        await assert_url(bookmark_url)
                        await page.get_by_test_id("title-input").fill(prefix + " HTTP")
                        await page.get_by_test_id("url-input").fill("http://example.org/other")
                        await page.get_by_test_id("add").click()
                        assert await items.count() == 2
                        assert await items.last.locator("a[href]").first.get_attribute("href") == (
                            "http://example.org/other"
                        )
                        await items.last.get_by_test_id("delete").click()

                    async def invalid_url_check() -> None:
                        for invalid in ("", "not-a-url", "/relative", "javascript:alert(1)"):
                            await page.get_by_test_id("title-input").fill(prefix + " invalid")
                            await page.get_by_test_id("url-input").fill(invalid)
                            await page.get_by_test_id("add").click()
                            assert await items.count() == 1, f"Accepted invalid URL: {invalid!r}"

                    async def empty_check() -> None:
                        await add("   ")
                        assert await items.count() == 1

                    async def boundary_check() -> None:
                        value = (
                            '<img src=x onerror="document.body.dataset.injected=1">' + "长" * 120
                        )
                        await add(value)
                        assert await items.count() == 2
                        assert await page.locator("body").get_attribute("data-injected") is None
                        # Literal user content must not become HTML.
                        assert await items.locator("img").count() == 0

                    async def delete_check() -> None:
                        await items.last.get_by_test_id("delete").click()
                        assert await items.count() == 1

                    async def layout_check() -> None:
                        assert await page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth + 1"
                        )
                        for locator in [
                            page.get_by_test_id("title-input"),
                            page.get_by_test_id("add"),
                            items.first,
                        ]:
                            box = await locator.bounding_box()
                            assert box and box["width"] >= 24 and box["height"] >= 24
                            assert box["x"] >= -1 and box["x"] + box["width"] <= data["width"] + 1

                    for name, operation in [
                        ("add", add_check),
                        ("edit", edit_check),
                        ("toggle", toggle_check),
                        ("filter", filter_check),
                        ("persistence", persistence_check),
                        ("empty_input", empty_check),
                        ("boundary", boundary_check),
                        ("delete", delete_check),
                        ("layout", layout_check),
                    ]:
                        await check(name, operation)
                    if data["kind"] == "bookmarks":
                        await check("url", url_check)
                        await check("invalid_url", invalid_url_check)
                    checks["runtime"] = float(not errors)
                    if errors:
                        failures["runtime"] = "\n".join(errors)[:1400]
                    screenshot = await page.screenshot(full_page=True)
                    screenshots.append(
                        await self.content.put_bytes(screenshot, media_type="image/png")
                    )
                    await context.close()
                finally:
                    await browser.close()
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)
        output: Any = {
            "task_id": task.task_id,
            "solution_id": solution.solution_id,
            "checks": checks,
            "failures": failures,
            "screenshots": [
                {"uri": x.uri, "digest": x.digest, "media_type": x.media_type} for x in screenshots
            ],
        }
        trace = await self.content.put_json(
            {
                "turns": [
                    {"role": "user", "content": json.dumps(data)},
                    {"role": "assistant", "content": json.dumps(output)},
                ]
            }
        )
        ref = await self.trajectories.save(
            Trajectory(
                new_id("trajectory"),
                "browser-verifier",
                trace,
                {},
                now(),
                task.task_id,
                solution.solution_id,
            )
        )
        return TaskResult(await self.content.put_json(output), ref)


class BrowserVerifier:
    def __init__(self, content: FileContentStore):
        self.content = content

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        from areal_rsi.api.base import ContentRef
        from areal_rsi.base.storage import decode

        result = json.loads(await self.content.read_text(request.result.output))
        if (
            result["task_id"] != request.task.task_id
            or result["solution_id"] != request.solution.solution_id
        ):
            raise ValueError("Browser result identity mismatch")
        checks = result["checks"]
        task_input = request.task.input
        expected = (
            BOOKMARK_CHECKS
            if isinstance(task_input, dict) and task_input.get("kind") == "bookmarks"
            else CHECKS
        )
        passed = set(checks) == set(expected) and all(value == 1.0 for value in checks.values())
        return VerificationResult(
            request.task.task_id,
            request.result.trajectory.trajectory_id,
            passed,
            float(passed),
            checks,
            json.dumps(result["failures"]),
            tuple(decode(ContentRef, ref) for ref in result["screenshots"]),
        )
