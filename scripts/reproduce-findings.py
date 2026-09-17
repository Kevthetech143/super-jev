"""Offline reproducers for the three harness findings. Node 24+, Python 3, POSIX.

Adapted from jev-robust/local-tests.py, which is left untouched. That script
points at its own release snapshot of commit 89e899a; this one points at the
repository it lives in, so it can be run before and after the fixes.

No API calls, no credentials, no .env. Every subprocess is killed after one
second, so this script cannot hang even when the bug under test is present.

Usage:  python3 scripts/reproduce-findings.py [REPO_ROOT]

Exit code 0 means all three findings are fixed, 1 means at least one still
reproduces. Run it against the release snapshot to see the original failures.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent


def execute(args):
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=1)
        return {'blocked': False, 'exit': p.returncode, 'output': p.stdout.strip(), 'error': p.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {'blocked': True, 'timeoutSeconds': 1}


results = []

# Finding 3: the organizer CLI blocked on open() of a named pipe before it could
# reach its regular-file check. Fixed means: returns within the second, nonzero
# exit, and says the input must be a regular file.
with tempfile.TemporaryDirectory() as tmp:
    fifo = Path(tmp) / 'records.json'
    os.mkfifo(fifo)
    r = execute(['node', 'src/cli.ts', 'organize', str(fifo), '--demo'])
    r['fixed'] = not r['blocked'] and r.get('exit') != 0 and 'regular' in r.get('error', '')
    results.append({'test': 'FIFO input rejected without blocking', **r})

# Finding 2: a journal append that never resolves outlived a 20 ms run deadline.
# Fixed means: the run settles well inside the one-second kill timer.
journal = '''
import {run} from './src/loop.ts';
const keepalive=setInterval(()=>{},100);
try {
 const started=Date.now();
 let acted=false;
 const result=await run({
  domain:{name:'probe',observe:async()=>({}),questions:()=>({q:{type:'noul',instructions:'x'}}),
   decide:()=>({kind:'act',action:{tool:'t',args:{}}}),permit:()=>true,
   tools:{t:{validate:()=>true,execute:async()=>{acted=true;return {};}}},
   reduce:s=>s,verify:async()=>false},
  initial:{},evaluator:{evaluate:async()=>({model:'probe',answers:{q:{type:'noul',noul:1}}})},
  journal:{append:()=>new Promise(()=>{})},timeoutMs:20});
 console.log(JSON.stringify({settled:true,elapsedMs:Date.now()-started,status:result.status,reason:result.reason,journaled:result.journaled,acted}));
} catch (error) {
 console.log(JSON.stringify({settled:true,rejected:true,elapsedMs:0,acted:false}));
} finally {clearInterval(keepalive);}
'''
r = execute(['node', '--input-type=module', '-e', journal])
if not r['blocked'] and r.get('exit') == 0:
    try:
        observed = json.loads(r['output'])
    except ValueError:
        observed = {}
    r['observed'] = observed
    r['fixed'] = bool(observed.get('settled')) and not observed.get('acted') and observed.get('elapsedMs', 10_000) < 1000
else:
    r['fixed'] = False
results.append({'test': 'Hung journal bounded by the run deadline', **r})

# Finding 4: the validator accepted score 0 with all probability mass on level 1.
# Fixed means: the call throws, so the process exits nonzero and never prints.
score = '''
import {validateEvaluation} from './src/jev.ts';
validateEvaluation({state:{},questions:{q:{type:'score',instructions:'Rate',criteria:['low','high']}}},
 {model:'probe',answers:{q:{type:'score',score:0,confidence:1,probabilities:{0:0,1:1}}}});
console.log('accepted inconsistent score');
'''
r = execute(['node', '--input-type=module', '-e', score])
r['fixed'] = not r['blocked'] and r.get('exit') != 0 and 'accepted inconsistent score' not in r.get('output', '')
results.append({'test': 'Score inconsistent with its distribution rejected', **r})

print(json.dumps(results, indent=2))
outstanding = [r['test'] for r in results if not r['fixed']]
print(f"\n{len(results) - len(outstanding)}/{len(results)} findings fixed", file=sys.stderr)
for test in outstanding:
    print(f"  still reproduces: {test}", file=sys.stderr)
sys.exit(1 if outstanding else 0)
