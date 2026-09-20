"""Synthetic local stress checks for the verified pointer/cache experiment."""

from __future__ import annotations

import concurrent.futures
import json
import statistics
import tempfile
import time
from pathlib import Path

from service import Service, pack, sha

ROOT=Path(__file__).resolve().parent
checks={};failures=[]
def check(name,fn):
 try:
  fn()
  checks[name]=True
 except Exception as e:
  checks[name]=False
  failures.append({'test':name,'error':repr(e)})
def eq(a,b):
 assert a==b,(a,b)
def rejects(fn):
 try:
  fn()
 except ValueError:
  return
 raise AssertionError('accepted invalid approval')
class Fixture:
 def __init__(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT)
  self.root=Path(self.tmp.name)
  self.calls=0
  self.source=self.root/'source.txt'
  self.source.write_text('The launch color is blue.\n')
  self.manifest=self.root/'manifest.json'
  self.registry=self.root/'registry.json'
  self.prepare()
  self.s=Service(self.root/'db.sqlite',self.registry,self.retrieve)
  self.s.register('docs','test',['alice'])
 def prepare(self):
  h=sha(self.source)
  self.passage={'sourceId':'one','path':str(self.source),'contentSHA':h,'startLine':1,'endLine':1,'reviewedText':self.source.read_text()}
  self.manifest.write_text(pack({'sources':[{'id':'one','path':str(self.source),'contentSHA':h}], 'preparations':[{**self.passage,'policy':'reviewed','status':'reviewed'}]}))
  self.registry.write_text(pack({'datasets':{'test':{'manifestPath':str(self.manifest),'manifestSHA256':sha(self.manifest),'originals':[{'path':str(self.source),'sha256':h}]}}}))
 def retrieve(self,d,q):self.calls+=1;return {'status':'ready','passages':[self.passage.copy()]}
 def ask(self,**kw):return self.s.search('docs',kw.pop('q','color?'),kw.pop('principal','alice'),**kw)
 def approve(self,r=None,**kw):
  r=r or self.ask();self.s.approve(r['approvalTicket'],'alice','Blue.',[{'sourceId':'one','quote':'blue'}],approved=True,**kw);return r

def basic():
 f=Fixture();r=f.ask();eq(r['status'],'ready');f.approve(r);eq(f.ask()['status'],'verified-cache-hit');eq(f.calls,1)
check('cold_then_verified_cache',basic)
for name,mutate,expected in [
 ('removed_pointer',lambda f:f.s.remove('docs'),'unknown-pointer'),
 ('revoked_principal',lambda f:f.s.register('docs','test',[]),'access-denied'),
 ('changed_source',lambda f:f.source.write_text('green'),'preparation-required'),
 ('missing_source',lambda f:f.source.unlink(),'preparation-required'),
 ('changed_manifest',lambda f:f.manifest.write_text('{}'),'preparation-required'),
 ('corrupt_registry',lambda f:f.registry.write_text('{'),'preparation-required')]:
 def run(m=mutate,e=expected):
  f=Fixture();f.approve();before=f.calls;m(f);eq(f.ask()['status'],e);eq(f.calls,before)
 check(name,run)
for name,args in [('paraphrase',{'q':'What color?'}),('negation',{'q':'Is the color NOT blue?'}),('new_context',{'context':'different project'}),('expired',{'now':time.time()+90000})]:
 def run(a=args):
  f=Fixture();f.approve();eq(f.ask(**a)['status'],'ready');eq(f.calls,2)
 check(name+'_does_not_reuse',run)
def wrong_person():
 f=Fixture();f.approve();eq(f.ask(principal='bob')['status'],'access-denied');eq(f.calls,1)
check('wrong_person_blocked',wrong_person)
def readd():
 f=Fixture();f.approve();f.s.remove('docs');f.s.register('docs','test',['alice']);eq(f.ask()['status'],'ready')
