---
name: project-opalahoa
description: "Core identity of the opalahoa project — Hawaii marketplace/giveaway app, stack, and key architecture"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7988ec26-4c3d-4cef-9545-b814b0c5e8e4
---

Hawaii-focused marketplace and giveaway platform. Live at https://www.opalahoa.com (Railway).

**Stack:** React 19 + Tailwind 4 + Express 4 + tRPC 11, MySQL/TiDB via Drizzle ORM, Cloudflare R2 for storage.

**Key features:**
- Marketplace listings with image uploads, search, filters (category/price/island)
- Auction system with countdown timers and live bidding
- Giveaway/FlipCard system — users flip cards to reveal discounted deals or free gifts, claim with address form (Street/City/ZIP + Hawaii island select)
- Square payment integration for discounted giveaway items and tier upgrades
- Coupon codes on giveaway items (if price hits $0, no payment needed)
- Tier Promotion System (promo codes → free/discounted/price_override tier upgrades)
- User tiers: Free → Seller → Premium
- Role system: `user` | `admin`
- Super Admin panel for giveaway/coupon/listing management
- Email/password auth + Manus OAuth (both supported)
- Google Maps integration via proxy

**Storage:** Migrated from Manus built-in storage to Cloudflare R2. Images served via `/manus-storage/` path; frontend prefixes with `window.location.origin`.

**Why:** App started on Manus platform, fully migrated to Railway as of 2026-06-14.

**How to apply:** Frame all infrastructure decisions around Railway + TiDB + Cloudflare R2. Do not reference Manus platform infrastructure.
