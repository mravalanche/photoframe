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
const makeElement=()=>({hidden:false,disabled:false,dataset:{},value:'',textContent:'',children:[],
  classes:new Set(['badge','badge-neutral']),
  get classList(){return {add:(name)=>this.classes.add(name),remove:(name)=>this.classes.delete(name),
    contains:(name)=>this.classes.has(name)};},
  addEventListener(){},removeAttribute(){},append(...children){this.children.push(...children);},
  replaceChildren(...children){this.children=children;this.value=children[0]?.value||'';},
  cloneNode(){const result=makeElement();result.classes=new Set(this.classes);return result;}});
const element=id=>{
  if(!elements.has(id))elements.set(id,makeElement());
  return elements.get(id);
};
element('release-badge-template').content={firstElementChild:makeElement()};
const summary=()=>element('release-choice-summary').children;
const badge=label=>summary().find(child=>child.textContent===label);
const select=version=>{element('release-choice').value=version;element('release-choice').onchange();};
let nextPoll,modern=true,channel='stable',rejectInspection=false,catalogMode='default';
const verified={version:'9.0.0',updater_protocol:1,settings_schema_min:2,settings_schema_max:2,
  rollback_schema_min:2,rollback_schema_max:2,release_notes:'Verified notes'};
const context={Option,Set,Date,AbortSignal,CustomEvent:class{},
  document:{getElementById:element,createElement:makeElement,dispatchEvent(){}},
  PhotoframeUpdates:{paintBadge(){},relativeCheck(){return 'Just now';},releaseNotes(el,text){el.textContent=text;}},
  setTimeout(callback){nextPoll=callback;},clearTimeout(){},
  fetch:async(path,options={})=>{
    const body=options.body?JSON.parse(options.body):null;calls.push({path,body});
    if(path.endsWith('/status'))return{ok:true,json:async()=>({managed:true,phase:'idle',channel,
      release_selection_supported:modern,release_selection_reason:modern?'':'Administrator helper upgrade required',
      latest_manifest:{version:'10.0.0',release_notes:'Latest notes'}})};
    if(path.endsWith('/session'))return{ok:true,json:async()=>({csrf:'local'})};
    if(path.endsWith('/catalog')) {
      const releases=[
      {version:'9.1.0',channel,relationship:'newer',bundle_available:false,available:false,
        reason:'Signed bundle is not published',published_at:null},
      {version:'9.0.0',channel,relationship:'newer',bundle_available:true,available:true,
        published_at:'2026-10-25T12:00:00Z',compatibility:'Not verified'},
      {version:'1.0.0',channel,relationship:'older',bundle_available:true,available:false,
        reason:'Not newer than the running version',published_at:null},
      {version:'8.0.0',channel,relationship:'installed',bundle_available:true,available:false,
        reason:'Not newer than the running version',published_at:null},
      ];
      return {ok:catalogMode!=='error',json:async()=>({channel,message:'Release listing unavailable',
        releases:catalogMode==='empty'?[]:catalogMode==='older'?releases.filter(r=>r.relationship==='older')
          :catalogMode==='unavailable'?releases.filter(r=>!r.available):releases})};
    }
    if(path.endsWith('/inspect'))return{ok:!rejectInspection,json:async()=>rejectInspection
      ? {message:'Signature verification failed'} : {channel,manifest:verified}};
    if(path.endsWith('/preferences'))channel=body.channel;
    return{ok:true,json:async()=>({phase:'idle'})};
  }};