check('remove_readd_cannot_resurrect_cache',readd)
def updated():
 f=Fixture();f.approve();f.source.write_text('The launch color is green.\n');f.prepare();eq(f.ask()['status'],'preparation-required');f.s.register('docs','test',['alice']);r=f.ask();eq(r['status'],'ready');assert 'green' in r['passages'][0]['reviewedText']
check('reviewed_update_requires_reregistration',updated)
def restart():
 f=Fixture();f.approve();f.s=Service(f.root/'db.sqlite',f.registry,f.retrieve);eq(f.ask()['status'],'verified-cache-hit')
check('pointer_and_cache_persist',restart)
def no_auto():
 f=Fixture();f.ask();f.ask();eq(f.calls,2)
check('ready_does_not_auto_approve',no_auto)
for status in ['no-match','preparation-required','refused','error']:
 def run(st=status):
  f=Fixture();f.s.retrieve=lambda d,q:{'status':st};eq(f.ask(),{'status':st});eq(f.ask(),{'status':st})
 check('preserve_'+status,run)
for name,action in [('removed',lambda f:f.s.remove('docs')),('changed_source',lambda f:f.source.write_text('new')),('rebound',lambda f:f.s.register('docs','test',['alice']))]:
 def run(a=action,n=name):
  f=Fixture()
  def retrieve(d,q):a(f);return {'status':'ready','passages':[f.passage]}
  f.s.retrieve=retrieve;r=f.ask();assert r['status']!='ready' and 'passages' not in r
 check('inflight_'+name+'_withholds_output',run)
def ticket_reuse():
 f=Fixture();r=f.approve();rejects(lambda:f.approve(r))
check('approval_ticket_one_use',ticket_reuse)
def fake_quote():
 f=Fixture();f.s.retrieve=lambda d,q:{'status':'ready','passages':[{**f.passage,'reviewedText':'The launch color is orange.'}]};r=f.ask()
 rejects(lambda:f.s.approve(r['approvalTicket'],'alice','Orange.',[{'sourceId':'one','quote':'orange'}],approved=True))
check('forged_quote_with_real_hash_rejected',fake_quote)
def explicit():
 f=Fixture();r=f.ask();rejects(lambda:f.s.approve(r['approvalTicket'],'alice','Blue',[{'sourceId':'one','quote':'blue'}]))
check('explicit_approval_required',explicit)
def delayed():
 f=Fixture();r=f.ask();rejects(lambda:f.approve(r,now=time.time()+700))
check('expired_review_ticket',delayed)
def corrupt():
 f=Fixture();f.approve()
 with f.s.connect() as c:c.execute("UPDATE cache SET body='{' ")
 eq(f.ask()['status'],'ready')
check('corrupted_cache_retrieves_again',corrupt)
def scopes():
 f=Fixture();f.s.register('other','test',['alice']);f.approve();eq(f.s.search('other','color?','alice')['status'],'ready')
check('other_pointer_cannot_reuse',scopes)
def parallel_review():
 f=Fixture();r=f.ask()
 def approval(_):
  try:f.approve(r);return True
  except ValueError:return False
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool: results=list(pool.map(approval,range(24)))
 eq(sum(results),1)
check('concurrent_ticket_consumption_exactly_once',parallel_review)
# High-volume mechanical stress, explicitly synthetic rather than Jev accuracy.
f=Fixture();f.approve();latencies=[]
def hit(_):
 start=time.perf_counter();r=f.ask();eq(r['status'],'verified-cache-hit');return (time.perf_counter()-start)*1000
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:latencies=list(pool.map(hit,range(1000)))
checks['1000_concurrent_cache_hits_no_extra_retrieval']=f.calls==1
out={'checks':checks,'failures':failures,'syntheticCacheHits':len(latencies),'workers':16,'medianMs':statistics.median(latencies),'maxMs':max(latencies),'retrievalCallsIncludingInitial':f.calls}
(ROOT/'stress-results.json').write_text(json.dumps(out,indent=2));print(json.dumps(out));assert all(checks.values())
