"""The photo preview queues the latest choice and reports failures."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def test_preview_waits_for_first_image_and_shows_latest_choice() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    template = Path("src/photoframe/templates/index.html").read_text()
    inline_scripts = re.findall(r"<script>(.*?)</script>", template, flags=re.DOTALL)
    scenario = f"""
const vm = require('node:vm');
const documentMock = {{ addEventListener() {{}} }};
const timers = [];
const browser = {{
  PhotoframeTabIdentity: {{ createBrowserSession() {{
    return {{ currentIntent() {{}}, newIntent() {{}}, release() {{}}, reclaim() {{}} }};
  }} }},
  addEventListener() {{}},
  setTimeout(callback) {{ timers.push(callback); }},
}};
global.document = documentMock;
global.window = browser;
global.self = browser;
const requests = [];
global.Image = class {{
  constructor() {{ this.naturalWidth = 100; }}
  set src(value) {{ this.value = value; requests.push(this); }}
  get src() {{ return this.value; }}
}};
vm.runInThisContext({json.dumps(inline_scripts[1])});
const message = {{ textContent: '' }};
const retry = {{ hidden: true }};
const feedback = {{
  hidden: false,
  classList: {{ toggle() {{}} }},
  querySelector(selector) {{ return selector === '[data-preview-message]' ? message : retry; }},
}};
const container = {{ setAttribute() {{}} }};
const image = {{
  src: 'https://frame.test/photos/1/prepared?matte=white',
  naturalWidth: 100, alt: 'Photo',
  replaceWith(next) {{ this.replacement = next; }},
}};
const editor = {{
  isConnected: true,
  querySelector(selector) {{
    if (selector === '[data-preview-feedback]') return feedback;
    if (selector === '.selected-image') return container;
    throw new Error(`Unexpected selector: ${{selector}}`);
  }},
}};
const state = {{ editor, image, displayedUrl: image.src, initialLoading: false,
  loading: false, retries: 0, desiredUrl: 'https://frame.test/photos/1/prepared?matte=colour' }};
loadFramingPreview(state);
if (requests.length !== 1 || !state.loading) throw new Error('first preview did not start');
state.desiredUrl = 'https://frame.test/photos/1/prepared?matte=blur';
loadFramingPreview(state);
if (requests.length !== 1) throw new Error('preview requests overlapped');
requests[0].onload();
if (requests.length !== 2 || image.replacement) throw new Error('stale preview was shown');
requests[1].onload();
if (state.displayedUrl !== state.desiredUrl || !feedback.hidden) throw new Error('latest preview was not shown');
state.desiredUrl = 'https://frame.test/photos/1/prepared?matte=black';
loadFramingPreview(state);
requests[2].onerror();
if (retry.hidden === false || timers.length !== 1) throw new Error('transient failure was not retried');
for (let attempt = 0; attempt < 4; attempt += 1) {{
  timers.shift()();
  requests[requests.length - 1].onerror();
}}
if (retry.hidden || !message.textContent.includes('Try again')) throw new Error('preview failure was silent');
"""
    subprocess.run([node, "-e", scenario], check=True)
