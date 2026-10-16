"""Browser integration: bookmark URLs are independent acceptance requirements."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from areal_rsi.api.base import (
    ArtifactVersion,
    BenchmarkSplit,
    HarnessVersion,
    ModelVersion,
    SolutionVersion,
    VerificationRequest,
    VerifierSpec,
)
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore
from areal_rsi.modules.browser import BrowserArtifactRuntime, BrowserBenchmark, BrowserVerifier

PAGE = r"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<style>*{box-sizing:border-box}body{margin:12px}input,button{min-width:24px;
min-height:28px;
max-width:100%}
article{min-height:40px;
overflow-wrap:anywhere}
form{display:flex;
flex-wrap:wrap;
gap:4px}</style>
<form>
<input data-testid="title-input">
<input data-testid="url-input">
<button data-testid="add">Add</button></form>

<button data-testid="filter-all">All</button>
<button data-testid="filter-active">Active</button>
<button data-testid="filter-completed">Starred</button><main></main>
<script>
let rows=JSON.parse(localStorage.getItem('rows')||'[]'),filter='all';

const q=id=>document.querySelector(`[data-testid="${id}"]`);

const save=()=>localStorage.setItem('rows',JSON.stringify(rows));

function render(){document.querySelector('main').replaceChildren();
for(const r of rows){if(filter==='active'&&r.star||filter==='completed'&&!r.star)continue;
const a=document.createElement('article');
a.dataset.testid='item';
const link=document.createElement('a');
link.href=__HREF__;
link.textContent=r.title;
a.append(link);
const toggle=document.createElement('input');
toggle.type='checkbox';
toggle.dataset.testid='toggle';
toggle.checked=r.star;
toggle.onchange=()=>{r.star=toggle.checked;
save();
render()};
a.append(toggle);

for(const action of ['edit','delete']){const b=document.createElement('button');
b.dataset.testid=action;
b.textContent=action;
b.onclick=()=>{if(action==='delete'){rows=rows.filter(x=>x!==r);
save();
render()}else{const e=document.createElement('input');
e.dataset.testid='edit-input';
e.value=r.title;
const s=document.createElement('button');
s.dataset.testid='save';
s.textContent='Save';
s.onclick=()=>{r.title=e.value;
__EDIT__;
save();
render()};
a.append(e,s)}};
a.append(b)}document.querySelector('main').append(a)}}
document.querySelector('form').onsubmit=e=>{e.preventDefault();
const title=q('title-input').value.trim(),url=q('url-input').value;
let valid=false;
try{valid=['http:','https:'].includes(new URL(url).protocol)}catch{}if(!title||!(__VALID__))return;
rows.push({title,url,star:false});
save();
render()};

for(const f of ['all','active','completed'])q('filter-'+f).onclick=()=>{filter=f;
render()};
render();

</script>"""


@pytest.mark.parametrize("defect", ["none", "missing_link", "lost_on_edit", "unsafe_url"])
def test_bookmark_url_contract(tmp_path: Path, defect: str) -> None:
    pytest.importorskip("playwright.async_api")

    async def scenario() -> None:
        site = tmp_path / "site"
        site.mkdir()
        page = PAGE.replace(
            "__HREF__", "'https://wrong.example/'" if defect == "missing_link" else "r.url"
        )
        page = page.replace(
            "__EDIT__", "r.url='https://lost.example/'" if defect == "lost_on_edit" else ""
        )
        page = page.replace("__VALID__", "true" if defect == "unsafe_url" else "valid")
        (site / "index.html").write_text(page)
        content = FileContentStore(tmp_path / "content")
        ref = await content.snapshot(site)
        solution = SolutionVersion(
            "s",
            ModelVersion("m", "fixture", ref, "test"),
            HarnessVersion("h", "fixture", ref, "test"),
            "test",
            artifacts=(ArtifactVersion("a", "bookmarks", ref, "test"),),
        )
        task = (await BrowserBenchmark("bookmark-web-v1").list_tasks(BenchmarkSplit.FINAL))[1]
        runtime = BrowserArtifactRuntime(
            content,
            FileTrajectoryStore(tmp_path / "trajectories", content),
            tmp_path / "work",
            artifact_name="bookmarks",
        )
        result = await runtime.run(solution, task)
        payload = json.loads(await content.read_text(result.output))
        report = await BrowserVerifier(content).verify(
            VerificationRequest(task, solution, result, VerifierSpec("browser", "fixture"))
        )
        if defect == "none":
            assert report.passed, payload["failures"]
            assert payload["checks"]["url"] == payload["checks"]["invalid_url"] == 1
        else:
            assert not report.passed
            assert (
                payload["checks"][
                    {"missing_link": "url", "lost_on_edit": "edit", "unsafe_url": "invalid_url"}[
                        defect
                    ]
                ]
                == 0
            )

    asyncio.run(scenario())
