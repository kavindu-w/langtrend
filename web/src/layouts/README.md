# src/layouts

Shared page shell for every route in `src/pages/`.

```text
layouts/
└── BaseLayout.astro   <head>, top nav, footer — wraps every page via <slot />
```

`BaseLayout.astro` imports `../styles/global.css` and KaTeX's stylesheet, renders the `<head>` (title/description props, viewport, color-scheme), the top nav (Home/About, with `aria-current` set from `Astro.url`), and a contact footer. Pages wrap their content in `<BaseLayout title=... description=...><slot /></BaseLayout>`; there is currently only the one layout since every page shares the same shell.
