(() => {
  const dock = document.getElementById('activity-dock');
  if (!dock) return;
  const title = dock.querySelector('[data-activity-title]');
  const detail = dock.querySelector('[data-activity-detail]');
  const progress = dock.querySelector('progress');
  const dismiss = dock.querySelector('[data-activity-dismiss]');
  let timer, polling = false, submitting = false, trackedAlbum = null, state = null;
  let currentKey = null, refreshedAlbum = null, pendingFrame = false;
  let dismissed = null, completedAt = 0, completionKey = null, failures = 0, localError = null;
  try { dismissed = window.sessionStorage.getItem('photoframe-dismissed-activity'); } catch (_) { /* Storage may be disabled. */ }
  const terminal = phase => ['complete', 'failed'].includes(phase);
  const keyOf = (kind, job) => `${kind}:${job.id || job.operation_id || job.started_at || ''}:${job.phase}`;
  function show(heading, message, active, failed = false, counts = null) {
    dock.hidden = false;
    document.body.classList.add('has-activity');
    dock.classList.toggle('activity-failed', failed);
    if (title.textContent !== heading) title.textContent = heading;
    if (detail.textContent !== message) detail.textContent = message;
    progress.hidden = !active;
    if (counts && counts.total > 0) {
      progress.max = counts.total;
      progress.value = counts.completed;
    } else progress.removeAttribute('value');
    dismiss.hidden = active;
  }
  function hide() { dock.hidden = true; document.body.classList.remove('has-activity'); }
  function render() {
    if (!state) return;
    const album = state.album || {}, frame = state.render || {};
    setAlbumBusy(submitting || Boolean(album.active));
    const announcement = document.querySelector('[data-album-announcement]');
    if (album.active && announcement && announcement.textContent !== 'Album change in progress. Follow the pinned status below.') {
      announcement.textContent = 'Album change in progress. Follow the pinned status below.';
    }
    if (submitting || pendingFrame) return;
    let kind, job;
    if (album.active) { kind = 'album'; job = album; }
    else if (frame.active) { kind = 'frame'; job = frame; }
    else if (album.id && (album.phase === 'failed' || terminal(album.phase) && trackedAlbum === album.id)) { kind = 'album'; job = album; }
    else if (frame.phase === 'failed' || (frame.phase === 'complete' && frame.operation_id && frame.operation_id === currentRenderIntent())) { kind = 'frame'; job = frame; }
    else { if (localError) show('Album change not confirmed', localError, false, true); else hide(); return; }
    localError = null;
    const key = keyOf(kind, job);
    currentKey = key;
    if (key === dismissed) { hide(); return; }
    if (job.phase === 'complete') {
      if (completionKey !== key) { completionKey = key; completedAt = job.finished_at ? Date.parse(job.finished_at) : Date.now(); }
      if (Date.now() - completedAt > 8000) { hide(); return; }
    }
    const headings = kind === 'album' ? {
      loading: 'Loading album', checking: 'Checking photos', saving: 'Saving album',
      complete: 'Album ready', failed: 'Album change failed',
    } : {preparing:'Preparing photo', sending:'Sending to frame', waiting:'Refreshing e-ink display', complete:'Frame updated', failed:'Frame update failed'};
    let message = job.message || (kind === 'album' ? job.album_name || 'Preparing your album…' : 'Please wait while the frame updates.');
    if (kind === 'album' && job.phase === 'checking' && job.total != null) message = `${job.completed} of ${job.total} photos checked. ${job.album_name || ''}`;
    if (kind === 'frame' && job.phase === 'waiting') message = 'Waiting for the display to finish. E-ink refreshes can take a minute.';
    show(headings[job.phase] || 'Working…', message, job.active, job.phase === 'failed', kind === 'album' && job.phase === 'checking' ? job : null);
  }
  async function refreshWorkspace() {
    const panel = document.querySelector('[data-settings-panel="album"] > summary');
    const top = panel?.getBoundingClientRect().top;
    const headers = {};
    const intent = currentRenderIntent();
    if (intent) headers['X-Photoframe-Render-Intent'] = intent;
    const response = await fetch('/partials/workspace', {headers, cache:'no-store', signal:AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error('Workspace refresh unavailable');
    htmx.swap('#workspace', await response.text(), {swapStyle:'outerHTML'});
    const next = document.querySelector('[data-settings-panel="album"] > summary');
    if (next && Number.isFinite(top)) window.scrollBy(0, next.getBoundingClientRect().top - top);
  }
  async function poll() {
    clearTimeout(timer);
    if (polling) return;
    polling = true;
    try {
      const response = await fetch('/api/activity', {cache:'no-store', signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('Unavailable');
      const next = await response.json();
      failures = 0;
      if (next.album?.active) trackedAlbum = next.album.id;
      const finished = trackedAlbum && next.album?.id === trackedAlbum && terminal(next.album.phase)
        && refreshedAlbum !== `${next.album.id}:${next.album.phase}`;
      state = next;
      render();
      if (finished) {
        await refreshWorkspace();
        refreshedAlbum = `${next.album.id}:${next.album.phase}`;
      }
    } catch (_) {
      failures++;
      if (!dock.hidden || submitting) show('Connection interrupted', 'Reconnecting to check progress. The frame may still be working.', true);
    } finally {
      polling = false;
      timer = setTimeout(poll, failures ? Math.min(15000, failures * 2000) : state?.album?.active || state?.render?.active || !dock.hidden ? 1000 : 5000);
    }
  }
  dismiss.addEventListener('click', () => {
    dismissed = currentKey;
    localError = null;
    try { if (dismissed) window.sessionStorage.setItem('photoframe-dismissed-activity', dismissed); } catch (_) { /* Storage may be disabled. */ }
    hide();
  });
  document.addEventListener('submit', async event => {
    const form = event.target.closest?.('[data-album-confirm-form]');
    if (!form) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (submitting || state?.album?.active) return;
    submitting = true; localError = null; setAlbumBusy(true);
    announceAlbum('Album change in progress. Follow the pinned status below.');
    show('Starting album change', 'Loading the album. Your current frame stays visible.', true);
    try {
      const response = await fetch('/api/album/select', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({album_id:new FormData(form).get('album_id')}), signal:AbortSignal.timeout(15000)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || result.detail || 'Unable to start the album change.');
      trackedAlbum = result.id || result.album?.id;
      state = null;
    } catch (error) { localError = error.message; show('Album change not confirmed', `${error.message} Reconnecting to check whether it started.`, false, true); }
    finally { submitting = false; await poll(); }
  }, true);
  document.addEventListener('htmx:afterSwap', render);
  document.addEventListener('htmx:beforeRequest', event => {
    const path = event.detail.requestConfig?.path?.split('?', 1)[0];
    if (['/render/start', '/photo/next'].includes(path)) {
      pendingFrame = true;
      show('Preparing photo', 'Getting the photo ready for the frame…', true);
    }
  });
  document.addEventListener('htmx:afterRequest', event => {
    const path = event.detail.requestConfig?.path?.split('?', 1)[0];
    if (['/render/start', '/photo/next'].includes(path)) {
      pendingFrame = false;
      document.querySelectorAll('.notification-stack .notice.success').forEach(notice => dismissNotice(notice));
    }
    if (event.detail.requestConfig?.verb === 'post') poll();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  poll();
})();
