(function (global) {
  'use strict';

  const tabKey = 'photoframe-tab-id';
  const intentKey = 'photoframe-render-intent';
  const claimPrefix = 'photoframe-tab-claim:';

  function safeJson(raw) {
    try { return raw ? JSON.parse(raw) : null; } catch (_) { return null; }
  }

  function fallbackUUID() {
    const hex = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx';
    return hex.replace(/[xy]/g, character => {
      const random = Math.floor(Math.random() * 16);
      const value = character === 'x' ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    });
  }

  function browserUUID(browser) {
    try {
      if (typeof browser.crypto?.randomUUID === 'function') return browser.crypto.randomUUID();
      if (typeof browser.crypto?.getRandomValues === 'function') {
        const bytes = browser.crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0'));
        return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
      }
    } catch (_) { /* unavailable */ }
    return fallbackUUID();
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

  function createBrowserSession(browser) {
    let local = null;
    let session = null;
    try { local = browser.localStorage; } catch (_) { /* unavailable */ }
    try { session = browser.sessionStorage; } catch (_) { /* unavailable */ }
    return createSession({
      localStorage: local,
      sessionStorage: session,
      randomUUID: () => browserUUID(browser),
    });
  }

  const api = { createBrowserSession, createSession, claimPrefix, intentKey, tabKey };
  global.PhotoframeTabIdentity = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(globalThis));
