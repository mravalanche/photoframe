(() => {
  function setup() {
    document.querySelectorAll('img[src^="/thumbnail/"]').forEach(img => {
      if (img.dataset.recoveryReady) return;
      img.dataset.recoveryReady = 'true';
      const failed = () => {
        if (img.nextElementSibling?.classList.contains('thumbnail-error')) return;
        img.hidden = true;
        const placeholder = document.createElement('span');
        placeholder.className = 'thumbnail-error';
        placeholder.textContent = 'Preview unavailable';
        // Retry through the album recovery action; never nest buttons in thumbnail buttons.
        placeholder.title = 'Use Refresh photos & thumbnails to retry.';
        img.after(placeholder);
      };
      img.addEventListener('error', failed);
      img.addEventListener('load', () => {
        img.hidden = false;
        if (img.nextElementSibling?.classList.contains('thumbnail-error')) img.nextElementSibling.remove();
      });
      if (img.complete && !img.naturalWidth) failed();
    });
  }
  document.addEventListener('DOMContentLoaded', setup);
  document.addEventListener('htmx:afterSwap', setup);
})();
