# Photoframe project guidance

The UI is server-rendered FastAPI/Jinja with HTMX for focused updates. Keep the Python app usable without Node or a network connection at runtime.

## UI conventions

- Use the Jinja macros in `src/photoframe/templates/components/` for buttons, form controls, cards, disclosures, alerts, badges, tables, dialogs, and mobile navigation. Add a variant to a shared macro when the same need recurs.
- Use semantic daisyUI classes and the `photoframe-light` / `photoframe-dark` themes defined in `assets/styles/v2.css`. The browser stores only `light` or `dark` in `photoframe-theme`.
- Use the typography utilities documented in `docs/ui-v2-design-system.md`. Update `/ui/showcase` and its screenshots when a shared component or theme changes.
- Keep the four-destination mobile dock visible. Put the current operation and next action in the single contextual tray above it. Leave enough safe-area and content padding for both.
- Prefer HTMX swaps of the smallest meaningful region. Photo actions within `/photos` target `#photo-workspace` and update `#workspace-notices` out of band. Preserve focus and make changed status readable without colour alone.
- Rebuild checked-in CSS with `npm run build:css` after changing templates or class names. CI checks that this build leaves no diff and that the wheel contains the stylesheet and self-hosted fonts.

Use `develop` as the integration base and open a pull request for each feature branch. Do not push directly to `develop` or `main`.
