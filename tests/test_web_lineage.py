"""Version links must follow actual parents, including rollback and branching."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_chart_and_iteration_cards_use_actual_version_parents() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the frontend rendering test")
    script = Path(__file__).parents[1] / "src/areal_rsi/ui/static/app.js"
    check = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({
  location:{hash:''},window:{addEventListener(){}},setInterval(){},
  document:{getElementById(){return {addEventListener(){}};}},
  fetch:()=>new Promise(()=>{}),
});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const step=(iteration,parent,candidate)=>({
  iteration,input_solution_id:parent,candidate_solution_id:candidate,
  validation_score:.5+iteration/10,train_score:.8,next_solution_id:candidate,
  reason:'Reason <script>alert(1)</script>',finished_at:null,
});
context.run={run_id:'run_fixture',baseline_solution_id:'base',baseline_score:.5,max_iterations:4,
  agents:[{agent_id:'research-agent-1',history:[
    step(1,'base','v1'),step(2,'base','v2'),step(3,'v1','v3'),step(4,'missing','v4'),
  ]}]};
const graph=vm.runInContext('chart(run)',context);
const edges=[...graph.matchAll(/data-parent-version="([^"]+)" data-child-version="([^"]+)"/g)]
  .map(match=>[match[1],match[2]]);
assert.deepEqual(edges,[['base','v1'],['base','v2'],['v1','v3']]);
const cards=vm.runInContext(
  'versionHistory(run,run.agents[0]).steps.map(entry=>iterationCard(run,run.agents[0],entry))',context
);
assert.match(cards[1],/data-version-target="version-run_fixture-base"/);
assert.match(cards[2],/data-version-target="version-run_fixture-v1"/);
assert.match(cards[3],/未找到上一版本/);
assert.doesNotMatch(cards[3],/data-version-target="version-run_fixture-missing"/);
for(const card of cards){
  assert.ok(card.indexOf('修改原因')<card.indexOf('<details'));
  assert.match(card,/Reason &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(card,/<script>/);
}
assert.match(graph,/data-version-target="version-run_fixture-v3"/);
"""
    subprocess.run([node, "-e", check, str(script)], check=True, capture_output=True, timeout=10)
