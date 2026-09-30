# What phones record, and why each part breaks an edit

Measured on real iPhone clips on 30 September 2026. Every one of our 3 test files had all 5 of these at once.

| What the phone did | What `probe` prints | What goes wrong if nobody fixes it | What the kit does |
|---|---|---|---|
| **10-bit HDR colour** (HLG, the iPhone default) | `HDR: yes (arib-std-b67)` | Played on most screens and apps after a normal edit, the video looks grey and washed out | Tone-maps to normal colour on every encode, after scaling down, because doing it at 4K took more than twice as long on our laptop test (367 seconds against 142 for the same 2 minutes) |
| **Portrait by metadata.** The pixels are stored sideways and a flag says "turn this 90 degrees" | `rotation: -90 degrees` | A tool that reads the pixel size calls it landscape and crops the wrong way | ffmpeg applies the turn on decode; every size decision uses the turned size |
| **A frame rate that wobbles.** "30 fps" averaged 29.80 | `frame rate: VARIABLE` | Cuts drift out of lip sync, a little more after every cut | Locks the output to exactly 30 frames a second, and cuts every piece to a whole number of frames |
| **Hidden data tracks.** Location, motion, depth | `5 extra data tracks, dropped` | Some tools choke on them or copy them into the output | Takes the first video and first audio track only |
| **Large files.** About 240 MB a minute at 4K on the clip we timed, so 20 GB is roughly 80 minutes | `0.58 GB, 0:02:27` | The cloud machine's disk is 30 GB | Never downloads the file; reads it in place on Drive, a range at a time |

## Before you film, if you can choose

- **1080p is plenty for a phone screen** and about a quarter of the work of 4K. Film 4K only when you want to crop in hard afterwards.
- **30 fps** unless you are filming fast motion. The kit outputs 30 unless you ask `probe` for `--keep60`.
- **A clip-on mic beats the phone's own.** On one of our recordings the phone track sat about 12 dB under the lapel mic, and a phone mic recording 2 people cannot separate their voices afterwards.

## How we know

The 5 rows come from `ffprobe` on 3 iPhone videos (580 MB to 1.3 GB, 4K, HEVC 10-bit HLG, portrait) and one full edit through the kit on 30 September 2026: working copy, samples, a 38-second vertical cut from the 4K original, and all 12 checks passing on ffmpeg 7.1.1, including every planned frame present and picture and sound matching to the frame.
