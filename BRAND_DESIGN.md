# BRAND_DESIGN.md — Clever Wolf AI

> **Status:** v1 draft (Quincy + Claude). This is the design-spec counterpart to `BRAND_VOICE.md`.
> **Purpose:** Single source of truth for design tokens. Confirm tokens here *before* writing site code.
> **Tunable knobs** are flagged `⟡ KNOB`. Everything else can be treated as settled for v1.

---

## 0. Positioning (why the design looks the way it does)

Clever Wolf AI is the AI-services arm of Clever Wolf Digital, selling to Seattle-area SMBs. The brand promise is **premium but unintimidating** — "the smart pack, not the lone genius." Buyers are owners and operators, not engineers, so the design has to read *credible and high-end* without tipping into cold tech-bro sterility.

Design thesis: **a wolf pack at dusk** — cool fog-slate base, the warmth of a fire, sharp coordinated intelligence. Two disciplined accents (a Puget-Sound teal for trust/action, a reserved brass for the single premium moment) carry the whole identity. We deliberately avoid the three AI-default looks (cream + serif + terracotta; near-black + one acid accent; hairline broadsheet).

---

## 1. Color tokens

Cool-warm slate neutrals (the "pelt"), one cool primary, one warm reserved highlight.

| Token | Hex | Use |
|---|---|---|
| `--ink` | `#0E1419` | Primary dark surface / dark-mode background |
| `--ink-raised` | `#161D24` | Cards & raised surfaces on dark |
| `--ink-elevated` | `#1E272F` | Popovers, hover surfaces on dark |
| `--hairline` | `#2A343D` | Borders / dividers on dark |
| `--paper` | `#F6F4EF` | Light-mode background (warm off-white, approachable) |
| `--paper-raised` | `#FFFFFF` | Cards on light |
| `--paper-hairline` | `#E3DFD6` | Borders / dividers on light |
| `--teal` | `#0E9D8E` | **Primary action / links / focus** (Puget Sound) |
| `--teal-hover` | `#0B857A` | Hover/active for primary |
| `--teal-soft` | `#12C4B0` | Glows, on-dark emphasis, charts |
| `--brass` | `#C9912E` | **Reserved highlight** — one moment per view max |
| `--brass-soft` | `#E0B45E` | Brass on dark backgrounds |

**Neutral text scale**
| Token | On dark | On light | Use |
|---|---|---|---|
| `--text-strong` | `#F2F5F6` | `#10171C` | Headlines, key labels |
| `--text` | `#C3CCD2` | `#2C353B` | Body |
| `--text-muted` | `#8A949C` | `#5C666D` | Captions, secondary |
| `--text-faint` | `#5C666D` | `#9099A0` | Disabled, placeholders |

**Semantic**
| Token | Hex |
|---|---|
| `--success` | `#2FB67C` |
| `--warning` | `#E0A23A` |
| `--error` | `#E0524E` |
| `--info` | `#3D8FE0` |

`⟡ KNOB` — If Clever Wolf Digital (the parent) has an existing primary color or wordmark color, swap `--teal` to inherit it so the subsidiary reads as family. Send me the parent palette and I'll re-derive the scale.

```css
:root {
 --ink:#0E1419; --ink-raised:#161D24; --ink-elevated:#1E272F; --hairline:#2A343D;
 --paper:#F6F4EF; --paper-raised:#FFFFFF; --paper-hairline:#E3DFD6;
 --teal:#0E9D8E; --teal-hover:#0B857A; --teal-soft:#12C4B0;
 --brass:#C9912E; --brass-soft:#E0B45E;
 --success:#2FB67C; --warning:#E0A23A; --error:#E0524E; --info:#3D8FE0;
 /* default = light mode text */
 --text-strong:#10171C; --text:#2C353B; --text-muted:#5C666D; --text-faint:#9099A0;
}
[data-theme="dark"] {
 --paper:#0E1419; --paper-raised:#161D24; --paper-hairline:#2A343D;
 --text-strong:#F2F5F6; --text:#C3CCD2; --text-muted:#8A949C; --text-faint:#5C666D;
}
```

---

## 2. Typography

Deliberately *not* the Fraunces/serif route used on other Clever Wolf builds. Modern grotesque system with a mono utility face that gives the "intelligent system" texture without decoration.

| Role | Family | Source | Notes |
|---|---|---|---|
| Display / headlines | **Clash Display** | Fontshare | Used with restraint — heroes & section titles only |
| Body / UI | **Satoshi** | Fontshare | Clean, friendly, the workhorse |
| Utility / data / eyebrows | **JetBrains Mono** | Google Fonts | Labels, stats, code, "boot-up" eyebrows |

