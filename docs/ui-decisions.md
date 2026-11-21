# GharSaathi UI decisions

These are durable product conventions for the mobile shopping chat.

- Keep the home screen chat-first: the user opens it, speaks or types, makes the important decision, and leaves.
- Treat it like a messaging app. Assistant replies keep breathing room on the right; user replies keep breathing room on the left. New turns should settle into view without abrupt scrolling.
- Never render the GharSaathi avatar beside assistant messages, including the greeting, loading, empty, basket, support, tracking, and voice transcript states. The logo exists only in the fixed header; repeated left-side icons waste mobile width and can create dark-border/cropping artifacts.
- Product comparison is decision-first: state the meaningful price/time difference before secondary details.
- For multiple products, use one compact card at a time with a stacked/deck affordance. A short swipe or a successful Add advances to the next item.
- Never let horizontal interactions create page-level overflow, clipped half-cards, or hard cut edges. Keep the active card comfortably contained and use depth/fade only as an affordance.
- Preserve explicit approval for purchases and support messages. Clearly distinguish prepared/approved from actually submitted or purchased.
- Use static demo data while iterating on UI. Live provider calls happen only when explicitly switching to live mode.
