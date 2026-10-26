"""The picker verifies and stages the chosen version without poll-driven selection changes."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_release_picker_verification_selection_channel_and_legacy_controls():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    script = Path("src/photoframe/static/updates.js").resolve()
    scenario = r"""
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),calls=[];
class Option { constructor(text,value){this.textContent=text;this.value=value;this.disabled=false;} }
const element=id=>{
  if(!elements.has(id))elements.set(id,{hidden:false,disabled:false,dataset:{},value:'',textContent:'',
    addEventListener(){},removeAttribute(){},replaceChildren(...children){this.children=children;this.value=children[0]?.value||'';}});
  return elements.get(id);
};
let nextPoll,modern=true,channel='stable';
const verified={version:'9.0.0',updater_protocol:1,settings_schema_min:2,settings_schema_max:2,
  rollback_schema_min:2,rollback_schema_max:2,release_notes:'Verified notes'};
const context={Option,Set,Date,AbortSignal,CustomEvent:class{},
  document:{getElementById:element,dispatchEvent(){}},
  PhotoframeUpdates:{paintBadge(){},relativeCheck(){return 'Just now';},releaseNotes(el,text){el.textContent=text;}},
  setTimeout(callback){nextPoll=callback;},clearTimeout(){},
  fetch:async(path,options={})=>{
    const body=options.body?JSON.parse(options.body):null;calls.push({path,body});
    if(path.endsWith('/status'))return{ok:true,json:async()=>({managed:true,phase:'idle',channel,
      release_selection_supported:modern,release_selection_reason:modern?'':'Administrator helper upgrade required',
      latest_manifest:{version:'10.0.0',release_notes:'Latest notes'}})};
    if(path.endsWith('/session'))return{ok:true,json:async()=>({csrf:'local'})};
    if(path.endsWith('/catalog'))return{ok:true,json:async()=>({channel,releases:[
      {version:'9.0.0',available:true,published_at:'2026-10-25T12:00:00Z',compatibility:'Not verified'},
      {version:'1.0.0',available:false,published_at:null,compatibility:'Not newer than running version'}]})};
    if(path.endsWith('/inspect'))return{ok:true,json:async()=>({channel,manifest:verified})};
    if(path.endsWith('/preferences'))channel=body.channel;
    return{ok:true,json:async()=>({phase:'idle'})};
  }};
(async()=>{
  vm.runInNewContext(fs.readFileSync(SCRIPT,'utf8'),context);
  await new Promise(setImmediate);
  await element('load-releases').onclick();
  assert.equal(element('release-choice').children[2].disabled,true);
  element('release-choice').value='9.0.0';element('release-choice').onchange();
  assert.equal(element('stage-selected-release').disabled,true);
  await element('inspect-release').onclick();
  assert.equal(element('stage-selected-release').disabled,false);
  assert.equal(element('selected-release-notes').textContent,'Verified notes');
  await nextPoll();
  assert.equal(element('release-choice').value,'9.0.0');
  assert.equal(element('stage-selected-release').disabled,false);
  await element('stage-selected-release').onclick();await new Promise(setImmediate);
  assert.equal(calls.find(c=>c.path.endsWith('/stage-selected')).body.release,'9.0.0');
  element('update-channel').value='develop';await element('update-channel').onchange();
  await new Promise(setImmediate);
  assert.equal(element('stage-selected-release').disabled,true);
  assert.equal(element('release-choice').value,'');
  modern=false;await nextPoll();
  assert.equal(element('inspect-release').disabled,true);
  assert.equal(element('stage-selected-release').disabled,true);
  assert.ok(element('release-picker-reason').textContent.includes('Administrator'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace("SCRIPT", json.dumps(str(script)))
    subprocess.run([node, "-e", scenario], check=True)
