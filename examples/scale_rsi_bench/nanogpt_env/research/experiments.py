"""Record at most fourteen development training submissions (one final reserved)."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

p = argparse.ArgumentParser()
p.add_argument('root', type=Path)
p.add_argument('configs', nargs='*')
p.add_argument('--campaign', default='research', help='Workspace directory with its own 14-dev-run budget')
a = p.parse_args()
root = a.root.resolve()
work = root/'workspace'/a.campaign
ledger = work/'attempts.json'
rows = json.loads(ledger.read_text()) if ledger.exists() else []
for name in a.configs:
    if len(rows) >= 14:
        raise RuntimeError('14 development attempts exhausted; reserve attempt 15 for final verifier')
    config = work/(name+'.json')
    selection = work/(name+'.selection.json')
    config_data = json.loads(config.read_text())
    command = [str(Path(__file__).with_name('curate.py').resolve()),
               '--root',str(root),'--features',str(work/'features.npz'),'--config',str(config),
               '--output',str(selection)]
    if config_data.get('coverage'):
        command = ['bash',str(root/'scripts/slurm.sh'),'python']+command
    else:
        command = [sys.executable]+command
    subprocess.run(command,check=True)
    row = dict(attempt=len(rows)+1,name=name,config=json.loads(config.read_text()))
    rows.append(row)
    ledger.write_text(json.dumps(rows,indent=2)+'\n')
    run = subprocess.run(['bash',str(root/'bench.sh'),str(selection),'--name','wht-nano-'+name],
                         capture_output=True,text=True,check=True)
    row.update(json.loads(run.stdout))
    ledger.write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(row),flush=True)
for row in rows:
    if 'run_dir' not in row: continue
    result = Path(row['run_dir'])/'result.json'
    if result.exists():
        row['metrics'] = json.loads(result.read_text())
    print(row['attempt'],row['name'],row.get('metrics',{}).get('heldout_ppl','pending'),row['job_id'])
ledger.write_text(json.dumps(rows,indent=2)+'\n')
