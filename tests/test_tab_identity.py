import json
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