(async()=>{
  vm.runInNewContext(fs.readFileSync(SCRIPT,'utf8'),context);
  await new Promise(setImmediate);
  await element('load-releases').onclick();
  assert.equal(element('release-choice').value,'9.0.0');
  assert.ok(element('release-choice').children.every(option=>option.value));
  assert.equal(element('release-choice').children.find(option=>option.value==='1.0.0').disabled,false);
  assert.ok(element('release-choice').children.find(option=>option.value==='9.0.0').textContent.includes('Stable · Newer'));
  assert.ok(element('release-choice').children.find(option=>option.value==='1.0.0').textContent.includes('Stable · Older'));
  assert.ok(badge('Stable').classList.contains('badge-success'));
  assert.ok(badge('Newer').classList.contains('badge-primary'));
  assert.equal(badge('Verified'),undefined);
  assert.equal(element('inspect-release').disabled,false);
  assert.equal(element('stage-selected-release').disabled,true);
  assert.equal(calls.some(call=>call.path.endsWith('/inspect')||call.path.endsWith('/stage-selected')),false);
  select('1.0.0');
  assert.ok(badge('Older').classList.contains('badge-neutral'));
  assert.equal(element('inspect-release').disabled,true);
  assert.equal(element('stage-selected-release').disabled,true);
  assert.equal(element('selected-release-detail').textContent,'Not newer than the running version');
  select('8.0.0');
  assert.ok(badge('Installed').classList.contains('badge-neutral'));
  assert.equal(element('inspect-release').disabled,true);
  select('9.1.0');
  assert.ok(badge('Newer').classList.contains('badge-primary'));
  assert.ok(badge('Bundle unavailable').classList.contains('badge-warning'));
  assert.equal(element('inspect-release').disabled,true);
  select('9.0.0');
  assert.ok(badge('Stable').classList.contains('badge-success'));
  assert.ok(badge('Newer').classList.contains('badge-primary'));
  assert.equal(badge('Verified'),undefined);
  assert.equal(element('stage-selected-release').disabled,true);
  await element('inspect-release').onclick();
  assert.equal(element('stage-selected-release').disabled,false);
  assert.equal(element('selected-release-notes').textContent,'Verified notes');
  assert.ok(badge('Verified').classList.contains('badge-success'));
  await nextPoll();
  assert.equal(element('release-choice').value,'9.0.0');
  assert.equal(element('stage-selected-release').disabled,false);
  await element('stage-selected-release').onclick();await new Promise(setImmediate);
  assert.equal(calls.find(c=>c.path.endsWith('/stage-selected')).body.release,'9.0.0');
  rejectInspection=true;await element('inspect-release').onclick();
  assert.equal(element('stage-selected-release').disabled,true);
  assert.equal(badge('Verified'),undefined);
  assert.ok(element('selected-release-detail').textContent.includes('verification failed'));
  rejectInspection=false;
  await element('inspect-release').onclick();
  assert.ok(badge('Verified'));
  catalogMode='error';await element('load-releases').onclick();
  assert.equal(element('release-choice-summary').hidden,true);
  assert.equal(element('release-choice').value,'');
  assert.equal(element('stage-selected-release').disabled,true);
  assert.ok(element('update-error').textContent.includes('listing unavailable'));
  catalogMode='unavailable';await element('load-releases').onclick();
  assert.equal(element('release-choice').value,'9.1.0');
  assert.ok(badge('Bundle unavailable'));
  assert.equal(element('inspect-release').disabled,true);
  assert.equal(element('stage-selected-release').disabled,true);
  catalogMode='older';await element('load-releases').onclick();
  assert.equal(element('release-choice').value,'1.0.0');
  assert.ok(badge('Older'));
  assert.equal(element('inspect-release').disabled,true);
  catalogMode='empty';await element('load-releases').onclick();
  assert.equal(element('release-choice').value,'');
  assert.equal(element('release-choice').disabled,true);
  assert.equal(element('release-choice-summary').hidden,true);
  assert.ok(element('selected-release-detail').textContent.includes('No canonical releases'));
  catalogMode='default';
  element('update-channel').value='develop';await element('update-channel').onchange();
  await new Promise(setImmediate);
  assert.equal(element('stage-selected-release').disabled,true);
  assert.equal(element('release-choice').value,'');
  assert.equal(element('release-choice-summary').hidden,true);
  await element('load-releases').onclick();select('9.0.0');
  assert.ok(badge('Development').classList.contains('badge-info'));
  assert.equal(badge('Verified'),undefined);
  modern=false;await nextPoll();
  assert.equal(element('inspect-release').disabled,true);
  assert.equal(element('stage-selected-release').disabled,true);
  assert.ok(element('release-picker-reason').textContent.includes('Administrator'));
})().catch(error=>{console.error(error);process.exitCode=1;});
""".replace("SCRIPT", json.dumps(str(script)))
    subprocess.run([node, "-e", scenario], check=True)
