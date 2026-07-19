# Demo fixtures — the two URLs the script depends on

THROWAWAY (DEMO-11). These fixtures are tuned so the demo tells a clean story.
**Tune the fixtures, never the engine** — the whole credibility claim is that the
recommendation engine on stage is the real one.

Paste these into *Find my size*. Both are reproducible from a clean
`make db-reset && make demo-seed`.

## 1. The headline moment — confident pick

```
https://www.jcrew.com/p/bowery-dress-shirt
```

→ **M, confidence 0.73.** One candidate, four §5.4 components. This is the
"closet in, correct size out" beat (demo script §5.3).

## 2. The nuance — sub-60% two-candidate path

```
https://bananarepublic.com/p/grant-slim-non-iron-shirt
```

→ **M vs L, confidence 0.56.** Confidence < 0.60 forces the two-candidate
display with a trade-off string (PRD §5.4). This is the "when we're not sure, we
say so" beat (demo script §5.4).

The Banana Republic size chart is deliberately spaced so the user's fit profile
lands almost exactly between M and L: 5 cm chest steps with the profile's
preferred chest sitting mid-gap. Nudging `chest_cm` on M or L by more than ~1 cm
collapses this back to a confident single candidate — if you edit this product,
re-check the confidence before presenting.

## Also seeded

- `https://www.uniqlo.com/us/en/products/easy-care-stretch-slim` → L, 0.72
- `https://propercloth.com/dress-shirts/slim-stretch-poplin`

## Unknown-brand path

Any other hostname (e.g. `https://www.zara.com/...`) → **422**, "That doesn't
look like one of our partner brands yet." Safe to demo; it mirrors PRD §7.5.
