"""Exercise activity transitions against the actual browser script without a device."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_activity_job_progress_recovery_and_single_workspace_refresh() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    script = Path("src/photoframe/static/activity.js").resolve()
    scenario = r"""
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
const element = () => ({hidden:true, textContent:'', classList:{add(){},remove(){},toggle(){}},
  listeners:{}, addEventListener(name, fn){this.listeners[name]=fn;},removeAttribute(name){delete this[name];}});
const dock = element(), title=element(), detail=element(), progress=element(), dismiss=element();
dock.querySelector = selector => ({'[data-activity-title]':title,'[data-activity-detail]':detail,
  'progress':progress,'[data-activity-dismiss]':dismiss})[selector];
const events={}, jobs=[], refreshes=[], timers=[];
let busy=false, refreshOk=false;
const context={console, AbortSignal, Date, FormData:class{get(){return 'missing';}}, announceAlbum(){}, setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},
 document:{hidden:false,body:element(),getElementById:()=>dock,querySelector:()=>null,
 addEventListener:(name,fn)=>events[name]=fn}, window:{scrollBy(){}},
 setAlbumBusy:value=>busy=value, currentRenderIntent:()=> 'owned',
 htmx:{swap:()=>refreshes.push(true)},
 fetch:async path=>{if(path==='/partials/workspace')return{ok:refreshOk,text:async()=>'<div>Updated</div>'};const next=jobs.shift();if(next instanceof Error)throw next;return{ok:true,json:async()=>next};}};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const poll=async state=>{jobs.push(state);timers.pop()();await flush();};
(async()=>{
 jobs.push({album:{id:'job',active:true,phase:'loading',album_name:'Summer'},render:{phase:'idle'}});
 vm.runInNewContext(fs.readFileSync(SCRIPT,'utf8'),context);await flush();
 assert.equal(dock.hidden,false);assert.equal(busy,true);assert.equal(progress.value,undefined);
 await poll({album:{id:'job',active:true,phase:'checking',completed:3,total:8,album_name:'Summer'},render:{phase:'idle'}});
 assert.equal(progress.value,3);assert.equal(progress.max,8);assert.match(detail.textContent,/3 of 8/);
 await poll(new Error('offline'));
 assert.equal(title.textContent,'Connection interrupted');assert.equal(dock.hidden,false);
 const complete={album:{id:'job',active:false,phase:'complete',message:'Summer is ready'},render:{phase:'idle'}};
 await poll(complete);assert.equal(refreshes.length,0);
 refreshOk=true;await poll(complete);assert.equal(title.textContent,'Album ready');assert.equal(busy,false);assert.equal(refreshes.length,1);
 await poll(complete);assert.equal(refreshes.length,1);
 dismiss.listeners.click();await poll(complete);assert.equal(dock.hidden,true);
 await poll({album:{phase:'idle'},render:{phase:'waiting',active:true,operation_id:'owned'}});
 assert.equal(title.textContent,'Refreshing e-ink display');assert.equal(progress.value,undefined);
 await poll({album:{phase:'idle'},render:{phase:'failed',active:false,operation_id:'owned',message:'Display timed out'}});
 assert.equal(dismiss.hidden,false);assert.match(detail.textContent,/timed out/);
 dismiss.listeners.click();assert.equal(dock.hidden,true);
 await poll({album:{id:'untracked',phase:'failed',active:false,message:'Album kept'},render:{phase:'idle'}});
 assert.equal(title.textContent,'Album change failed');assert.equal(dock.hidden,false);
 dismiss.listeners.click();await poll({album:{id:'untracked',phase:'failed'},render:{phase:'idle'}});
 assert.equal(dock.hidden,true);
 jobs.push(new Error('Cannot connect'),{album:{phase:'idle'},render:{phase:'idle'}});
 await events.submit({target:{closest:()=>({})},preventDefault(){},stopImmediatePropagation(){}});
 assert.equal(title.textContent,'Album change not confirmed');assert.equal(dock.hidden,false);
 dismiss.listeners.click();await poll({album:{phase:'idle'},render:{phase:'idle'}});
 assert.equal(dock.hidden,true);
 context.document.body.dataset={page:'settings'};
 await poll({album:{id:'other-tab',phase:'loading',active:true},render:{phase:'idle'}});
 await poll({album:{id:'other-tab',phase:'complete',active:false},render:{phase:'idle'}});
 assert.equal(refreshes.length,1,'Other tabs must not replace a Settings form');
})();
""".replace("SCRIPT", json.dumps(str(script)))
    subprocess.run([node, "-e", scenario], check=True)
