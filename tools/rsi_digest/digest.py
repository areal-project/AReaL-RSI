"""RSI research collection and reviewed-report publishing. Python 3.11+, stdlib only."""

import argparse
import fcntl
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1] / ".rsi" / "research-digest"


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def canonical(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise ValueError("Expected HTTP(S) source")
    if p.hostname in ("arxiv.org", "export.arxiv.org"):
        path = re.sub(r"^/(pdf|html)/", "/abs/", p.path)
        return "https://arxiv.org" + re.sub(r"(?:v\d+)?(?:\.pdf)?$", "", path)
    return urllib.parse.urlunsplit(("https", p.netloc.lower(), p.path.rstrip("/"), p.query, ""))


def fetch(url):
    headers = {"User-Agent": "RSI-Research-Digest/1.0"}
    if urllib.parse.urlsplit(url).hostname == "api.github.com" and os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
        return r.read().decode()


def collect(config, root, day):
    since = (date.fromisoformat(day) - timedelta(days=config["lookback_days"])).isoformat()
    history = sorted(root.glob("*/publication.json"))
    verified = [p.parent.name for p in history if read(p, {}).get("verified")]
    if verified:
        since = min(since, max(verified))
    rows, coverage, snapshots = [], [], {}

    def attempt(label, fn):
        try:
            value = fn()
            snapshots[label] = value
            coverage.append({"source": label, "status": "ok", "count": len(value)})
            return value
        except Exception as exc:
            coverage.append({"source": label, "status": "failed", "error": str(exc)})
            return []

    def papers():
        params = urllib.parse.urlencode(
            {
                "search_query": config["arxiv_query"],
                "start": 0,
                "max_results": 100,
                "sortBy": "lastUpdatedDate",
                "sortOrder": "descending",
            }
        )
        feed = ET.fromstring(fetch("https://export.arxiv.org/api/query?" + params))
        result = []
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in feed.findall(ns + "entry"):
            fields = {
                k: " ".join(entry.findtext(ns + k, "").split())
                for k in ("id", "title", "summary", "published", "updated")
            }
            if fields["updated"][:10] >= since:
                result.append(dict(fields, url=fields["id"], source="arxiv"))
        return result

    rows.extend(attempt("arxiv_latest_100", papers))
    for query in config["github_queries"]:

        def search(query=query):
            params = urllib.parse.urlencode(
                {"q": query + " pushed:>=" + since, "sort": "updated", "per_page": 30}
            )
            payload = json.loads(fetch("https://api.github.com/search/repositories?" + params))
            if payload.get("incomplete_results"):
                coverage.append({"source": query, "status": "partial"})
            return [
                dict(
                    url=r["html_url"],
                    title=r["full_name"],
                    source="github",
                    description=r.get("description"),
                    updated=r["pushed_at"],
                )
                for r in payload["items"]
            ]

        rows.extend(attempt("github: " + query, search))
        time.sleep(2)
    for repo in config["watch_repos"]:
        attempt(
            "commits: " + repo,
            lambda repo=repo: json.loads(
                fetch(
                    "https://api.github.com/repos/"
                    + repo
                    + "/commits?"
                    + urllib.parse.urlencode({"since": since + "T00:00:00Z", "per_page": 30})
                )
            ),
        )
    seen = read(root / "seen.json", {})
    unique = {}
    for row in rows:
        row["url"] = canonical(row["url"])
        row["previously_reported"] = seen.get(row["url"])
        unique[row["url"]] = row
    packet = {
        "date": day,
        "since": since,
        "coverage": coverage,
        "items": list(unique.values()),
        "snapshots": snapshots,
        "web_queries": config["web_queries"],
        "notice": "Bounded candidates only; perform web searches and verify primary sources.",
    }
    save(root / day / "candidates.json", packet)
    return {"count": len(unique), "coverage": coverage, "since": since}


def render(report):
    date.fromisoformat(report["date"])
    if not {"arxiv", "github", "wechat", "xiaohongshu"} <= set(report["coverage"]):
        raise ValueError("All four channel coverage statuses required")
    if not report["summary"].strip():
        raise ValueError("Summary required")
    lines = [f"# RSI 研究日报｜{report['date']}", "", report["summary"], "", "## 渠道覆盖", ""]
    lines.extend(f"- **{k}**：{v}" for k, v in report["coverage"].items())
    seen = set()
    for item in report["items"]:
        key = canonical(item["url"])
        if key in seen:
            raise ValueError("Duplicate source")
        seen.add(key)
        for field in (
            "title",
            "source",
            "event_date",
            "evidence",
            "finding",
            "limitations",
            "action",
        ):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError("Missing field: " + field)
        if item["event_date"] != "unknown" and date.fromisoformat(
            item["event_date"]
        ) > date.fromisoformat(report["date"]):
            raise ValueError("Future event")
        event = "日期待核实" if item["event_date"] == "unknown" else item["event_date"]
        lines.extend(
            [
                "",
                "## " + item["title"],
                "",
                f"来源：[{item['source']}]({item['url']}) · {event}",
                "",
                "证据：" + item["evidence"],
                "",
                item["finding"],
                "",
                "限制：" + item["limitations"],
                "",
                "工程建议：" + item["action"],
            ]
        )
    return "\n".join([*lines, "", "## 下一步", "", report.get("next_steps", "继续核验增量。"), ""])


def yuque(*args):
    r = subprocess.run(
        [os.getenv("YUQUE_BIN", "yuque"), *args, "--json"],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    return json.loads(r.stdout)


def publish(config, root, report, body):
    path = root / report["date"] / "publication.json"
    journal = read(path, {})
    title = f"RSI 研究日报｜{report['date']}"
    if journal.get("pending") and not journal.get("route"):
        raise RuntimeError("Uncertain previous create: reconcile remote exact title before retry")
    if journal.get("route"):
        result = yuque("update", "doc", journal["route"], "--body-file", str(body))
    else:
        docs = yuque("list", "docs", config["namespace"], "--limit", "100")
        matches = [d for d in docs if d["title"] == title]
        if len(matches) > 1:
            raise RuntimeError(
                "Multiple remote reports with same date; manual reconciliation needed"
            )
        if matches:
            route = config["namespace"] + "/" + matches[0]["slug"]
            save(path, {"route": route, "url": matches[0]["url"], "verified": False})
            result = yuque("update", "doc", route, "--body-file", str(body))
        else:
            save(path, {"pending": True, "title": title})
            result = yuque(
                "create",
                "doc",
                "--namespace",
                config["namespace"],
                "--title",
                title,
                "--body-file",
                str(body),
                "--format",
                "markdown",
                "--idempotency-key",
                "rsi-digest-" + report["date"],
            )
    route = config["namespace"] + "/" + result["slug"]
    journal = {"route": route, "url": result["url"], "verified": False}
    save(path, journal)
    remote = yuque("show", "doc", route)
    if remote.get("title") != title or not remote.get("body") or not remote.get("published_at"):
        raise RuntimeError("Publication read-back failed")
    if any(item["url"] not in remote["body"] for item in report["items"]):
        raise RuntimeError("Read-back missing citations")
    journal["verified"] = True
    save(path, journal)
    seen = read(root / "seen.json", {})
    for item in report["items"]:
        seen[canonical(item["url"])] = {
            "reported_on": report["date"],
            "event_date": item["event_date"],
        }
    save(root / "seen.json", seen)
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "render", "publish"])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--state-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    config = read(HERE / "config.json")
    day = datetime.now(ZoneInfo(config["timezone"])).date().isoformat()
    root = args.state_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "collect":
            result = collect(config, root, day)
        else:
            report = read(args.report or root / day / "report.json")
            content = render(report)
            save(root / report["date"] / "report.json", report)
            body = root / report["date"] / "report.md"
            body.write_text(content)
            result = (
                {"path": str(body)}
                if args.command == "render"
                else publish(config, root, report, body)
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
