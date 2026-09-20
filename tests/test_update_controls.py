"""Exercise credential-free update actions through the actual browser script."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_update_controls_establish_session_automatically_for_each_action():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    script = Path("src/photoframe/static/updates.js").resolve()
    scenario = r"""
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),calls=[];
const element=id=>{
  assert.ok(!['unlock-form','unlocked','update-pin','lock-update'].includes(id));
  if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,addEventListener(){}});
  return elements.get(id);
};
let generation=0;
const context={document:{getElementById:element},AbortSignal,Set,Date,
  setTimeout(){},clearTimeout(){},
  fetch:async(path,options={})=>{
    calls.push(path);
    if(path.endsWith('/status'))return{ok:true,json:async()=>({managed:true,phase:'idle',weekly:true,running_version:'1.3.0'})};
    if(path.endsWith('/session'))return{ok:true,json:async()=>({csrf:`token-${++generation}`})};
    assert.equal(options.headers['X-CSRF-Token'],`token-${generation}`);
    assert.equal(options.credentials,'same-origin');
    return{ok:true,json:async()=>({phase:'idle'})};
  }};
(async()=>{
  vm.runInNewContext(fs.readFileSync(SCRIPT,'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  await element('check-update').onclick();
  await element('check-update').onclick();
  assert.equal(generation,2);
  assert.deepEqual(calls.filter(p=>!p.endsWith('/status')),
    ['/api/updates/session','/api/updates/check','/api/updates/session','/api/updates/check']);
  assert.equal(element('check-update').disabled,false);
})();
""".replace("SCRIPT", json.dumps(str(script)))
    subprocess.run([node, "-e", scenario], check=True)
