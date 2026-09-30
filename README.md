# Video studio

Edit phone videos from the Claude app. Film, drop the video in a Google Drive folder called `Video Inbox`, and ask Claude (Code tab) to edit it. Claude shows short labelled samples before rendering anything, puts the finished video back in Drive, and remembers your picks so it asks less over time.

This repository holds only the editor (`tools/vidkit.py`) and Claude's instructions (`CLAUDE.md`, `.claude/skills/phone-edit/`). Your videos, your preferences and your Drive connection stay in your own Google Drive and your own Claude environment; nothing personal is ever written here.

Setup takes about 40 minutes on a phone, once. Start a Claude cloud session on this repository and say "Set me up. Read CLAUDE.md."
