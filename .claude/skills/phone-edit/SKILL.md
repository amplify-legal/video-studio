---
name: phone-edit
description: Edit a video recorded on a phone, from the Claude mobile app, inside a Claude Code cloud session. Reads the file in place on Google Drive (up to 20 GB), builds a small working copy in resumable pieces, transcribes it, finds where the picture changes, shows labelled samples, renders full quality only after a pick, checks the file, puts it back in Drive, and learns the person's own way of editing over time. Triggers - "edit my video", "edit my newest video", "make a short", "cut this for Instagram", "carry on", "B and D", a pick of labelled options, "from now on...". NOT for publishing anything.
---

# Phone edit

The person is on a phone and may not be technical. Short messages, plain words, labels to pick from. Every command is `python3 tools/vidkit.py ...`; a job folder is `/tmp/jobs/<date>-<short-name>`, called `J` below.

## The 2 things that matter most

1. **Speed to a finished video.** Their first video should come back as a finished short as fast as the machine allows, with 1 round of samples and no interview. Questions come later, 1 at a time.
2. **Their style, not ours.** Every person films differently and has their own idea of where a cut belongs. Defaults here are a starting point. What they pick, what they correct and what they say wins, and it is written down so the next session starts from it.

## 0. Before anything

```bash
bash tools/setup.sh                        # only if ffmpeg or rclone is missing
python3 tools/vidkit.py learn pull         # their taste, from their Drive
python3 tools/vidkit.py mount
python3 tools/vidkit.py find
```

Read everything in `taste/`: `edit-preferences.md` (their words, the newest line wins), `observations.md` (what earlier sessions noticed), `edit-defaults.json` (numbers). Never ask what these already answer. If `find` shows several new videos and they didn't say which, list them (name, time, size) and let them pick.

## 1. First video ever (no `edit-log.jsonl` yet): the fast path

- **Ask nothing up front.** Assume a vertical short for Instagram or TikTok, under 60 seconds, captions on, unless their message said otherwise.
- Run steps 2 to 4, then find the strongest 30 to 60 seconds: a complete thought with a clear start, ideally the moment with the most energy or the clearest point.
- **1 round, 2 samples, 1 choice:** A is that moment tightened; B is a second strong moment, or the same moment starting on a different first sentence. 12 to 20 seconds each.
- On their pick, render, verify, deliver. Then ask the 1 question from step 9.

## 2. What the phone recorded, and the working copy

```bash
python3 tools/vidkit.py probe "Video Inbox/<file>" --job J
python3 tools/vidkit.py proxy --job J      # run again until it prints "proxy ready"
```

Tell them the length and roughly how long the first pass takes (`taste/machine.md` if it exists), in 1 line. Everything else the probe finds (HDR, rotation, frame rate) is fixed automatically; mention it only if asked. The proxy works in pieces and pauses itself near the time limit; every run finishes at least 1 piece.

## 3. Words and picture changes

```bash
python3 tools/vidkit.py transcribe --job J
python3 tools/vidkit.py scenes --job J
python3 tools/vidkit.py sheet --job J
```

Read the contact sheets as images, `J/transcript.txt` start to finish, and `J/scenes.json`. A video with no sound skips transcribe.

## 4. Where cuts land

Words are only one signal. A cut can land:
- **on the words:** between sentences, after a pause (`tighten`, the default)
- **between moments:** where the picture changes: they move, look away, walk off, the shot changes (`tighten --snap scenes`)
- **on the beat of the delivery:** after a laugh, a gesture, a reaction the transcript cannot see (you find these on the contact sheets)

Which one feels right is their creative call. Until the taste file says, use words, and when two cut points are close, prefer the one at a picture change. **Never cut inside a word**, and never land a cut where the transcript is clean but the picture jumps badly: look at the frames either side.

```bash
python3 tools/vidkit.py tighten --job J --edl J/edl-raw.json --out J/edl.json [--drop-fillers] [--snap scenes]
```

## 5. Options

Write `J/options.json` (see `reference/options-example.json`):
- **Only open choices become options.** A confirmed default is applied, not offered. A lean from the log that is not confirmed yet is offered as A, named "your usual".
- **First video:** 1 choice, 2 options. **Later:** up to 3 choices, 2 or 3 options each, and fewer as defaults settle.
- **Every option has its own letter for the whole job**, never reused, and 2 options that look the same are 1 option.

```bash
python3 tools/vidkit.py samples --job J --options J/options.json
python3 tools/vidkit.py deliver --job J --options
```

Send 1 message: the Drive folder (`Video Edits/<job>/options`), then 1 line per option, letter first. Wait.

## 6. Record the pick, in their words

```bash
python3 tools/vidkit.py learn record --job J --dimension <what the choice was about> --choice "<letter and name>" --offered "<the letters>" --words "<their exact words>"
```

Use these names for `--dimension` so picks, questions and defaults line up: `format`, `length`, `opening`, `pace`, `cut_on`, `captions`, `music`, `framing`, `style`, `rules`, `keyterms`.

Every correction they add ("captions smaller", "start on the second sentence") is recorded with `--kind correction` AND written into `taste/edit-preferences.md` under Rules the same session, quoted and dated. A correction is a rule the moment they say it.

## 7. Render, check, look

```bash
python3 tools/vidkit.py render --job J --edl J/edl.json    # run again until it prints "rendered"
python3 tools/vidkit.py verify --job J
```

Red means fix and render again. Green is not done: look at 3 frames (start, middle, end) and read the captions around every cut. A fault anyone would notice is yours to fix without asking.

```bash
python3 tools/vidkit.py deliver --job J
```

Tell them in 1 line where it is and how long it is.

## 8. Notice how they film

After every video, write 1 or 2 things you noticed that would change how the next one is edited: how they start, how they restart a sentence, whether they move, where the energy is, what they cut or kept against your guess.

```bash
python3 tools/vidkit.py learn observe --dimension <filming|pace|opening|cuts|...> --words "<what you noticed>"
```

These are guidelines, not rules. If the same thing shows up in 3 videos, say it back to them once, as a question, and let them confirm it into the taste file.

## 9. 1 question, at most, after delivery

```bash
python3 tools/vidkit.py learn next-question --mark
```

If it prints `ASK`, ask exactly that question, after the finished video is delivered, never before. If it prints `NONE`, ask nothing. It never asks more than 1 a day, skips anything their picks already answered, and holds deeper questions until they have made a few videos. When they answer:

```bash
python3 tools/vidkit.py learn answer --qid <id> --words "<their exact words>"
```

then write the answer into `edit-preferences.md`, quoted and dated. If they ignore the question, drop it; it does not come back that day.

## 10. Turn picks into defaults, then save

```bash
python3 tools/vidkit.py learn propose
```

A default is proposed only after the same pick 3 times on 2 different days. Ask once, quoting their own past words. Record the answer with `learn record --kind accepted` or `--kind declined`; on yes, write it into `edit-preferences.md` under Defaults and any number into `edit-defaults.json` (for example `"cut_on": "scenes"`, `"pause_keep": 0.4`, `"drop_fillers": true`).

**Always end with:**

```bash
python3 tools/vidkit.py learn push
```

This session cannot save to the repository, so their taste lives in their Drive. Without this step, everything learned today is lost.

## Never

- Render the final before a recorded pick.
- Ask more than 1 taste question in a session, or any before their first video is delivered.
- Copy someone else's style into theirs. The defaults are where they start, not where they should end up.
- Delete or overwrite anything in `Video Inbox`. Download a big original. Publish anything.
- Write an `rm` on a path built from a variable or a glob.
