from pathlib import Path
import re
import subprocess
import pytest
REPO=Path(__file__).resolve().parent.parent

def read(path):
    return (REPO/path).read_text(encoding="utf-8")

def test_popup_file_routes_and_media_query_semantics_behave():
    boot = read("static/boot.js")
    block = boot[boot.index("let _attachChoiceOpen"):boot.index("// ── Voice input")]
    touch = boot[boot.index("function _hasFinePointerCoexisting"):boot.index("function _isNumpadEnter")]
    block = touch + "\n" + block
    script = f"""
const assert = require('node:assert/strict');
const state = {{ width: 400, coarse: true, fine: false }};
class Element {{
  constructor(id) {{ this.id=id; this.attrs={{}}; this.listeners={{}}; this.hidden=true; this.value=''; this.dataset={{}}; this.focused=false; this.clicks=0; }}
  setAttribute(k,v) {{ this.attrs[k]=String(v); }}
  removeAttribute(k) {{ delete this.attrs[k]; }}
  getAttribute(k) {{ return this.attrs[k] ?? null; }}
  addEventListener(k,fn) {{ this.listeners[k]=fn; }}
  removeEventListener(k,fn) {{ if(this.listeners[k]===fn) delete this.listeners[k]; }}
  focus() {{ this.focused=true; document.activeElement=this; }}
  click() {{ this.clicks++; }}
  querySelector() {{ return choice; }}
  querySelectorAll(selector) {{ return selector.includes('[data-attach-choice]') ? [choice,files].filter(item=>item.dataset.attachChoice&&!item.getAttribute('disabled')) : []; }}
  contains(target) {{ return target===choice||target===files; }}
  closest(selector) {{
    if (this.id === 'choice' && selector.includes('[data-attach-choice]')) return this;
    if (this.id === 'popup' && selector.includes('#attachChoicePopup')) return this;
    if (this.id === 'attach' && selector.includes('#btnAttach')) return this;
    return null;
  }}
}}
const attach = new Element('attach');
const popup = new Element('popup');
const choice = new Element('choice'); choice.dataset.attachChoice='camera';
const files = new Element('files'); files.dataset.attachChoice='files';
const camera = new Element('camera');
const elements = {{ btnAttach:attach, attachChoicePopup:popup, fileInput:files, cameraFileInput:camera }};
const document = {{ listeners:{{}}, activeElement:null, addEventListener(k,fn){{this.listeners[k]=fn;}} }};
const mediaQueries = {{}};
const window = {{ listeners:{{}}, addEventListener(k,fn){{this.listeners[k]=fn;}}, matchMedia(q){{
  if(mediaQueries[q]) return mediaQueries[q];
  const query = {{
    get matches() {{ return q.includes('max-width: 640px') ? state.width <= 640 : q.includes('pointer:coarse') ? state.coarse : q.includes('any-pointer:fine') ? state.fine : false; }},
    listeners:[],
    addEventListener(k,fn) {{ if(k==='change') this.listeners.push(fn); }},
  }};
  mediaQueries[q] = query;
  return query;
}} }};
globalThis.document=document; globalThis.window=window;
globalThis.matchMedia=(q)=>window.matchMedia(q);
globalThis.$=(id)=>elements[id];
Object.defineProperty(globalThis,'navigator',{{value:{{userAgent:''}},configurable:true}});
globalThis.S={{ pendingFiles:[] }};
globalThis.addFiles=(items)=>{{ S.pendingFiles.push(...items); globalThis.admitted=items; }};
globalThis.console={{ log(){{}} }};
const frames=[]; globalThis.setTimeout=fn=>frames.push(fn);
const notifyCapabilityChange=()=>Object.values(mediaQueries).forEach(query=>query.listeners.forEach(fn=>fn()));
eval({block!r});
assert.equal(attach.getAttribute('aria-haspopup'),'menu');
attach.onclick({{preventDefault(){{}}}});
assert.equal(popup.hidden,false); assert.equal(attach.getAttribute('aria-expanded'),'true');
attach.listeners.pointerdown();
popup.listeners.focusout({{currentTarget:popup,relatedTarget:attach}});
assert.equal(popup.hidden,false);
attach.onclick({{preventDefault(){{}}}});
assert.equal(popup.hidden,true);
attach.onclick({{preventDefault(){{}}}});
attach.listeners.pointerdown();
popup.listeners.focusout({{currentTarget:popup,relatedTarget:null}});
assert.equal(popup.hidden,false);
attach.onclick({{preventDefault(){{}}}});
assert.equal(popup.hidden,true);
attach.onclick({{preventDefault(){{}}}});
assert.equal(popup.hidden,false);
_handleAttachChoiceKeydown({{key:'ArrowDown',preventDefault(){{}}}});
assert.equal(document.activeElement,files);
_handleAttachChoiceKeydown({{key:'ArrowUp',preventDefault(){{}}}});
assert.equal(document.activeElement,choice);
_handleAttachChoiceKeydown({{key:'ArrowDown',preventDefault(){{}}}});
assert.equal(document.activeElement,files);
_handleAttachChoiceKeydown({{key:'Home',preventDefault(){{}}}});
assert.equal(document.activeElement,choice);
_handleAttachChoiceKeydown({{key:'End',preventDefault(){{}}}});
assert.equal(document.activeElement,files);
state.width=1280; state.coarse=false; state.fine=true; notifyCapabilityChange();
assert.equal(popup.hidden,true); assert.equal(attach.getAttribute('aria-expanded'),null);
assert.equal(attach.getAttribute('aria-controls'),null);
assert.equal(attach.focused,true);
state.width=400; state.coarse=true; state.fine=false; notifyCapabilityChange();
attach.onclick({{preventDefault(){{}}}});
assert.equal(popup.hidden,false); assert.equal(attach.getAttribute('aria-expanded'),'true');
state.fine=true; mediaQueries['(any-pointer:fine)'].listeners.forEach(fn=>fn());
assert.equal(popup.hidden,true); assert.equal(attach.getAttribute('aria-expanded'),null);
state.fine=false; notifyCapabilityChange();
attach.focused=false;
attach.onclick({{preventDefault(){{}}}});
popup.listeners.focusout({{currentTarget:popup,relatedTarget:choice}}); assert.equal(popup.hidden,false);
popup.listeners.focusout({{currentTarget:popup,relatedTarget:files}}); assert.equal(popup.hidden,false);
popup.listeners.focusout({{currentTarget:popup,relatedTarget:attach}}); assert.equal(popup.hidden,true);
attach.onclick({{preventDefault(){{}}}});
popup.listeners.focusout({{currentTarget:popup,relatedTarget:new Element('outside')}}); assert.equal(popup.hidden,true);
state.width=844; notifyCapabilityChange(); attach.onclick({{preventDefault(){{}}}}); assert.equal(popup.hidden,false);
popup.listeners.click({{target:choice}});
assert.equal(attach.focused,false); assert.equal(camera.clicks,1);
camera.listeners.change(); assert.equal(attach.focused,false); frames.shift()(); assert.equal(attach.focused,true);
assert.equal(camera.listeners.cancel,undefined);
attach.focused=false; attach.onclick({{preventDefault(){{}}}}); popup.listeners.click({{target:choice}});
camera.listeners.cancel(); assert.equal(attach.focused,false); frames.shift()(); assert.equal(attach.focused,true);
assert.equal(camera.listeners.change,undefined); assert.equal(popup.hidden,true); assert.equal(attach.getAttribute('aria-expanded'),'false');
state.width=1280; state.coarse=false; state.fine=true; notifyCapabilityChange();
assert.equal(attach.getAttribute('aria-haspopup'),null);
assert.equal(attach.getAttribute('aria-expanded'),null);
assert.equal(attach.getAttribute('aria-controls'),null);
attach.onclick({{preventDefault(){{}}}}); assert.equal(files.clicks,1);
state.width=844; state.coarse=true; state.fine=true; navigator.userAgent='iPhone'; notifyCapabilityChange();
attach.onclick({{preventDefault(){{}}}}); assert.equal(popup.hidden,false);
assert.equal(attach.getAttribute('aria-haspopup'),'menu');
const first=new File(['x'],'image.jpg',{{type:'image/jpeg'}});
const second=new File(['y'],'image.jpg',{{type:'image/jpeg'}});
_handleCameraFiles([first,second]);
assert.deepEqual(admitted.map(file=>file.name),['image.jpg','image (1).jpg']);
"""
    result = subprocess.run(["node", "--eval", script], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

def _js_function_body(source, function_name):
    """Return a JavaScript function body using balanced braces."""
    match = re.search(rf'function\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{', source)
    if not match:
        raise AssertionError(f"Missing JavaScript function {function_name}()")
    open_brace = match.end() - 1
    depth = 0
    for idx in range(open_brace, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1:idx]
    raise AssertionError(f"Could not parse JavaScript function {function_name}()")

def test_resize_only_closes_dropdowns_when_phone_boundary_crossed():
    """Executed listener harness: composer dropdown state must reset ONLY when
    the phone/desktop (640px) boundary is crossed.

    Drives the real registration + handler code from static/ui.js under node
    with a fake MediaQueryList: repeated same-mode resizes must produce zero
    closes, the first boundary change after load must still deliver (no
    uninitialized-state miss), and both directions must reset the composer
    menu family exactly once per crossing.
    """
    import json
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node is not available for executing the JS listener harness")

    ui_js = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
    # Extract the registration block: from the _phoneWidthQuery declaration
    # through the closing brace of the addEventListener/addListener if-chain
    # (the block ends at the "\n}\n" that closes the outer if after the
    # addListener fallback line).
    start = ui_js.index("const _phoneWidthQuery=")
    tail = ui_js.index("addListener(_onPhoneBoundaryChange);", start)
    end = ui_js.index("\n}", tail) + 2
    block = ui_js[start:end]

    script = """
// ── fake close functions with call counts ──
const calls = { mobile: 0, model: 0, reasoning: 0, ws: 0 };
function closeMobileComposerConfig(){ calls.mobile++; }
function closeModelDropdown(){ calls.model++; }
function closeReasoningDropdown(){ calls.reasoning++; }
// closeWsDropdown must be undefined -> exercises the typeof guard, counts via ws
// ── fake MediaQueryList ──
let _matches = false; // page loads at 804px -> not phone
const listeners = [];
const mql = {
  get matches(){ return _matches; },
  addEventListener: (type, fn) => { if (type === 'change') listeners.push(fn); },
};
globalThis.window = { matchMedia: (q) => mql };
// ── the real registration block from static/ui.js ──
__BLOCK__
function fireBoundary(){
  _matches = !_matches;
  for (const fn of listeners) fn({ matches: _matches });
}
// ── scenario ──
// 1. keyboard-style resizes at constant 804px: no boundary change, zero closes
  ;(function resizeBurst(){ })();
// (resize events are not even observable to this listener — nothing to fire)
const before = JSON.stringify(calls);
// 2. FIRST boundary change after load (no prior resize): must deliver
fireBoundary(); // 804 -> 640 (fold)
const afterFold = JSON.stringify(calls);
// 3. same-mode events must never fire the listener (guard: listeners only run on change)
// 4. second crossing (unfold)
fireBoundary(); // 640 -> 804
const afterUnfold = JSON.stringify(calls);
// 5. third crossing (fold again)
fireBoundary();
const afterThird = JSON.stringify(calls);
console.log(JSON.stringify({ before, afterFold, afterUnfold, afterThird, listeners: listeners.length }));
""".replace("__BLOCK__", block)

    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node failed: {r.stderr}"
    st = json.loads(r.stdout.strip())
    # exactly one change listener registered
    assert st["listeners"] == 1, "exactly one boundary-change listener must be registered"
    # no closes before any boundary change (keyboard resizes are invisible)
    assert st["before"] == '{"mobile":0,"model":0,"reasoning":0,"ws":0}'
    # first crossing after load: every close runs exactly once (no missed first transition)
    assert st["afterFold"] == '{"mobile":1,"model":1,"reasoning":1,"ws":0}', \
        "first fold must close mobile config, model, and reasoning dropdowns once each"
    # ws stays 0: closeWsDropdown is undefined, so the typeof guard must skip it
    # both directions close
    assert st["afterUnfold"] == '{"mobile":2,"model":2,"reasoning":2,"ws":0}', \
        "unfold must also reset the composer menu family (both-directions policy)"
    assert st["afterThird"] == '{"mobile":3,"model":3,"reasoning":3,"ws":0}'

def test_burger_mode_transition_closes_composer_menus():
    """Executed fit harness: cf-burger transitions (which can happen at a
    CONSTANT width, e.g. 760px, with no 640px boundary crossing) must close
    the composer menu family exactly once per real transition, and no-op fit
    passes (unchanged stage) must produce zero closes.

    Drives the real _fitComposerFooter under node with a fake footer/left
    pair whose clientWidth/scrollWidth are controlled per scenario — always
    above the 640px phone boundary, so the MediaQueryList path is never
    involved and the burger transition is the only trigger.
    """
    import json
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node is not available for executing the JS fit harness")

    ui_js = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
    fit_fn = "function _fitComposerFooter(){" + _js_function_body(ui_js, "_fitComposerFooter") + "}"
    boundary_fn = "function _onPhoneBoundaryChange(){" + _js_function_body(ui_js, "_onPhoneBoundaryChange") + "}"

    script = """
// ── fake close functions with call counts ──
const calls = { mobile: 0, model: 0, reasoning: 0 };
function closeMobileComposerConfig(){ calls.mobile++; }
function closeModelDropdown(){ calls.model++; }
function closeReasoningDropdown(){ calls.reasoning++; }

__FIT_FN__
__BOUNDARY_FN__

// ── fake DOM: footer + left with controlled geometry ──
const classes = new Set();
let clientW = 400, scrollW = 300; // start NON-burger (no overflow)
const footer = {
  style: {},
  classList: {
    contains: (c) => classes.has(c),
    remove: (...cs) => cs.forEach(c => classes.delete(c)),
    add: (...cs) => cs.forEach(c => classes.add(c)),
    toggle: (c, force) => { if (force) classes.add(c); else classes.delete(c); },
  },
  getBoundingClientRect: () => ({ height: 42 }),
  querySelector: (sel) => (sel === '.composer-left' ? left : null),
  _classes: classes,
};
const left = {
  get clientWidth(){ return clientW; },
  get scrollWidth(){ return scrollW; },
};
globalThis.document = { querySelector: (sel) => (sel === '.composer-footer' ? footer : null) };

// scenario — everything above the 640px boundary:
// 1. baseline fit at no-overflow: no transition, zero closes
_fitComposerFooter();
const afterBaseline = JSON.stringify(calls);
// 2. no-op refit (same geometry): zero closes
_fitComposerFooter();
const afterNoop = JSON.stringify(calls);
// 3. content grows -> overflow -> burger transition: one cleanup
scrollW = 700; // now overflows at cf-icons AND cf-burger stage
_fitComposerFooter();
const afterEnterBurger = JSON.stringify(calls) + " burger=" + classes.has('cf-burger');
// 4. refit while still burger: zero additional closes
_fitComposerFooter();
const afterStayBurger = JSON.stringify(calls);
// 5. content shrinks -> leave burger: one more cleanup
scrollW = 300;
_fitComposerFooter();
const afterLeaveBurger = JSON.stringify(calls) + " burger=" + classes.has('cf-burger');
console.log(JSON.stringify({ afterBaseline, afterNoop, afterEnterBurger, afterStayBurger, afterLeaveBurger }));
""".replace("__FIT_FN__", fit_fn).replace("__BOUNDARY_FN__", boundary_fn)

    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"node failed: {r.stderr}"
    st = json.loads(r.stdout.strip())
    # baseline + no-op refit: zero closes (unchanged stage is harmless)
    assert st["afterBaseline"] == '{"mobile":0,"model":0,"reasoning":0}'
    assert st["afterNoop"] == '{"mobile":0,"model":0,"reasoning":0}'
    # entering burger: exactly one cleanup of the composer menu family
    assert st["afterEnterBurger"] == '{"mobile":1,"model":1,"reasoning":1} burger=true', \
        "entering burger mode must close the composer menu family exactly once"
    # staying burger: no additional closes
    assert st["afterStayBurger"] == '{"mobile":1,"model":1,"reasoning":1}', \
        "unchanged-geometry refits must not re-fire the cleanup"
    # leaving burger: exactly one more cleanup (both-directions policy)
    assert st["afterLeaveBurger"] == '{"mobile":2,"model":2,"reasoning":2} burger=false', \
        "leaving burger mode must also close the composer menu family exactly once"
