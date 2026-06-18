# BRAND_DESIGN.md — CleverWolf.ai
_Updated 2026-06-18. Previous draft (teal/brass/Clash Display) deprecated — Quincy confirmed navy/gold direction._

---

## Color tokens

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#0A1726` | Primary dark background |
| `--card` | `#0F2438` | Cards, raised surfaces |
| `--accent` | `#C8A24E` | Signal Gold — CTAs, highlights, eyebrows |
| `--blue` | `#2E6FB8` | Secondary accent — links, wolf mark |
| `--blue-light` | `#6BA6E0` | Soft blue — glows, detail |
| `--text` | `#E7EEF6` | Primary text |
| `--text-muted` | `#9DB2C7` | Body copy, secondary text |
| `--text-faint` | `#5E7488` | Captions, disabled |
| `--border` | `rgba(255,255,255,.07)` | Subtle dividers on dark |
| `--light-bg` | `#F6F4EF` | Light sections (About, Approach, Clients) |
| `--light-text` | `#0A1726` | Text on light sections |
| `--light-muted` | `#45525F` | Body on light sections |

```css
:root {
  --accent: #C8A24E;
  --blue:   #2E6FB8;
}
```

---

## Typography

| Role | Family | Weight | Source |
|---|---|---|---|
| Display / headlines | **Schibsted Grotesk** | 400–900 | Google Fonts |
| Body / UI | **Hanken Grotesk** | 300–700 | Google Fonts |
| Editorial italic | **Newsreader** | 400–500 italic | Google Fonts |

```html
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:ital,wght@0,400;0,500;0,600;0,700;0,800;0,900;1,400&family=Hanken+Grotesk:wght@300;400;500;600;700&family=Newsreader:ital,opsz,wght@1,6..72,400;1,6..72,500&display=swap" rel="stylesheet">
```

- Headlines: Schibsted Grotesk 800, `letter-spacing: -0.02em`, `line-height: 1.04`
- Body: Hanken Grotesk 400, `font-size: 17–19px`, `line-height: 1.65–1.74`
- Eyebrows: Schibsted Grotesk 600–700, `font-size: 12px`, `letter-spacing: .26em`, `text-transform: uppercase`, color `--accent`

---

## Wolf mark (Sentinel SVG)

Crystalline, faceted, forward-facing. Used in nav (34px), hero display (168px), footer (34px).

```svg
<symbol id="wolf" viewBox="0 0 120 120" overflow="visible">
  <path d="M38,6 L28,32 L21,52 L33,73 L55,92 L60,98 L65,92 L87,73 L99,52 L92,32 L82,6 L66,30 L60,22 L54,30 Z" fill="#2E6FB8"/>
  <path d="M60,22 L66,30 L82,6 L92,32 L99,52 L87,73 L65,92 L60,98 Z" fill="#1E4E7E"/>
  <path d="M60,50 L54,70 L60,98 Z" fill="#6BA6E0"/>
  <path d="M60,50 L66,70 L60,98 Z" fill="#4F8FD0"/>
  <path d="M40,13 L50,30 L33,30 Z" fill="#173A5C"/>
  <path d="M80,13 L87,30 L70,30 Z" fill="#122E47"/>
  <path d="M39,47 L51,50 L48,57 L38,52 Z" fill="#C8A24E"/>
  <path d="M81,47 L69,50 L72,57 L82,52 Z" fill="#A9863F"/>
  <path d="M56,77 L64,77 L60,86 Z" fill="#0A1726"/>
</symbol>
```

---

## Motion

```css
@keyframes cwGlow {
  0%, 100% { opacity: .5; }
  50%       { opacity: .9; }
}
@keyframes cwRise {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: none; }
}
```

---

## Layout

- Max width: `1200px`
- Narrow/prose: `760px`
- Section padding: `96–108px` vertical, `40px` horizontal
- Grid gap: `20px` (cards), `48–72px` (two-col layouts)
- Border radius: `12–16px` cards, `9–11px` buttons

---

## Components

- **Primary CTA:** `background: var(--accent)`, `color: #0A1726`, Schibsted Grotesk 700, `border-radius: 10–11px`
- **Secondary CTA:** `border: 1px solid rgba(255,255,255,.22)`, transparent bg
- **Cards (dark):** `background: #0F2438`, `border: 1px solid rgba(255,255,255,.07)`, `border-radius: 16px`
- **Cards (light):** `background: #fff`, `border: 1px solid #E7E1D4`
- **AI badge (on service cards):** gold-bordered box, `background: rgba(200,162,78,.06)`, `border: 1px solid rgba(200,162,78,.18)`
- **Nav:** sticky, `backdrop-filter: blur(14px)`, `background: rgba(10,23,38,.82)`

---

## Source file
`C:\Users\tidas\cleverwolf-ai-website\index.html` — production-ready reference implementation.
