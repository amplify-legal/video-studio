# How it learns your way of editing

It learns the way a good editor learns a new client: by watching what you pick, writing down what you correct, and asking 1 good question at a time, never a questionnaire.

## What it keeps, all in your Google Drive (`Video Edits/_taste`)

| File | What is in it | Who writes it |
|---|---|---|
| `edit-preferences.md` | Your defaults and rules, in your own words, dated. The newest line wins. | You, or Claude on your yes |
| `edit-log.jsonl` | Every pick and correction: what was offered, what you chose, your exact words | Claude, every video |
| `observations.md` | What Claude noticed about how you film: how you start, restart, move, where the energy is | Claude, 1 or 2 lines per video |
| `questions.json` | The questions still worth asking, ranked by how much the answer would change your edits | Claude marks them asked or answered |
| `edit-defaults.json` | The numbers behind your defaults: pause length, where cuts land, caption size | Claude, on your yes |

## The rules it follows

1. **Your first video comes back fast.** No interview. 2 samples, 1 pick, a finished short.
2. **A correction is a rule immediately.** Say "captions smaller" once and it is written down, in your words, before the session ends.
3. **A pick is only a hint.** The same pick 3 times on 2 different days becomes a proposal, and it only becomes a default when you say yes. A no is not asked again for 30 days.
4. **1 question at most, after the video is delivered, never before.** Never more than 1 a day. Questions your picks already answered are skipped, and the deeper ones (what makes a video feel like yours) wait until you have made a few.
5. **Where cuts land is your call.** Tight on your words, at natural moments when the picture changes, or on the beat of a laugh or a gesture. It starts on the words, notices what you keep and cut, and asks once you have made enough videos for the answer to mean something.
6. **Your style, not a template.** The defaults are a starting point. Nothing from anybody else's taste is copied into yours.

## Changing your mind

Tell any session "from now on..." or edit `edit-preferences.md` in the Drive app. Your newest words beat everything older, including a default.
