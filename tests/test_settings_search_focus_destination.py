"""Search activation hands focus to the destination without changing its value.

The integrated Chromium gate also exercises native Enter on a real checkbox.
These bounded Node cases cover focus fallbacks/cleanup without a browser fixture.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("mode", ["control", "hidden_first", "inert_first", "fallback", "existing_tabindex", "missing"])
def test_search_navigation_focuses_destination_without_activation(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required")
    source = (Path(__file__).resolve().parents[1] / "static/panels.js").read_text()
    source = source.split("function _navigateToSettingsField(entry) {", 1)[1].split("\nfunction _resolveSettingsField(entry)", 1)[0]
    script = r'''
const assert = require('node:assert/strict');
const mode = MODE;
let active = null, activated = 0, blur = null, highlighted = false, switched = false;
const good = {getClientRects: () => [1], closest: () => null,
  focus: opts => {assert.equal(opts.preventScroll, true); active = good;},
  click: () => activated++};
const hidden = {getClientRects: () => [], closest: () => null, focus: () => {throw Error('hidden focus');}};
const inert = {getClientRects: () => [1], closest: () => ({}), focus: () => {throw Error('inert focus');}};
const attrs = new Map(mode === 'existing_tabindex' ? [['tabindex', '0']] : []);
const field = {scrollIntoView: opts => assert.equal(opts.block, 'center'), matches: () => false,
  querySelectorAll: selector => {
    assert(selector.includes(':not(:disabled)'));
    assert(selector.includes(':not([type="hidden"])'));
    return mode === 'control' ? [good] : mode === 'hidden_first' ? [hidden, good] :
      mode === 'inert_first' ? [inert, good] : [];
  },
  hasAttribute: name => attrs.has(name), setAttribute: (name,value) => attrs.set(name,value),
  removeAttribute: name => attrs.delete(name), addEventListener: (event,cb,opts) => {
    assert.equal(event,'blur'); assert.equal(opts.once,true); blur=cb;
  }, focus: opts => {assert.equal(opts.preventScroll,true); active=field;}};
function switchSettingsSection(section,opts){assert.equal(section,'appearance');assert.equal(opts.skipLazyLoad,true);switched=true;}
function requestAnimationFrame(cb){cb();}
function _resolveSettingsField(){return mode === 'missing' ? null : field;}
function _highlightSettingsField(el){assert.equal(el,field);highlighted=true;}
FUNCTION
_navigateToSettingsField({sectionKey:'appearance'});
assert.equal(switched,true);
assert.equal(activated,0);
if(mode === 'missing'){assert.equal(active,null);assert.equal(highlighted,false);}
else {
 assert.equal(highlighted,true);
 if(['control','hidden_first','inert_first'].includes(mode)){assert.equal(active,good);assert.equal(attrs.has('tabindex'),false);}
 else {assert.equal(active,field);if(mode==='fallback'){assert.equal(attrs.get('tabindex'),'-1');blur();assert.equal(attrs.has('tabindex'),false);}else{assert.equal(attrs.get('tabindex'),'0');assert.equal(blur,null);}}
}
'''.replace("MODE", json.dumps(mode)).replace("FUNCTION", "function _navigateToSettingsField(entry) {" + source)
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
