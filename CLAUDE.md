# Video studio

This repository edits phone videos from the Claude app. It holds the editor and the instructions, never video and never anyone's settings: each person's videos, taste and Drive token live in their own Google Drive and their own Claude environment.

## Every session, first

1. `bash tools/setup.sh` if `ffmpeg` or `rclone` is missing (the environment's setup script normally did this).
2. `python3 tools/vidkit.py mount`. If it says `didn't find section in config file` or `rclone did not start`, Google Drive is not connected yet: run **Connect Google Drive** below before anything else.
3. `python3 tools/vidkit.py learn pull`, then read `taste/edit-preferences.md`. Never ask what it answers.

## Connect Google Drive (first time, from the phone, no computer)

1. `python3 tools/vidkit.py connect`. Give the person the Google link it prints, and say in plain words: open it, pick the Google account with your videos, tap Allow; the next page will show an error, which is expected; copy the whole address from the top of that page and paste it here.
2. When they paste it: `python3 tools/vidkit.py connect --finish "<what they pasted>"`. Drive now works in this session.
3. `python3 tools/vidkit.py folders` creates their Video Inbox and Video Edits folders.
4. To save it for future sessions: `python3 tools/vidkit.py settings --elevenlabs "<their key>"` prints 1 block. Walk them, one step at a time, through https://claude.ai/code in Safari: tap the environment's name above the message box, add an environment named Video, Network access Full, paste the block into Environment variables, save. Say plainly the block holds passwords and goes only in that box. A new session on the Video environment picks it up.

## Editing

Any request about a video: follow `.claude/skills/phone-edit/SKILL.md`.

- Show labelled samples before any final render. The render refuses without a recorded pick.
- Never download an original, never delete anything in `Video Inbox`, never publish.
- Record every pick and correction with `learn record`, in the person's exact words, and end every session with `python3 tools/vidkit.py learn push`, because this session cannot save changes to the repository.
- The person is on a phone and may not be technical: short messages, plain words, labels to pick from, no file paths unless they ask.
- Never write an `rm` on a path built from a variable or a glob.
