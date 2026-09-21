"""End-to-end live navigation diagnostic, fictional reviewed sources only."""
import json,os,subprocess,sys,time
from pathlib import Path
D=Path(__file__).resolve().parent
R=Path(__file__).resolve().parents[3]
F=json.loads((D/'frozen.json').read_text())
for source in F['sources']:
    source['path']=str(D/source['path'])
# Every reproduction has isolated connector/cache/output state.
import tempfile
(R/'.local').mkdir(exist_ok=True)
D=Path(tempfile.mkdtemp(prefix='navigation-',dir=R/'.local'))
C=D/'config.json'
C.write_text(json.dumps({'db':str(D/'memory.db'),'registry':str(D/'registry.json'),'providerTimeoutSeconds':90}))
CLI=R/'experiments/verified-pointer-memory/cli.py'
def call(q):
    f=D/'request.json';f.write_text(json.dumps(q))
    t=time.monotonic()
    p=subprocess.run([sys.executable,str(CLI),'--config',str(C),'--input',str(f)],text=True,capture_output=True,timeout=95)
    try:r=json.loads(p.stdout)
    except Exception:raise RuntimeError('CLI failed to return JSON') from None
    return r,round(time.monotonic()-t,3)
for structure in F['structures']:
    q={'action':'connect','pointer':structure,'structure':structure,'sources':F['sources'],'principals':['pilot']}
    preview,_=call(q)
    assert preview.get('reason')=='review-required',preview
    (D/(structure+'-preview.json')).write_text(json.dumps(preview,indent=2))
    q.update(sources=preview['sources'],reviewed=True,navigationSHA=preview['navigationSHA'])
    reg,_=call(q)
    assert reg.get('status')=='registered',reg
rows=[];calls=0
for repeat in range(1,F['repeats']+1):
    for c in F['cases']:
        for structure in (F['structures'] if repeat==1 else list(reversed(F['structures']))):
            # Reserve full bound so an unexpectedly deep traversal cannot overrun budget.
            if calls+F['limits']['maxRounds']>F['budget']:raise RuntimeError('Provider call budget reached')
            r,seconds=call({'action':'navigate','pointer':structure,'principal':'pilot','question':c['question'],'limits':F['limits']})
            calls+=r.get('calls',F['limits']['maxRounds'])
            ids=[v['sourceId'] for v in r.get('candidates',[])]
            score=('hit' if c['expected'] in ids else 'miss') if c['expected'] else ('empty' if not ids and r.get('status')=='no-candidates' else 'suggested-or-error')
            row={'case':c['id'],'repeat':repeat,'structure':structure,'expected':c['expected'],'outcome':score,'seconds':seconds,'result':r}
            rows.append(row)
            with (D/'results.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
            print(json.dumps({k:row[k] for k in ('case','repeat','structure','outcome','seconds')}),flush=True)
(D/'summary.json').write_text(json.dumps({'calls_reported':calls,'results':rows},indent=2))
