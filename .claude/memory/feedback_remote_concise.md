---
name: Remote bot must give concise responses
description: Telegram bot replies should be short and actionable since users interact via voice on mobile
type: feedback
---

Remote (Telegram) bot responses must be short and precise. Users interact via voice on mobile — long responses cause them to lose context.

**Why:** User explicitly said voice-based remote interaction needs precise, minimal answers. Unlike text chat, voice users can't easily re-read or scroll.

**How to apply:** When building dispatch prompts and formatting bot replies, prioritize brevity. Summary should be 1-2 sentences max. Terminal output should be trimmed to the most relevant lines.
