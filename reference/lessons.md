# What we learned rendering with Claude, and what it changed in this pack

We edited podcasts, trainings, workshops and shorts with Claude through August and September 2026, on a laptop, on a rented cloud machine, and once inside a Claude cloud session. Each line below is something that went wrong for us and the thing in this kit that stops it going wrong for you.

## Getting the footage in

- **Moving big files through connector tools fails quietly.** A 3 GB file timed out twice on a Drive download through a connector, and a 376 MB upload was refused outright. **So:** the kit never moves the original. rclone reads it in place.
- **A fresh cloud machine has nothing on it.** No ffmpeg, no fonts, no downloads folder. **So:** a setup script installs everything, and it is cached after the first session.
- **A cloud session's disk filled up at the last step of a long edit** because the repository it cloned carried gigabytes of history. **So:** the repository holds code and taste only, never video.

## Words and cuts

- **Names get misheard unless the transcriber is told them first.** Fixing them afterwards with find and replace missed some every time. **So:** `taste/keyterms.txt` goes to the transcriber with every job.
- **The transcriber attaches a pause to the end of the word before it.** A cut at "end of word" can leave half a second of silence. **So:** `tighten` cuts between words and trims every pause to a length you choose.
- **Our pause rule for talking-head video:** a pause of 1 second or more shrinks to 0.6 seconds, and nothing shorter than 1.2 seconds survives unless it carries real words. It is the starting default, and the first thing your picks will override.
- **Sound and picture drift apart over many cuts** when each piece's audio is cut to the planned length and its video to the frame grid. On our own test of this kit on 30 September 2026 it was 0.37 seconds across 18 cuts of an iPhone clip, and a length check on the whole file still passed. **So:** every piece is snapped to whole frames, and `verify` compares picture and sound lengths directly.

## Options before rendering

- **The best results came from picking, not describing.** When the top 3 of each open choice were rendered as short samples on one page and picked, the full render was right first time. When options were described in words, it took rounds. **So:** samples are labelled, short, half size, and made before anything final.
- **2 options that look the same waste a round.** **So:** the skill drops any option it cannot tell apart in 4 words.
- **A label must mean the same thing everywhere.** "Option 2" once meant one file in the folder and another on the page, and the wrong one shipped. **So:** labels are burned into the video itself and never reused in a job.

## Renders that finish

- **A render with no end time can run forever.** One wrote a 21.7 GB file before anyone noticed. **So:** every encode here carries an explicit length or frame count.
- **Long commands get cut off.** In a Claude cloud session a foreground command is allowed about 10 minutes and a backgrounded one about 30 more, per Anthropic's docs. **So:** heavy steps work in pieces of 1 to 2 minutes, save progress after each, stop themselves before the limit and continue when run again.
- **"Working" means the output file is growing.** A busy processor meter proved nothing on 2 occasions. **So:** the kit prints a line per finished piece.

## Checking

- **Green is not done.** Automated checks catch what is mechanically broken, never whether the cut is good. **So:** `verify` says so in its last line, and the skill looks at frames and reads captions before delivering.
- **Check the finished file, not the plan.** Arithmetic on the edit list said the timing was right while the file itself had drifted. **So:** every check reads the output file.
- **Captions under about 78 pixels on a 1920-tall video get missed on a phone.** That floor comes from our own caption rules for shorts, and we had to set it 3 times because it once lived in one project's script. **So:** it is in the kit itself and cannot be set lower.
- **A music bed at about 2 percent volume sits under speech without competing.** Our earlier defaults of 6 and 15 percent were too loud every time. **So:** the default is -34 dB, about 2 percent.

## Getting better

- **A correction that lives only in one edit gets made again.** **So:** every pick and correction goes into your taste log in your words, and the log lives on Drive where every future session reads it.
- **One example is a guideline, not a rule.** **So:** a default is proposed only after the same pick 3 times on 2 different days, and only you can accept it.