`⟡ KNOB` — Mono can be IBM Plex Mono instead (you've used it on Level Up Smarter) if you want consistency across the portfolio. Say the word and I'll switch the token.

**Type scale** (1.250 major-third, 16px base)
```css
:root {
 --font-display:"Clash Display",system-ui,sans-serif;
 --font-body:"Satoshi",system-ui,sans-serif;
 --font-mono:"JetBrains Mono",ui-monospace,monospace;

 --fs-eyebrow:0.78rem; /* 12.5px — mono, uppercase, tracked */
 --fs-body-sm:0.875rem; /* 14px */
 --fs-body:1rem; /* 16px */
 --fs-lead:1.25rem; /* 20px — intros */
 --fs-h3:1.563rem; /* 25px */
 --fs-h2:2.441rem; /* 39px */
 --fs-h1:3.815rem; /* 61px — clamp on mobile */
 --fs-hero:clamp(2.8rem,6vw,5.96rem);
}
```
- Headlines: weight 600, `letter-spacing:-0.02em`, `line-height:1.05`.
- Body: weight 400, `line-height:1.6`, max measure `68ch`.
- Eyebrow: mono, weight 500, `text-transform:uppercase`, `letter-spacing:0.18em`, in `--teal` or `--brass`.

---

## 3. Spacing, radius, elevation

8pt base. `--space-1`=4px → `--space-2`=8 → `3`=12 → `4`=16 → `6`=24 → `8`=32 → `12`=48 → `16`=64 → `24`=96 → `32`=128 (section rhythm).

```css
:root{
 --r-sm:6px; --r-md:10px; --r-lg:16px; --r-pill:999px;
 --shadow-sm:0 1px 2px rgba(14,20,25,.06), 0 1px 3px rgba(14,20,25,.10);
 --shadow-md:0 4px 12px rgba(14,20,25,.10), 0 2px 4px rgba(14,20,25,.06);
 --shadow-lg:0 18px 48px rgba(14,20,25,.18);
 --shadow-teal:0 8px 32px rgba(14,157,142,.28); /* primary CTA glow, sparing */
}
```
Border-radius philosophy: soft-but-not-bubbly (`--r-md` default). No zero-radius broadsheet look, no fully rounded pills except chips/badges.

---

## 4. Layout

```css
:root{ --container:1200px; --container-narrow:760px; --gutter:clamp(20px,5vw,64px); }
/* breakpoints */ /* sm 480 · md 768 · lg 1024 · xl 1280 */
```
Section vertical rhythm: `--space-32` top/bottom on desktop, `--space-16` on mobile. Don't fill sections with flat ink — alternate `--paper` / `--paper-raised`, or on dark use a faint top-edge teal→transparent gradient to give depth.

---

## 5. Motion

Codifying the moves you like (count-up, edge-wipe, hover-lift). Respect `prefers-reduced-motion`.

```css
:root{
 --dur-fast:140ms; --dur:240ms; --dur-slow:520ms;
 --ease-out:cubic-bezier(.16,1,.3,1); /* entrances, hovers */
 --ease-inout:cubic-bezier(.65,0,.35,1); /* transitions */
}
```
- **Hover-lift** (cards): `translateY(-4px)` + `--shadow-lg`, `--dur` `--ease-out`.
- **Edge-wipe** (CTA / listing hover): `--teal` sweep from left, `--dur-slow`.
- **Count-up** (proof stats): trigger on scroll-in, ~1.2s, mono numerals.
- **Reduced motion:** disable transforms/wipes, keep opacity fades only.

---

## 6. Signature element (the one memorable thing)

**The boot-up eyebrow → pack-node hero.** Section eyebrows are set in mono and "type in" on load like a system coming online (e.g. `> initializing.pack`), capped by a single `--teal` underline sweep. The hero carries a quiet **pack motif**: 3–5 connected nodes (a small constellation/graph) drawn in hairline + teal, one node lit brass — "coordinated intelligence." Spend boldness here; keep everything else disciplined and quiet.

---

## 7. Components (conventions)

- **Primary button:** `--teal` bg, `--text-strong` (light text), `--r-md`, `--shadow-teal` on hover, mono-optional micro-label. Verb-true label (`Book your audit`, not `Submit`).
- **Secondary button:** transparent, `1px --hairline` border, fills to `--ink-elevated` / `--paper-raised` on hover.
- **Card:** `--paper-raised` / `--ink-raised`, `--r-lg`, `--shadow-sm` → `--shadow-lg` on hover-lift, `1px` hairline.
- **Input:** `1px --hairline`, `--r-md`, focus ring `2px --teal` at 40% + border `--teal`. Never remove the focus ring.
- **Badge/chip:** mono, `--r-pill`, teal or brass tinted bg.
- **Brass rule:** appears once per viewport at most — a headline stat, a single CTA flourish, or the lit pack-node. Never as a fill.

---

## 8. Imagery & iconography

- **Photography:** real, warm, human Seattle/PNW + the people behind the work. Avoid stock "robot hand / glowing brain" AI clichés entirely.
- **Icons:** thin, even-stroke line icons (1.5px), slightly geometric — the clean Japanese-iconography feel. One family, no mixing.
- **Wolf mark:** geometric, single-weight; works at 24px favicon and as a large hairline watermark. Lit-node brass detail optional at large sizes only.

---

## 9. Quality floor (non-negotiable)

Responsive to 360px · visible keyboard focus everywhere · `prefers-reduced-motion` respected · color contrast ≥ 4.5:1 body / 3:1 large · semantic HTML landmarks · `--teal` on `--paper` passes AA for large text/UI (verify for small body — darken to `--teal-hover` where needed).

---

## 10. Open inputs to finalize v1 → v1.0

1. **Parent palette** — does Clever Wolf Digital have a locked color/wordmark to inherit? (decides §1 `--teal`)
2. **Mono choice** — JetBrains Mono vs IBM Plex Mono for portfolio consistency (§2)
3. **`BRAND_VOICE.md`** — paste it so tone tokens (eyebrow phrasing, button verbs) match voice exactly
