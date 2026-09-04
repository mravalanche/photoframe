(function (global) {
  'use strict';

  const tabKey = 'photoframe-tab-id';
  const intentKey = 'photoframe-render-intent';
  const claimPrefix = 'photoframe-tab-claim:';

  function safeJson(raw) {
    try { return raw ? JSON.parse(raw) : null; } catch (_) { return null; }
  }

  function createSession(options) {
    const local = options.localStorage;
    const session = options.sessionStorage;
    const randomUUID = options.randomUUID;
    const instanceId = randomUUID();
    let tabId = null;
    let claimKey = null;
    let storageAvailable = true;
    let volatileIntent = null;

    function claim() {
      tabId = tabId || session.getItem(tabKey) || randomUUID();
      claimKey = claimPrefix + tabId;
      const existingClaim = local.getItem(claimKey);
      if (existingClaim && existingClaim !== instanceId) {
        // sessionStorage may have been cloned into a duplicated or opener-created
        // tab. The live origin-wide claim makes that copy surrender ownership.
        tabId = randomUUID();
        claimKey = claimPrefix + tabId;
        session.setItem(tabKey, tabId);
        session.removeItem(intentKey);
      } else {
        session.setItem(tabKey, tabId);
      }
      local.setItem(claimKey, instanceId);
    }

    try {
      claim();
    } catch (_) {
      // Without origin-wide storage, never trust a persisted ownership marker.
      storageAvailable = false;
      try { session.removeItem(intentKey); } catch (_) { /* unavailable */ }
    }

    function currentIntent() {
      if (!storageAvailable) return volatileIntent;
      try {
        const intent = safeJson(session.getItem(intentKey));
        return intent?.tabId === tabId ? intent.operationId : null;
      } catch (_) {
        return null;
      }
    }

    function newIntent() {
      const operationId = randomUUID();
      if (!storageAvailable) {
        volatileIntent = operationId;
        return operationId;
      }
      try {
        session.setItem(intentKey, JSON.stringify({ tabId, operationId }));
        return operationId;
      } catch (_) {
        return null;
      }
    }

    function release() {
      if (!storageAvailable || !claimKey) return;
      try {
        if (local.getItem(claimKey) === instanceId) local.removeItem(claimKey);
      } catch (_) { /* unavailable */ }
    }

    function reclaim() {
      if (!storageAvailable) return;
      try { claim(); } catch (_) { storageAvailable = false; }
    }

    return {
      currentIntent,
      newIntent,
      reclaim,
      release,
      get tabId() { return tabId; },
    };
  }

  const api = { createSession, claimPrefix, intentKey, tabKey };
  global.PhotoframeTabIdentity = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(globalThis));
