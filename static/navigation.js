// Presentation-only routes. Never activate a profile/workspace or run an item.
// Load after panels.js/sessions.js and before boot.js.
const _navigationPanels = new Set(['chat','settings','tasks','kanban','skills','memory','workspaces','profiles','todos','insights','logs']);
const _navigationSections = new Set(['conversation','appearance','preferences','providers','plugins','extensions','system','help']);
const _navigation = {ready:false, generation:0, restoring:null, index:0, accepted:null, rollback:false};

function navigationHasRoute() { return location.hash.startsWith('#panel='); }
function _navigationIdentifier(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 1024 && !/[\u0000-\u001f\u007f]/.test(value);
}
function _navigationParse() {
  const p = new URLSearchParams(navigationHasRoute() ? location.hash.slice(1) : '');
  const panel = _navigationPanels.has(p.get('panel')) ? p.get('panel') : 'chat';
  return {panel, section:panel === 'settings' && _navigationSections.has(p.get('section')) ? p.get('section') : 'conversation',
    item:_navigationIdentifier(p.get('item')) ? p.get('item') : '', scope:p.get('scope') || ''};
}
function navigationBegin(ticket) {
  if (ticket) return ticket;
  _navigation.restoring = null;
  return {generation:++_navigation.generation, profile:S.activeProfile || 'default',
    profileGeneration:typeof _profileSwitchGeneration === 'number' ? _profileSwitchGeneration : 0};
}
function navigationCurrent(ticket) {
  return ticket.generation === _navigation.generation && ticket.profile === (S.activeProfile || 'default')
    && ticket.profileGeneration === (typeof _profileSwitchGeneration === 'number' ? _profileSwitchGeneration : 0);
}
function _navigationNotice(key, fallback) {
  const text = typeof t === 'function' ? t(key) : '';
  if (typeof showToast === 'function') showToast(text && text !== key ? text : fallback, 4000);
}
function _navigationSnapshot() {
  const panel = _navigationPanels.has(_currentPanel) ? _currentPanel : 'chat';
  let item = '';
  if (panel === 'skills' && _skillMode === 'read') item = _currentSkillDetail?.name || '';
  if (panel === 'tasks' && _cronMode === 'read' && !_currentCronDetail?.read_only) item = String(_currentCronDetail?.id || '');
  if (panel === 'workspaces' && _workspaceMode === 'read') item = _currentWorkspaceDetail?.path || '';
  if (panel === 'profiles' && _profileMode === 'read') item = _currentProfileDetail?.name || '';
  return {panel, section:_currentSettingsSection, item, scope:S.activeProfile || 'default'};
}
function _navigationUrl(route, sid) {
  // Do not propagate prompts, arbitrary query parameters, or form contents.
  const base = new URL(sid ? _sessionUrlForSid(sid) : location.href, location.origin);
  const query = new URLSearchParams();
  if (!sid) {
    for (const key of ['session','session_id']) if (base.searchParams.has(key)) query.set(key, base.searchParams.get(key));
  }
  const hash = new URLSearchParams({panel:route.panel});
  if (route.panel === 'settings') hash.set('section', _navigationSections.has(route.section) ? route.section : 'conversation');
  if (_navigationIdentifier(route.item)) { hash.set('item', route.item); hash.set('scope', route.scope); }
  return base.pathname + (query.size ? '?' + query : '') + '#' + hash;
}
function _navigationRemember() {
  _navigation.accepted = {url:location.pathname + location.search + location.hash, state:history.state};
}
function _navigationWrite(route, replace = false, sid) {
  const url = _navigationUrl(route, sid);
  const current = location.pathname + location.search + location.hash;
  if (!replace && url === current) { _navigationRemember(); return; }
  if (!replace) ++_navigation.index;
  history[replace ? 'replaceState' : 'pushState']({...history.state, hermesNavigationIndex:_navigation.index}, '', url);
  _navigationRemember();
}
function navigationChanged(ticket) {
  if (!_navigation.ready || (ticket && !navigationCurrent(ticket)) || _navigation.restoring) return;
  _navigationWrite(_navigationSnapshot());
}
function navigationSessionUrl(sid) {
  // Boot must not push a second entry while restoring a deep link.
  if (!_navigation.ready) return navigationHasRoute();
  if (_navigation.restoring) return true;
  _navigationWrite(_navigationSnapshot(), false, sid);
  return true;
}
function _navigationClearDetail(panel) {
  if (panel === 'skills') { ++_skillLoadGeneration; _currentSkillDetail = null; _skillMode = 'empty';
    const body = $('skillDetailBody'); if (body) { body.innerHTML = ''; body.style.display = 'none'; }
    const title = $('skillDetailTitle'); if (title) title.textContent = '';
    const empty = $('skillDetailEmpty'); if (empty) empty.style.display = '';
    _setSkillHeaderButtons('empty');
  }
  if (panel === 'tasks') _clearCronDetail();
  if (panel === 'workspaces') _clearWorkspaceDetail();
  if (panel === 'profiles') _clearProfileDetail();
}
async function _navigationOpenItem(route, ticket) {
  const {panel, item, scope} = route;
  _navigationClearDetail(panel);
  if (!item) return;
  // Scope is a constraint, never an instruction to switch profiles.
  if (!scope || scope !== ticket.profile) {
    _navigationNotice('navigation_profile_mismatch', 'This link belongs to another profile. Switch profiles explicitly, then reopen it.');
    return;
  }
  let found = false;
  if (panel === 'skills' && (_skillsData || []).some(s => s.name === item)) {
    found = await openSkill(item, null, false, ticket) === true;
  }
  if (panel === 'tasks') {
    const job = (_cronList || []).find(j => String(j.id) === item && !j.read_only);
    if (job) { await openCronDetail(job, null, false, ticket); found = true; }
  }
  if (panel === 'workspaces' && (_workspaceList || []).some(w => w.path === item)) {
    openWorkspaceDetail(item, null, ticket); found = true;
  }
  if (panel === 'profiles' && (_profilesCache?.profiles || []).some(p => p.name === item)) {
    openProfileDetail(item, null, ticket); found = true;
  }
  if (!found && navigationCurrent(ticket)) _navigationNotice('navigation_item_unavailable', 'This item is unavailable. Choose another item from the list.');
}
function _navigationUndo() {
  const accepted = _navigation.accepted;
  if (!accepted) return;
  const targetIndex = history.state?.hermesNavigationIndex;
  const delta = _navigation.index - targetIndex;
  if (Number.isInteger(targetIndex) && delta) {
    _navigation.rollback = true;
    history.go(delta);
  } else history.replaceState(accepted.state, '', accepted.url);
}
async function _navigationRestore(initial = false) {
  const route = _navigationParse();
  const ticket = navigationBegin();
  _navigation.restoring = ticket;
  const targetIndex = history.state?.hermesNavigationIndex;
  try {
    // Includes same-panel item changes; a veto must not consume browser history.
    if (!await guardUnsavedEditors() || !navigationCurrent(ticket)) {
      if (navigationCurrent(ticket)) _navigationUndo();
      return;
    }
    const sid = _sessionIdFromLocation();
    if (!initial && sid && sid !== S.session?.session_id && S.busy) { _navigationUndo(); return; }
    if (!await switchPanel(route.panel, {navigationTicket:ticket}) || !navigationCurrent(ticket)) {
      if (navigationCurrent(ticket)) _navigationUndo();
      return;
    }
    if (!initial && sid && sid !== S.session?.session_id) {
      // The session loader retains canonical lineage resolution but cannot
      // change the active profile on a history visit.
      await loadSession(sid, {skipProfileResolve:true});
      if (!navigationCurrent(ticket)) return;
    }
    if (route.panel === 'settings') switchSettingsSection(route.section, {navigationTicket:ticket});
    await _navigationOpenItem(route, ticket);
    if (!navigationCurrent(ticket)) return;
    if (Number.isInteger(targetIndex)) _navigation.index = targetIndex;
    _navigationWrite(_navigationSnapshot(), true, S.session?.session_id);
  } catch (_) {
    if (navigationCurrent(ticket)) {
      // Panel data failed after the view changed: keep URL and visible panel
      // together, rather than restoring only the old URL over a new view.
      if (Number.isInteger(targetIndex)) _navigation.index = targetIndex;
      _navigationWrite(_navigationSnapshot(), true);
      _navigationNotice('navigation_unavailable', 'Unable to open this location. Try again.');
    }
  } finally {
    if (_navigation.restoring === ticket) _navigation.restoring = null;
  }
}
async function initPersistentNavigation() {
  if (_navigation.ready || !S._bootReady) return;
  const hasRoute = navigationHasRoute();
  _navigation.ready = true;
  _navigation.index = Number.isInteger(history.state?.hermesNavigationIndex) ? history.state.hermesNavigationIndex : 0;
  if (hasRoute) await _navigationRestore(true);
  else _navigationWrite(_navigationSnapshot(), true);
}
function navigationPopState() {
  if (!_navigation.ready) return;
  if (_navigation.rollback) { _navigation.rollback = false; return; }
  void _navigationRestore();
}
