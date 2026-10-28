# Gallery feature-branch testing

The `feature/photos-gallery` branch combines the gallery implementation with the app
diagnostics already integrated into `develop`. Keep this branch separate from `develop`
until the interactive review is complete. It is not a signed Pi release.

## Start the standalone app later

From the feature-branch checkout:

```sh
uv sync --frozen --all-groups
uv run python scripts/run_standalone.py
```

Open <http://127.0.0.1:8765/photos> on the machine running the app. The launcher binds only
to loopback and uses the built-in local demo provider and display simulator. It needs no
Immich connection, API key or physical display. Node is needed to rebuild CSS and run
browser checks, but not to run the app.

The demo keeps the display simulator even when hardware settings are saved. The launcher
also disables inherited managed-installation markers, so it cannot activate the system updater.

Demo state is retained in the ignored `.standalone-data/` directory in this checkout.
Restarting with the same directory preserves saved photo layouts, hidden choices and
diagnostic history. Production data is separate. The launcher refuses to reuse a
non-empty directory without its standalone marker.

Use `--port 8766` if the default port is occupied. For a fresh session, choose a new empty
directory with `--data-dir /tmp/photoframe-gallery-review`. Stop the foreground app with
Ctrl+C. Starting it later is a separate action; preparing this branch does not install
a service or leave a server running.

## Interactive walkthrough

1. Open Photos at desktop and phone widths. Each image opens the existing framing modal;
   its single caption line contains the fit pill and truncated filename. Check the full
   accessible filename, keyboard focus and touch targets.
2. Change whole-photo/fill and background options. Save framing, close and reopen the
   modal, and check that the choice and gallery pill agree. Use Show now with the
   simulator and check the distinct On frame now badge.
3. Hide a photo, use Undo, then hide and restore it from hidden-photo recovery. Check
   default and bulk layouts, and open all disclosures and menus without horizontal page
   scrolling.
4. Open Settings → Advanced & recovery → Activity & diagnostics. Refresh, filter and
   download the log. Stop and restart the standalone app and confirm history remains.

The demo can validate navigation, framing workflows and diagnostics interaction. It
cannot validate real Inky output, Wi-Fi disconnection/recovery or a physical reboot soak.
The documented signed Develop channel becomes available after reviewed integration;
see [managed updates](managed-updates.md#signed-develop-channel).

## Automated verification

```sh
uv run poe check
npm ci
npx playwright install chromium
npm run test:browser
```

Browser checks launch and stop their own temporary local demo. They run with one worker because
all browser projects share that demo’s preview, framing and hidden-photo state. Failure screenshots and
traces are written under ignored `test-results/`. These checks do not start the persistent
standalone session described above.
