"""Regressions for the simpler navigation, theme switch and activity tray."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).parents[1]


def test_settings_and_updates_share_the_same_primary_destination() -> None:
    environment = Environment(
        loader=FileSystemLoader(ROOT / "src/photoframe/templates"),
        autoescape=select_autoescape(["html"]),
    )
    environment.globals["url_for"] = lambda _route, path: f"/static{path}"
    for section in ("provider", "updates"):
        html = environment.get_template("index.html").render(
            settings_section=section,
            settings_title="Photo provider" if section == "provider" else "Software updates",
            version="2.0.0.dev2",
        )
        assert html.count("data-theme-toggle") == 2  # One control, one JS selector.
        assert html.count('data-nav-key="') == 3
        assert 'data-nav-key="settings" aria-current="page"' in html
        assert 'data-nav-key="updates"' not in html
        assert 'href="/settings/updates"' in html
        assert "data-theme-choice" not in html
        assert "data-cycle-theme" not in html


def test_single_theme_toggle_updates_the_theme_and_accessible_state() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    template = (ROOT / "src/photoframe/templates/index.html").read_text()
    function = re.search(r"function setTheme\(theme\) \{.*?\n    \}", template, re.DOTALL)
    assert function is not None
    scenario = f"""
const vm = require('node:vm');
const toggle = {{ setAttribute(name, value) {{ this[name] = value; }} }};
const meta = {{content: ''}};
let saved;
global.document = {{
  documentElement: {{dataset: {{}}}},
  querySelector(selector) {{ return selector === '[data-theme-toggle]' ? toggle : meta; }},
}};
global.window = {{localStorage: {{setItem(key, value) {{ saved = [key, value]; }}}}}};
vm.runInThisContext({json.dumps(function[0])});
setTheme('dark');
if (document.documentElement.dataset.theme !== 'photoframe-dark') throw Error('dark theme missing');
if (toggle['aria-pressed'] !== 'true') throw Error('dark state missing');
if (meta.content !== '#1c2721') throw Error('dark browser theme missing');
setTheme('light');
if (document.documentElement.dataset.theme !== 'photoframe-light') throw Error('light theme missing');
if (toggle['aria-pressed'] !== 'false') throw Error('light state missing');
if (saved.join(':') !== 'photoframe-theme:light') throw Error('theme preference not stored');
"""
    subprocess.run([node, "-e", scenario], check=True)


def test_idle_frame_has_no_hovering_tray_but_active_render_does() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    script = ROOT / "src/photoframe/static/activity.js"
    scenario = f"""
const vm = require('node:vm');
const fs = require('node:fs');
const listeners = {{}};
const child = () => ({{hidden: true, textContent: '', removeAttribute() {{}}}});
const nodes = Object.fromEntries(['[data-activity-title]', '[data-activity-detail]', 'progress',
  '[data-activity-dismiss]', '[data-activity-cancel]', '[data-context-action]'].map(key => [key, child()]));
Object.values(nodes).forEach(node => node.addEventListener = () => {{}});
const dock = {{hidden: true, classList: {{toggle() {{}}}}, querySelector: key => nodes[key]}};
global.document = {{
  getElementById: key => key === 'activity-dock' ? dock : null,
  querySelector: () => null,
  body: {{classList: {{add() {{}}, remove() {{}}}}}},
  addEventListener: (name, callback) => listeners[name] = callback,
}};
global.window = {{sessionStorage: {{getItem: () => null}}}};
global.setAlbumBusy = () => {{}};
global.currentRenderIntent = () => null;
global.setTimeout = () => 1;
global.clearTimeout = () => {{}};
let state = {{album: {{}}, render: {{phase: 'idle', active: false}}}};
global.fetch = async () => ({{ok: true, json: async () => state}});
vm.runInThisContext(fs.readFileSync({json.dumps(str(script))}, 'utf8'));
(async () => {{
  await new Promise(setImmediate);
  if (!dock.hidden) throw Error('idle frame has a hovering tray');
  state = {{album: {{}}, render: {{phase: 'preparing', active: true}}}};
  listeners.visibilitychange();
  await new Promise(setImmediate);
  if (dock.hidden) throw Error('active render progress is hidden');
  if (nodes['[data-activity-title]'].textContent !== 'Preparing photo') throw Error('wrong active state');
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    subprocess.run([node, "-e", scenario], check=True)


def test_photo_editor_opens_in_place_and_returns_focus_without_reopening() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    template = (ROOT / "src/photoframe/templates/index.html").read_text()
    controller = template.split("    let photoEditorViewState = null;", 1)[1].split(
        "    function syncInterface()", 1
    )[0]
    scenario = f"""
const vm = require('node:vm');
let focused = false, scrollY = 900, opened = 0, closeClick;
const listeners = {{}};
const trigger = {{focus(options) {{ focused = options.preventScroll; }}}};
const form = {{elements: {{photo_id: {{value: 'portrait'}}}}, closest: () => null,
  querySelector: () => trigger}};
let availableForms = [form];
const rail = {{scrollLeft: 0}};
const dialog = {{
  dataset: {{editorOpen: 'true'}}, open: false,
  addEventListener(name, callback) {{ listeners[name] = callback; }},
  querySelectorAll: () => [{{addEventListener(name, callback) {{ closeClick = callback; }}}}],
  showModal() {{ this.open = true; opened++; }},
  close() {{ this.open = false; listeners.close(); }},
}};
global.document = {{
  querySelector: selector => selector === '[data-photo-editor-dialog]' ? dialog : rail,
  querySelectorAll: () => availableForms,
}};
global.window = {{scrollTo(x, y) {{ scrollY = y; }}}};
vm.runInThisContext('let photoEditorViewState = {{photoId: "portrait", fromRail: false, pageY: 900, railX: 80}};'
  + {json.dumps(controller)});
setupPhotoEditorDialog();
if (!dialog.open || scrollY !== 900) throw Error('preview did not open at the current page position');
closeClick();
if (dialog.open || !focused || rail.scrollLeft !== 80 || scrollY !== 900) {{
  throw Error('closing the editor lost focus or scroll position');
}}
setupPhotoEditorDialog();
if (opened !== 1) throw Error('closed editor reopened after an unrelated interface sync');
// A hidden photo has no thumbnail/preview trigger; focus its local Restore action.
availableForms = [{{elements: {{photo_id: {{value: 'portrait'}}}}, closest: () => null,
  querySelector: () => trigger}}];
focused = false;
photoEditorViewState.fromRail = true;
restorePhotoEditorFocus();
if (!focused || scrollY !== 900) throw Error('hidden-photo recovery lost the local action');
"""
    subprocess.run([node, "-e", scenario], check=True)
