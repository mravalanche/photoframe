(() => {
  const el = id => document.getElementById(id);
  let csrf = null, state = {}, timer, failures = 0, busy = false, pendingAction = 'activate';
  const active = new Set(['queued', 'checking', 'downloading', 'staging', 'verifying', 'activating', 'restarting', 'health_check', 'rolling_back']);
  function controls() {
    const blocked = busy || active.has(state.phase);
    el('check-update').disabled = blocked;
    el('weekly-checks').disabled = blocked;
    el('stage-update').disabled = blocked || !state.latest_manifest || state.latest_manifest.version.split('.').map(Number).reduce((a, v, i) => a || v - Number(state.running_version.split('.')[i]), 0) <= 0;
    el('apply-update').disabled = blocked || !state.staged_version;
    el('rollback-update').hidden = !state.previous_version;
    el('rollback-update').disabled = blocked;
  }
  async function send(action, body = {}) {
    if (action !== 'session') csrf = (await send('session')).csrf;
    const response = await fetch(`/api/updates/${action}`, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf || ''}, body:JSON.stringify(body), signal:AbortSignal.timeout(45000)});
    const result = await response.json();
    if (!response.ok) { throw new Error(result.message || 'Update request failed'); }
    return result;
  }
  async function action(name, body) {
    busy = true; controls(); el('update-error').textContent = '';
    try { if (name === 'activate' || name === 'rollback') { const requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), v => v.toString(16).padStart(2, '0')).join(''); body = {...body, request_id:requestId}; try { localStorage.setItem('photoframe-update-job', requestId); } catch (_) { throw new Error('Allow browser storage to save this update job before restarting.'); } } const result = await send(name, body); if (result.phase) state.phase = result.phase; }
    catch(error) { el('update-error').textContent = error.message; }
    finally { busy = false; controls(); await poll(); }
  }
  async function poll() {
    clearTimeout(timer);
    try {
      const response = await fetch('/api/updates/status', {cache:'no-store', signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('Unavailable');
      state = await response.json(); failures = 0;
      el('managed-controls').hidden = !state.managed;
      el('unmanaged').hidden = state.managed;
      el('update-status').textContent = state.message || state.phase;
      if (['complete', 'rolled_back', 'failed'].includes(state.phase)) { try { localStorage.removeItem('photoframe-update-job'); } catch (_) { /* Storage may be disabled. */ } }
      el('update-progress').hidden = !active.has(state.phase);
      el('update-progress').value = state.progress || 0;
      el('weekly-checks').checked = state.weekly;
      el('update-release').textContent = state.staged_version ? `v${state.staged_version} verified and ready to apply.` : state.latest_manifest ? `Latest stable release: v${state.latest_manifest.version}` : '';
      el('release-notes').textContent = state.latest_manifest?.release_notes || (state.public_release ? `Latest published version: v${state.public_release}.` : '');
      el('update-diagnostics').textContent = `Status: ${state.phase}. Running v${state.running_version}. ${state.job_id ? `Job: ${state.job_id}.` : ''} ${state.last_check ? `Last check: ${new Date(state.last_check * 1000).toLocaleString()}.` : 'Not checked yet.'} ${state.check_error || ''}`;
      el('reconnect').hidden = true; controls();
      timer = setTimeout(poll, active.has(state.phase) ? 2000 : 15000);
    } catch (_) {
      failures++;
      el('update-status').textContent = failures < 9 ? 'Waiting for Photoframe to reconnect…' : 'Automatic reconnection paused. Check the device, then choose Reconnect.';
      el('reconnect').hidden = failures < 9;
      if (failures < 9) timer = setTimeout(poll, Math.min(30000, 1000 * 2 ** failures));
    }
  }
  el('public-check').onclick = async () => { el('public-check').disabled = true; try { await fetch('/api/updates/releases', {signal:AbortSignal.timeout(15000)}); await poll(); } catch (_) { el('update-error').textContent = 'Release check unavailable. Try again later.'; } finally { el('public-check').disabled = false; } };
  el('check-update').onclick = () => action('check');
  el('stage-update').onclick = () => action('stage', {release:state.latest_manifest.version});
  el('weekly-checks').onchange = () => action('preferences', {weekly:el('weekly-checks').checked});
  el('apply-update').onclick = () => { pendingAction = 'activate'; el('confirm-title').textContent = 'Apply update and restart?'; el('confirm-detail').textContent = 'Your frame will pause briefly. Your saved settings and photos will be kept.'; el('confirm-action').textContent = 'Apply & restart'; el('apply-dialog').returnValue = ''; el('apply-dialog').showModal(); };
  el('rollback-update').onclick = () => { pendingAction = 'rollback'; el('confirm-title').textContent = 'Restore previous version and restart?'; el('confirm-detail').textContent = 'This also restores the settings saved before the update. Settings changes made since that update will be lost.'; el('confirm-action').textContent = 'Restore & restart'; el('apply-dialog').returnValue = ''; el('apply-dialog').showModal(); };
  el('apply-dialog').addEventListener('close', () => { if (el('apply-dialog').returnValue === 'confirm') action(pendingAction, {confirmed:true, ...(pendingAction === 'activate' ? {release:state.staged_version} : {})}); });
  el('reconnect').onclick = () => { failures = 0; poll(); };
  poll();
})();
