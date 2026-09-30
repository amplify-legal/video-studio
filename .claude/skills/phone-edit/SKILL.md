---
name: phone-edit
description: Edit a video recorded on a phone, from the Claude mobile app, inside a Claude Code cloud session. Reads the file in place on Google Drive (up to 20 GB), builds a small working copy in resumable chunks, transcribes it, shows labelled samples for every open choice, renders full quality only after the person picks, checks the file, and puts it back in Drive. Every pick and correction is logged so the next edit asks less. Triggers - "edit my video", "edit the video I just uploaded", "cut this for Instagram", "make a short from my latest clip", "carry on with the edit", "B and D", a pick of labelled options. NOT for publishing anything.
---

# Phone edit

The person is on a phone. Keep every message short, put the choices as labels, and never make them open something you could say in one line.

Every command below is `python3 tools/vidkit.py ...`. A job folder is `/tmp/jobs/<date>-<short-name>`; call it `J`.

## 0. Before the first question

```bash
python3 tools/vidkit.py learn pull      # their taste, from Drive
python3 tools/vidkit.py mount           # a read-only window onto Drive; nothing downloads
python3 tools/vidkit.py find
```

Read `taste/edit-preferences.md` and `taste/machine.md` in full. Anything a preference answers is not a question today.

If `find` lists more than one new video and they did not say which, ask with the names, times and sizes as options. Never guess.

## 1. What the phone recorded

```bash
python3 tools/vidkit.py probe "Video Inbox/<file>" --job J
```

Tell them in 2 lines: length, size, and roughly how long the first pass takes on their machine (`taste/machine.md` has their seconds per second of 4K; 1080p is about a quarter of that). HDR, rotation and a wobbling frame rate are fixed automatically; mention them only if asked. `reference/phone-footage.md` explains each.

## 2. The working copy

```bash
python3 tools/vidkit.py proxy --job J
```

It works in pieces (1 minute for 4K, 2 minutes otherwise) and stops itself before the command time limit (exit code 3, `PAUSED`). **Run it again until it prints `proxy ready`.** Every run finishes at least 1 piece, so it always moves forward. When it finishes it prints this machine's cost per second of footage; if `taste/machine.md` has no line for this resolution yet, add one. Run it in the background when the tool offers, and post one progress line per run, not per piece. A session that went idle and comes back just runs it again; finished pieces are kept.

## 3. The words

A video with no sound skips this step and stays uncaptioned.

```bash
python3 tools/vidkit.py transcribe --job J
```

Names in `taste/keyterms.txt` are spelled right; add any new name they mention before running. The engine used is written into `J/transcript.txt` line 1.

## 4. Look before you plan

```bash
python3 tools/vidkit.py sheet --job J
```

Read the contact sheets as images and read `J/transcript.txt` start to finish. You are looking for: where the real start is (people clear their throat, check the camera, say "okay"), the strongest moment, false starts and retakes, anyone else's face or name on screen, and anything private in the background.

## 5. Ask only what is open

At most 3 questions, in one message, and only those the taste file does not answer. The usual ones:

- **Where is it going?** (sets the format: vertical, horizontal, square)
- **How long?** (a target, or "as long as it needs")
- **Anything to cut or keep?**

If the taste file answers all 3, ask nothing and go straight to samples.

## 6. The edit list and the options

Write `J/edl.json` (see `reference/edl-example.json`): the segments to keep, in source seconds, plus format, framing, captions, loudness and music. Build the first pass from the transcript:

```bash
python3 tools/vidkit.py tighten --job J --edl J/edl-raw.json --out J/edl.json [--drop-fillers]
```

Then write `J/options.json` (see `reference/options-example.json`). Rules:

- **Only open choices become options.** A confirmed default is applied, not offered. An unconfirmed lean from the log is offered as option A with "your usual" in its name.
- **At most 3 choices per round, 2 or 3 options each.** More than that on a phone is noise.
- **Every option has a unique letter for the whole job** (A, B for pace; C, D for captions; E, F, G for the opening). Never reuse a letter within a job, even in round 2.
- **2 options that would look the same are 1 option.** If you cannot say in 4 words how B differs from A, drop B.
- **Opening choices are always a choice** when the start is not obvious: offer 2 or 3 different first sentences.

```bash
python3 tools/vidkit.py samples --job J --options J/options.json
python3 tools/vidkit.py deliver --job J --options
```

A `set` on an option changes only the settings it names; `"captions": {"highlight": "FFD400"}` keeps every other caption setting. Samples are 20 seconds, half size, from the working copy, with the label burned into the corner. Then send one message: the Drive folder (`Video Edits/<job>/options`), and one line per option, label first. Then stop and wait.

## 7. Record the pick, in their words

For every choice they answer, and every correction they add:

```bash
python3 tools/vidkit.py learn record --job J --dimension pace --choice "B tight" --offered "A natural,B tight" --words "<their exact words>"
python3 tools/vidkit.py learn record --job J --kind correction --dimension captions --choice "smaller" --words "<their exact words>"
```

Their words go in verbatim, typos included. If they reject every option, record that too and make a round 2 with new letters.

## 8. The real render

Update `J/edl.json` with the picks, then:

```bash
python3 tools/vidkit.py render --job J --edl J/edl.json
```

It renders from the original, reading only the kept parts, in pieces, and pauses (exit 3) near the time limit like the proxy step. Run it again until it prints `rendered`.

## 9. Check it, then look at it

```bash
python3 tools/vidkit.py verify --job J
```

12 mechanical checks, including every planned frame being there and picture and sound being the same length (loudness and dead air are skipped on a silent video). **Red means fix it and render again, never deliver.** Green is not done: pull 3 frames (start, middle, last second) with ffmpeg and look at them, and read the captions around every cut for a clipped or misspelled word. A fault any viewer would notice is yours to fix without asking.

```bash
python3 tools/vidkit.py deliver --job J
```

Tell them in 1 line where it is in Drive and how long it is.

## 10. Learn

```bash
python3 tools/vidkit.py learn propose
```

If it proposes a default, ask once, with their own past words quoted: *"You've picked the tight pace 3 times this week ('cut the ums'). Make it the default?"* Record the answer with `learn record --kind accepted` (or `--kind declined`) and the same dimension and choice, so it is not asked again. On yes, write it into `taste/edit-preferences.md` under Defaults, dated, and put any number it sets (pause lengths, caption size, music level) into `taste/edit-defaults.json`. A no is not proposed again for 30 days.

Always finish with:

```bash
python3 tools/vidkit.py learn push
```

That is the step that makes the next session smarter. The repository cannot carry it: a cloud session can only push to a side branch, so the taste lives on Drive.

## Never

- Render the final before a recorded pick (`render` refuses; do not use `--no-pick-needed` except to re-render something already picked).
- Delete or overwrite anything in `Video Inbox`.
- Download a big original to the machine. The window reads it in place; the disk is 30 GB.
- Publish, post or share a link anywhere.
- Write an `rm` on a path built from a variable or a glob.
