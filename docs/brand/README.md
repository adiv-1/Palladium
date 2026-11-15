# Palladium brand

- `mark.svg`: the helmet and crest, for small sizes (navigation, icons).
- `shield.svg`: the helmet inside the shield, for large placements (login, home, documents).
- `favicon.svg`: the mark on a navy tile, used as the browser icon.

In the apps the logo is the `Logo` React component (`apps/web/src/Logo.tsx`, `apps/dashboard/src/Logo.tsx`, identical copies). It takes its colours from the theme tokens (`--red`, `--yellow`), so it is correct in light and dark mode. The static SVGs here use the light-mode colours (#d8202f and #f0b400).

The mark is a simplified redraw of the original artwork: a helmet split red and gold, a six-plume crest, and a plain face opening, so it stays readable at 16 px.
