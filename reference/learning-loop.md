# How it gets better

3 files, all in `taste/`, all saved to your Drive at `Video Edits/_taste` at the end of every session and read back at the start of the next.

| File | Who writes it | What is in it |
|---|---|---|
| `edit-log.jsonl` | The session, every pick and every correction | Date, the choice, what else was offered, and your exact words |
| `edit-preferences.md` | You, or the session on your yes | Your defaults and your rules, in your words, dated |
| `edit-defaults.json` | The session, on your yes | The numbers behind a default: pause lengths, caption size, music level |

## The ladder

1. **A pick** is used for that video and logged. Nothing else changes.
2. **The same pick 3 times, on at least 2 different days,** becomes a proposal. `vidkit.py learn propose` does the counting over your last 5 picks on that choice. Corrections are rules straight away (step 5) and never counted as picks.
3. **You say yes,** and it becomes a default. From then on it is applied and not offered.
4. **You say no,** and that is written down too, so it is not proposed again for 30 days.
5. **A correction in your own words** ("captions smaller", "never start on 'so'") is written into `edit-preferences.md` the same session, quoted and dated. A correction is a rule the moment you say it; it does not need 3 days.

Why 3 picks on 2 days: one day's picks can all come from the same mood or the same video. The same answer on different days is a taste.

## What changes as it learns

- **Week 1:** 3 questions and 3 rounds of samples per video.
- **Once the defaults settle:** usually no questions, one round with 1 or 2 genuinely open choices, and the first sample often already right.

That is what we expect from how the ladder works, not something we have measured over weeks. Your log will show it: "See what it has learned" in `INSTALL-PROMPT.md` prints your defaults and what is still open.

## Changing your mind

Edit `edit-preferences.md` in the Drive app, or tell a session "from now on...". Your newest words win over anything older, including a default.
