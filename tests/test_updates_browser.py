"""Release notes stay inert while useful Markdown and truthful status are rendered."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_release_notes_and_update_status():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    script = Path("src/photoframe/static/update-status.js").resolve()
    scenario = r"""
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.value = ''; }
  append(node) { this.children.push(node); }
  replaceChildren() { this.children = []; this.value = ''; }
  set textContent(value) { this.value = value; this.children = []; }
  get textContent() { return this.value + this.children.map(node => node.textContent).join(''); }
  set innerHTML(value) { throw Error('Raw HTML must never be used'); }
}
const context = {URL, document: {
  querySelector() {return null;},
  createElement(tag) {return new Element(tag);},
  createTextNode(text) {const node = new Element('#text'); node.textContent = text; return node;}
}};
vm.runInNewContext(fs.readFileSync(SCRIPT, 'utf8'), context);
const ui = context.PhotoframeUpdates, root = new Element('div');
const source = '# Changes\n\nA **safer** update with `code`.\n\n- First fix\n- Second fix\n\n'
  + '1. Install\n2. Restart\n\n[Details](https://example.com/notes)\n'
  + '<img src=x onerror=alert(1)>\n[Unsafe](javascript:alert(1))\n'
  + '[Encoded](java%73cript:alert)\n[Data](data:text/html,test)\n\n'
  + '```html\n<script>alert(1)</script>\n```';
ui.releaseNotes(root, source);
function all(node) {return [node, ...node.children.flatMap(all)];}
const nodes = all(root);
assert.equal(nodes.filter(node => node.tagName === 'A').length, 1);
assert.equal(nodes.find(node => node.tagName === 'A').href, 'https://example.com/notes');
assert.equal(nodes.find(node => node.tagName === 'STRONG').textContent, 'safer');
assert.equal(nodes.find(node => node.tagName === 'H3').textContent, 'Changes');
assert.equal(nodes.find(node => node.tagName === 'UL').children.length, 2);
assert.equal(nodes.find(node => node.tagName === 'OL').children.length, 2);
assert.equal(nodes.filter(node => ['SCRIPT','IMG'].includes(node.tagName)).length, 0);
assert.ok(root.textContent.includes('<img src=x onerror=alert(1)>'));
assert.ok(nodes.find(node => node.tagName === 'PRE').textContent.includes('<script>'));
ui.releaseNotes(root, 'Replacement');
assert.equal(root.textContent, 'Replacement');
const managed = {managed:true,phase:'idle'};
assert.equal(ui.presentation(managed).label, 'Not checked yet');
assert.equal(ui.presentation({...managed,last_check:10}).label, 'Not checked yet');
assert.equal(ui.presentation({...managed,latest_manifest:{version:'1.0'}}).label, 'Up to date');
assert.equal(ui.presentation({...managed,upgrade_available:true}).label, 'Update available');
assert.equal(ui.presentation({...managed,staged_version:'1.1'}).label, 'Ready to install');
assert.equal(ui.presentation({...managed,phase:'checking',upgrade_available:true}).label, 'Checking for updates');
assert.equal(ui.presentation({...managed,check_error:'Network error',latest_manifest:{}}).label, 'Check failed');
assert.equal(ui.presentation({...managed,phase:'failed',staged_version:'1.1'}).label, 'Update failed');
assert.equal(ui.presentation({managed:false}).label, 'Manual updates');
assert.equal(ui.relativeCheck(null), 'Never checked');
assert.equal(ui.relativeCheck(100,100000), 'Just now');
assert.equal(ui.relativeCheck(100,160000), '1 minute ago');
assert.equal(ui.relativeCheck(100,7300000), '2 hours ago');
assert.equal(ui.relativeCheck(100,86500000), '1 day ago');
assert.equal(ui.relativeCheck(200,100000), 'Just now');
""".replace("SCRIPT", json.dumps(str(script)))
    subprocess.run([node, "-e", scenario], check=True)
