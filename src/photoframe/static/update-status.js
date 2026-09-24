(() => {
  const active = new Set(['queued', 'checking', 'downloading', 'staging', 'verifying', 'activating', 'restarting', 'health_check', 'rolling_back']);
  function presentation(state) {
    if (state.phase === 'unavailable') return {icon: '!', label: 'Updater unavailable', tone: 'warning'};
    if (state.phase === 'failed') return {icon: '!', label: 'Update failed', tone: 'error'};
    if (active.has(state.phase)) return {icon: '↻', label: state.phase === 'checking' ? 'Checking for updates' : 'Update in progress', tone: 'working'};
    if (state.check_error) return {icon: '!', label: 'Check failed', tone: 'warning'};
    if (state.staged_version) return {icon: '↓', label: 'Ready to install', tone: 'available'};
    if (state.upgrade_available) return {icon: '↓', label: 'Update available', tone: 'available'};
    if (state.phase === 'rolled_back') return {icon: '↶', label: 'Previous version restored', tone: 'warning'};
    if (!state.managed) return {icon: 'i', label: 'Manual updates', tone: 'neutral'};
    if (state.latest_manifest || state.phase === 'complete') return {icon: '✓', label: 'Up to date', tone: 'success'};
    return {icon: '—', label: 'Not checked yet', tone: 'neutral'};
  }
  function relativeCheck(timestamp, now = Date.now()) {
    if (!timestamp) return 'Never checked';
    const seconds = Math.max(0, Math.floor((now - timestamp * 1000) / 1000));
    if (seconds < 60) return 'Just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    const days = Math.floor(hours / 24);
    return `${days} day${days === 1 ? '' : 's'} ago`;
  }
  function paintBadge(node, state) {
    if (!node) return;
    const status = presentation(state);
    node.dataset.tone = status.tone;
    node.querySelector('[data-update-icon]').textContent = status.icon;
    node.querySelector('[data-update-label]').textContent = status.label;
  }
  // Deliberately support a small Markdown subset. Every source string becomes
  // text; only HTTP(S) links can create an anchor, and raw HTML stays literal.
  function inline(parent, text) {
    const tokens = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^\s)]+\))/g;
    let end = 0;
    for (const match of text.matchAll(tokens)) {
      parent.append(document.createTextNode(text.slice(end, match.index)));
      const token = match[0];
      let node;
      if (token.startsWith('`')) {
        node = document.createElement('code'); node.textContent = token.slice(1, -1);
      } else if (token.startsWith('**')) {
        node = document.createElement('strong'); node.textContent = token.slice(2, -2);
      } else {
        const parts = /^\[([^\]]+)\]\(([^\s)]+)\)$/.exec(token);
        try {
          const url = new URL(parts[2]);
          if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Unsupported URL');
          node = document.createElement('a'); node.href = url.href;
          node.rel = 'noopener noreferrer'; node.textContent = parts[1];
        } catch (_) { node = document.createTextNode(token); }
      }
      parent.append(node); end = match.index + token.length;
    }
    parent.append(document.createTextNode(text.slice(end)));
  }
  function releaseNotes(parent, source) {
    parent.replaceChildren();
    let list = null, paragraph = null, code = null;
    for (const line of String(source || '').split(/\r?\n/)) {
      if (/^\s*```/.test(line)) {
        if (code) code = null;
        else { const pre = document.createElement('pre'); code = document.createElement('code'); pre.append(code); parent.append(pre); }
        list = paragraph = null; continue;
      }
      if (code) { code.textContent += `${line}\n`; continue; }
      if (!line.trim()) { list = paragraph = null; continue; }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      const item = /^\s*(?:([-*+])|\d+\.)\s+(.+)$/.exec(line);
      if (heading) {
        const node = document.createElement(`h${Math.min(6, heading[1].length + 2)}`);
        inline(node, heading[2]); parent.append(node); list = paragraph = null;
      } else if (item) {
        const kind = item[1] ? 'ul' : 'ol';
        if (!list || list.tagName.toLowerCase() !== kind) { list = document.createElement(kind); parent.append(list); }
        const node = document.createElement('li'); inline(node, item[2]); list.append(node); paragraph = null;
      } else {
        if (!paragraph) { paragraph = document.createElement('p'); parent.append(paragraph); }
        else paragraph.append(document.createTextNode(' '));
        inline(paragraph, line); list = null;
      }
    }
  }
  globalThis.PhotoframeUpdates = {active, presentation, relativeCheck, paintBadge, releaseNotes};
  const badge = document.querySelector('[data-update-badge]');
  if (!badge) return;
  function show(state) {
    paintBadge(badge, state);
    badge.title = `Software updates · Last checked: ${relativeCheck(state.last_check)}`;
  }
  // The Settings page already polls; share its result instead of polling twice.
  if (document.getElementById('update-status')) {
    document.addEventListener('photoframe:update-status', event => show(event.detail));
    return;
  }
  async function poll() {
    try {
      const response = await fetch('/api/updates/status', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error('Unavailable');
      show(await response.json());
    } catch (_) { paintBadge(badge, {phase: 'unavailable'}); }
    setTimeout(poll, 30000);
  }
  poll();
})();
