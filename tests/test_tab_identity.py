import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")


def test_tab_identity_distinguishes_duplicate_from_reload() -> None:
    if NODE is None:
        pytest.skip("Node.js is unavailable")
    node = NODE
    script = Path("src/photoframe/static/tab-identity.js").resolve()
    scenario = f"""
const identity = require({json.dumps(str(script))});
class Storage {{
  constructor(copy) {{ this.values = new Map(copy ? copy.values : []); }}
  getItem(key) {{ return this.values.has(key) ? this.values.get(key) : null; }}
  setItem(key, value) {{ this.values.set(key, String(value)); }}
  removeItem(key) {{ this.values.delete(key); }}
}}
let sequence = 0;
const uuid = () => `00000000-0000-4000-8000-${{String(++sequence).padStart(12, '0')}}`;
const local = new Storage();
const originalStorage = new Storage();
const original = identity.createSession({{ localStorage: local, sessionStorage: originalStorage, randomUUID: uuid }});
const operation = original.newIntent();

const duplicateStorage = new Storage(originalStorage);
const duplicate = identity.createSession({{ localStorage: local, sessionStorage: duplicateStorage, randomUUID: uuid }});
if (duplicate.tabId === original.tabId) throw new Error('duplicate retained original tab id');
if (duplicate.currentIntent() !== null) throw new Error('duplicate retained render ownership');
if (original.currentIntent() !== operation) throw new Error('original lost render ownership');

original.release();
const reloaded = identity.createSession({{ localStorage: local, sessionStorage: originalStorage, randomUUID: uuid }});
if (reloaded.tabId !== original.tabId) throw new Error('ordinary reload changed tab id');
if (reloaded.currentIntent() !== operation) throw new Error('ordinary reload lost render ownership');

reloaded.release();
const replacementStorage = new Storage(originalStorage);
const replacement = identity.createSession({{ localStorage: local, sessionStorage: replacementStorage, randomUUID: uuid }});
reloaded.reclaim();
if (reloaded.tabId === original.tabId) throw new Error('restored page retained a reclaimed tab id');
if (reloaded.currentIntent() !== null) throw new Error('restored page retained copied ownership');
reloaded.release();
duplicate.release();
replacement.release();
"""
    subprocess.run([node, "-e", scenario], check=True)


def test_browser_session_survives_blocked_storage_and_crypto_access() -> None:
    if NODE is None:
        pytest.skip("Node.js is unavailable")
    node = NODE
    script = Path("src/photoframe/static/tab-identity.js").resolve()
    scenario = f"""
const identity = require({json.dumps(str(script))});
const blocked = {{
  get localStorage() {{ throw new DOMException('blocked', 'SecurityError'); }},
  get sessionStorage() {{ throw new DOMException('blocked', 'SecurityError'); }},
  get crypto() {{ throw new DOMException('blocked', 'SecurityError'); }},
}};
const session = identity.createBrowserSession(blocked);
const operation = session.newIntent();
if (!/^[0-9a-f]{{8}}-[0-9a-f]{{4}}-4[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$/.test(operation)) {{
  throw new Error('fallback did not create a valid v4 UUID');
}}
if (session.currentIntent() !== operation) throw new Error('volatile intent was not retained');
"""
    subprocess.run([node, "-e", scenario], check=True)


def test_page_initialization_continues_when_theme_storage_property_throws() -> None:
    if NODE is None:
        pytest.skip("Node.js is unavailable")
    node = NODE
    identity_script = Path("src/photoframe/static/tab-identity.js").resolve()
    template = Path("src/photoframe/templates/index.html").read_text()
    inline_scripts = re.findall(r"<script>(.*?)</script>", template, flags=re.DOTALL)
    assert len(inline_scripts) == 2
    scenario = f"""
const vm = require('node:vm');
const identity = require({json.dumps(str(identity_script))});
const documentListeners = {{}};
const windowListeners = {{}};
const themeButton = {{ dataset: {{ themeChoice: 'dark' }}, setAttribute(name, value) {{ this[name] = value; }} }};
const notice = {{ dataset: {{ autoDismiss: '6000' }} }};
const panel = {{
  open: false,
  dataset: {{ settingsPanel: 'display', defaultOpen: 'false' }},
  addEventListener(name, callback) {{ this[name] = callback; }},
}};
const documentMock = {{
  documentElement: {{ dataset: {{ theme: 'dark' }} }},
  addEventListener(name, callback) {{ documentListeners[name] = callback; }},
  querySelector() {{ return null; }},
  querySelectorAll(selector) {{
    if (selector === '[data-theme-choice]') return [themeButton];
    if (selector === '[data-notice][data-auto-dismiss]') return [notice];
    if (selector === '[data-settings-panel]') return [panel];
    return [];
  }},
}};
let uuidSequence = 0;
const browser = {{
  document: documentMock,
  PhotoframeTabIdentity: identity,
  crypto: {{ randomUUID: () => `00000000-0000-4000-8000-${{String(++uuidSequence).padStart(12, '0')}}` }},
  sessionStorage: {{ getItem() {{ return null; }}, setItem() {{}}, removeItem() {{}} }},
  addEventListener(name, callback) {{ windowListeners[name] = callback; }},
  setTimeout() {{ return 17; }},
  clearTimeout() {{}},
}};
Object.defineProperty(browser, 'localStorage', {{
  get() {{ throw new DOMException('blocked', 'SecurityError'); }},
}});
global.document = documentMock;
global.window = browser;
global.self = browser;
for (const source of {json.dumps(inline_scripts)}) vm.runInThisContext(source);
if (typeof documentListeners.DOMContentLoaded !== 'function') throw new Error('interface initializer missing');
documentListeners.DOMContentLoaded();
if (notice.dataset.dismissTimer !== '17') throw new Error('notification setup did not continue');
if (panel.dataset.accordionReady !== 'true') throw new Error('accordion setup did not continue');
if (typeof windowListeners.pagehide !== 'function' || typeof windowListeners.pageshow !== 'function') {{
  throw new Error('tab session setup did not continue');
}}
if (themeButton['aria-pressed'] !== 'true') throw new Error('theme controls were not synchronized');
"""
    subprocess.run([node, "-e", scenario], check=True)
