#!/usr/bin/env python3
"""vidkit: phone video editing inside a Claude Code cloud session.

One file, standard library only, drives ffmpeg, ffprobe and rclone.

The shape of a job, in the order a session runs it:

    vidkit.py mount                      # a local window onto your Drive (rclone serve http)
    vidkit.py find                       # the newest videos in your inbox folder
    vidkit.py probe  <file>  --job J     # what the phone actually recorded, and the plan
    vidkit.py proxy  --job J             # small copy + audio, in chunks, resumable
    vidkit.py transcribe --job J         # word timings (ElevenLabs Scribe, Deepgram fallback)
    vidkit.py sheet  --job J             # stills so the session can SEE the footage
    vidkit.py samples --job J --options J/options.json   # labelled A/B/C samples, nothing final
    vidkit.py render --job J --edl J/edl.json            # the full-quality cut, in chunks
    vidkit.py verify --job J             # the mechanical checks; green is not done
    vidkit.py deliver --job J            # uploads the result next to the source
    vidkit.py tighten --job J --edl in.json --out out.json [--drop-fillers]   # cut pauses and ums
    vidkit.py learn pull|record|show|propose|push      # the taste that makes the next edit better

Why it is built this way (every reason is a thing that broke):

* The cloud sandbox has a 30 GB disk, so a 20 GB source is NEVER downloaded. rclone serves
  the remote over local HTTP with range requests and ffmpeg reads only what it needs.
* A foreground command gets about 10 minutes and a backgrounded one about 30 more, so every
  heavy step works in chunks, writes its progress to disk, stops itself at --max-minutes and
  is simply run again to continue. Nothing is lost when a session pauses.
* Phones record 10-bit HDR (HLG), variable frame rate, rotated by metadata, with extra data
  tracks. Each of those breaks a naive edit, so probe detects them and every encode fixes
  them: tone-map to SDR, constant 30 fps, rotation applied, first video and audio track only.
* Every render carries an explicit duration (-t), so no encode can run forever.
* Nothing final renders before a person picked from labelled options.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

VERSION = "1.0 (2026-09-30)"
PORT = int(os.environ.get("VIDKIT_PORT", "8787"))
REMOTE = os.environ.get("VIDKIT_REMOTE", "gdrive:")          # rclone remote, with trailing colon
INBOX = os.environ.get("VIDKIT_INBOX", "Video Inbox")        # folder on the remote the phone uploads to
OUTBOX = os.environ.get("VIDKIT_OUTBOX", "Video Edits")      # where options and finished cuts go
TASTE_DIR = Path(os.environ.get("VIDKIT_TASTE") or Path(__file__).resolve().parent.parent / "taste").resolve()
CHUNK_SECONDS = 120        # 1080p and smaller; 4K uses 60 (see proxy)
VIDEO_EXT = (".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".3gp")


# ----------------------------------------------------------------------------- helpers

def say(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"STOP: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def need(tool: str) -> None:
    if not shutil.which(tool):
        die(f"{tool} is not installed. The environment setup script installs it; "
            f"see SETUP.md step 4, or run: sudo apt-get install -y {tool}")


def run(cmd: list[str], quiet: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-12:])
        die(f"command failed ({cmd[0]}):\n{tail}")
    if not quiet:
        say(p.stdout)
    return p


def ff(args: list[str]) -> None:
    """ffmpeg with the flags every call in this kit carries."""
    run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-threads", "0"] + args)


def jload(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def jsave(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def job_dir(arg: str) -> Path:
    d = Path(arg).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


class Budget:
    """Stops a chunked step before the harness kills it. Run the same command again to go on."""

    def __init__(self, minutes: float):
        self.end = time.time() + minutes * 60

    def left(self) -> float:
        return self.end - time.time()

    def ok(self, need_seconds: float) -> bool:
        return self.left() > need_seconds


def fmt_t(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s // 3600):d}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def source_url(src: str) -> str:
    """A local path stays a path; a remote path becomes the rclone window URL."""
    if src.startswith(("http://", "https://")) or Path(src).exists():
        return src
    return f"http://127.0.0.1:{PORT}/" + urllib.parse.quote(src)


def font_file() -> str:
    for name in ("Inter:bold", "DejaVu Sans:bold", "Arial:bold", "Helvetica:bold"):
        if shutil.which("fc-match"):
            p = subprocess.run(["fc-match", "-f", "%{file}", name], capture_output=True, text=True)
            if p.returncode == 0 and Path(p.stdout.strip()).exists():
                return p.stdout.strip()
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        if Path(f).exists():
            return f
    die("no font found for labels and captions; install fonts-dejavu-core")
    return ""


# ----------------------------------------------------------------------------- connect

AUTH_LOG = Path(os.environ.get("TMPDIR", "/tmp")) / "vidkit-authorize.log"


def cmd_connect(a) -> None:
    """Connect Google Drive from a phone, no computer needed.

    Step 1 (no --finish): starts rclone's sign-in listener on this cloud machine and prints the
    Google link. The person opens it on the phone, picks their account and taps Allow. Google
    then sends the phone to an address starting http://127.0.0.1:53682/ which the phone cannot
    open, so it shows an error page. That is expected: they copy that whole address.
    Step 2 (--finish "<address>"): hands the address to the listener here, which trades it for
    the Drive token, saves it for this session and prints it once for the environment settings."""
    need("rclone")
    if a.finish:
        q = urllib.parse.urlsplit(a.finish.strip()).query
        if "code=" not in q:
            die("that address has no code in it. Copy the WHOLE address from the error page, "
                "starting http://127.0.0.1:53682/")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:53682/?{q}", timeout=30).read()
        except Exception as ex:
            die(f"the sign-in listener is gone ({ex}). Run `vidkit.py connect` again; links last a few minutes.")
        tok = None
        for _ in range(40):
            time.sleep(0.5)
            txt = AUTH_LOG.read_text() if AUTH_LOG.exists() else ""
            if "invalid_grant" in txt:
                die("Google refused that code (it was used already or timed out). Run `vidkit.py connect` again.")
            i = txt.find('{"access_token"')
            if i >= 0:
                tok = txt[i: txt.index("}", i) + 1]
                break
        if not tok or '"refresh_token"' not in tok:
            die("no token came back. Run `vidkit.py connect` again.")
        conf = Path.home() / ".config" / "rclone" / "rclone.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(f"[gdrive]\ntype = drive\nscope = drive\ntoken = {tok}\n")
        say("CONNECTED. Drive works in this session now.")
        say("For every future session, save this in the environment settings as RCLONE_CONFIG_GDRIVE_TOKEN "
            "(copy everything between the 2 lines, braces included). It is a password for your Drive: "
            "paste it only into that box.")
        say("-" * 40)
        say(tok)
        say("-" * 40)
        return
    log = AUTH_LOG.open("w")
    subprocess.Popen(["rclone", "authorize", "drive", "--auth-no-open-browser"], stdout=log, stderr=log,
                     start_new_session=True)
    local = None
    for _ in range(40):
        time.sleep(0.5)
        txt = AUTH_LOG.read_text()
        i = txt.find("http://127.0.0.1:53682/auth?state=")
        if i >= 0:
            local = txt[i:].split()[0]
            break
    if not local:
        die("rclone did not start its sign-in listener; see " + str(AUTH_LOG))

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kw):
            return None
    try:
        urllib.request.build_opener(NoRedirect).open(local, timeout=10)
        die("rclone did not hand back a Google link")
    except urllib.error.HTTPError as ex:
        google = ex.headers.get("Location")
    say("Open this link on your phone, choose the Google account that holds your videos, and tap Allow:")
    say(google)
    say("Your phone will then show an error page (it cannot open 127.0.0.1). That is expected. "
        "Copy the WHOLE address from the address bar of that page and paste it here.")


def cmd_folders(a) -> None:
    """Creates the 2 Drive folders and reports free space, so nobody has to make them by hand."""
    need("rclone")
    for f in (INBOX, OUTBOX):
        run(["rclone", "mkdir", f"{REMOTE}{f}"])
    say(f"folders ready in Google Drive: {INBOX}, {OUTBOX}")
    p = subprocess.run(["rclone", "about", REMOTE, "--json"], capture_output=True, text=True)
    try:
        info = json.loads(p.stdout)
        free = info.get("free")
        if free is not None:
            say(f"free space: {free / 1e9:.1f} GB" + ("  (a 20 GB video needs a bigger Google plan)" if free < 25e9 else ""))
    except Exception:
        pass


def cmd_settings(a) -> None:
    """Prints the one block a person pastes into their environment's variables box, so they paste
    once instead of 5 times. Uses the Drive token saved by `connect` in this session."""
    conf = Path.home() / ".config" / "rclone" / "rclone.conf"
    tok = None
    if conf.exists():
        for line in conf.read_text().splitlines():
            if line.startswith("token = "):
                tok = line[len("token = "):].strip()
    if not tok:
        die("Drive is not connected in this session yet. Run `vidkit.py connect` first.")
    lines = ["RCLONE_CONFIG_GDRIVE_TYPE=drive", "RCLONE_CONFIG_GDRIVE_SCOPE=drive",
             f"RCLONE_CONFIG_GDRIVE_TOKEN={tok}", "BASH_DEFAULT_TIMEOUT_MS=600000"]
    if a.elevenlabs:
        lines.append(f"ELEVENLABS_API_KEY={a.elevenlabs.strip()}")
    if a.deepgram:
        lines.append(f"DEEPGRAM_API_KEY={a.deepgram.strip()}")
    say("Copy everything between the 2 lines and paste it into the environment's variables box. "
        "It contains passwords: paste it only there.")
    say("-" * 40)
    say("\n".join(lines))
    say("-" * 40)


# ----------------------------------------------------------------------------- mount / find

def cmd_mount(a) -> None:
    need("rclone")
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
        say(f"window already open: http://127.0.0.1:{PORT}/  ({REMOTE})")
        return
    except Exception:
        pass
    log = open(Path(os.environ.get("TMPDIR", "/tmp")) / "vidkit-rclone.log", "a")
    subprocess.Popen(
        ["rclone", "serve", "http", REMOTE, "--addr", f"127.0.0.1:{PORT}", "--read-only",
         "--dir-cache-time", "30s", "--buffer-size", "64M"],
        stdout=log, stderr=log, start_new_session=True)
    for _ in range(40):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2)
            say(f"window open: http://127.0.0.1:{PORT}/ serving {REMOTE} read-only. "
                f"Nothing is downloaded until ffmpeg asks for a range.")
            return
        except Exception:
            continue
    die("rclone did not start. Check the remote with: rclone lsd " + REMOTE +
        "  (TROUBLESHOOTING.md, 'rclone did not start')")


def cmd_find(a) -> None:
    need("rclone")
    folder = a.folder or INBOX
    p = run(["rclone", "lsjson", "-R", "--files-only", f"{REMOTE}{folder}"])
    items = [i for i in json.loads(p.stdout or "[]") if i["Path"].lower().endswith(VIDEO_EXT)]
    items.sort(key=lambda i: i.get("ModTime", ""), reverse=True)
    if not items:
        say(f"no videos in {REMOTE}{folder}. Upload from the phone into that folder first.")
        return
    say(f"newest videos in {REMOTE}{folder}:")
    for i in items[: a.n]:
        say(f"  {i['ModTime'][:16].replace('T', ' ')}  {i['Size'] / 1e9:6.2f} GB  {folder}/{i['Path']}")


# ----------------------------------------------------------------------------- probe

def ffprobe_json(url: str) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", url])
    return json.loads(p.stdout)


def cmd_probe(a) -> None:
    need("ffprobe")
    J = job_dir(a.job)
    url = source_url(a.source)
    info = ffprobe_json(url)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    au = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if not v:
        die("no video track in that file")
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = int(v.get("tags", {}).get("rotate", rot)) if "rotate" in v.get("tags", {}) else rot
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w
    num, den = (v.get("avg_frame_rate") or "0/1").split("/")
    avg = float(num) / float(den) if float(den) else 0.0
    rnum, rden = (v.get("r_frame_rate") or "0/1").split("/")
    rfr = float(rnum) / float(rden) if float(rden) else 0.0
    transfer = v.get("color_transfer", "")
    hdr = transfer in ("arib-std-b67", "smpte2084")
    dur = float(info["format"].get("duration", v.get("duration", 0)) or 0)
    size = int(info["format"].get("size", 0) or 0)
    extra = [s.get("codec_type") for s in info["streams"] if s.get("codec_type") not in ("video", "audio")]
    fps_target = 60 if (a.keep60 and rfr > 45) else 30
    probe = {
        "source": a.source, "url": url, "size_gb": round(size / 1e9, 2), "duration_s": round(dur, 2),
        "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"), "bit_depth": 10 if "10" in (v.get("pix_fmt") or "") else 8,
        "display_w": w, "display_h": h, "orientation": "portrait" if h > w else ("square" if h == w else "landscape"),
        "rotation_metadata": rot, "hdr": transfer if hdr else None,
        "fps_nominal": round(rfr, 3), "fps_average": round(avg, 3),
        "variable_frame_rate": bool(avg and abs(avg - rfr) > 0.05),
        "audio": {"codec": au.get("codec_name"), "rate": int(au.get("sample_rate", 0)), "channels": au.get("channels")} if au else None,
        "extra_tracks": extra,
        "plan": {
            "tonemap_to_sdr": hdr,
            "constant_fps": fps_target,
            "rotation": "applied by ffmpeg on decode" if rot else "none",
            "map": "first video + first audio only" + (f" ({len(extra)} data tracks dropped)" if extra else ""),
            "audio_out": "AAC 48 kHz stereo",
            "chunks": math.ceil(dur / CHUNK_SECONDS) if dur else None,
        },
    }
    jsave(J / "probe.json", probe)
    say(f"{a.source}: {probe['size_gb']} GB, {fmt_t(dur)}, {probe['codec']} {probe['bit_depth']}-bit, "
        f"{w}x{h} {probe['orientation']}")
    say("  HDR: " + (f"yes ({transfer}), will tone-map to SDR so it does not look washed out" if hdr else "no"))
    say("  frame rate: " + (f"VARIABLE (avg {avg:.2f}), will lock to {fps_target} fps so audio stays in sync"
                            if probe["variable_frame_rate"] else f"{rfr:g} fps, will output {fps_target}"))
    if rot:
        say(f"  rotation: {rot} degrees in metadata, applied on decode")
    if extra:
        say(f"  {len(extra)} extra data tracks (phone metadata), dropped")
    if au and int(au.get("sample_rate", 0)) != 48000:
        say(f"  audio at {au.get('sample_rate')} Hz, will output 48000")
    say(f"  written: {J / 'probe.json'}")


# ----------------------------------------------------------------------------- filters

def video_chain(probe: dict, w: int, h: int, fit: str = "crop", x: float = 0.5, y: float = 0.5,
                speed: float = 1.0, fps: int | None = None) -> str:
    """Framing first, then tone-map, then speed and frame rate.

    Scale BEFORE the tone-map: tone-mapping works in 32-bit float per pixel, and doing it on a
    4K frame made a 2-minute iPhone clip take 6 minutes on a laptop (measured 2026-09-30).
    At the output size it is a fraction of that, and the picture is indistinguishable."""
    parts = ["setpts=PTS-STARTPTS"]           # a seek into a phone file rarely lands on 0
    hdr = probe.get("hdr")
    if fit == "pad":
        parts.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                     f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        parts.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                     f"crop={w}:{h}:(iw-{w})*{x}:(ih-{h})*{y}")
    if hdr:
        tin = "arib-std-b67" if hdr == "arib-std-b67" else "smpte2084"
        parts.append(f"zscale=tin={tin}:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl=100,format=gbrpf32le,"
                     "zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
    if speed != 1.0:
        parts.append(f"setpts=PTS/{speed}")
    parts.append(f"fps={fps or probe['plan']['constant_fps']}")
    # clone the last frame for a moment so -frames:v can always reach its exact count
    parts.append("tpad=stop_mode=clone:stop_duration=0.5,setsar=1,format=yuv420p")
    return ",".join(parts)


def atempo(speed: float) -> str:
    if speed == 1.0:
        return "anull"
    chain, s = [], speed
    while s > 2.0:
        chain.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        chain.append("atempo=0.5"); s /= 0.5
    chain.append(f"atempo={s:.4f}")
    return ",".join(chain)


FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080),
           "vertical-720": (720, 1280), "horizontal-720": (1280, 720)}


def proxy_size(probe: dict) -> tuple[int, int]:
    w, h = probe["display_w"], probe["display_h"]
    if w >= h:
        return (int(round(540 * w / h / 2)) * 2, 540)
    return (540, int(round(540 * h / w / 2)) * 2)


# ----------------------------------------------------------------------------- proxy

def cmd_proxy(a) -> None:
    need("ffmpeg")
    J = job_dir(a.job)
    probe = jload(J / "probe.json") or die("run probe first")
    url = probe["url"]
    dur = probe["duration_s"]
    chunk = 60 if max(probe["display_w"], probe["display_h"]) >= 3000 else CHUNK_SECONDS
    n = math.ceil(dur / chunk)
    pw, ph = proxy_size(probe)
    C = J / "proxy_chunks"; C.mkdir(exist_ok=True)
    budget = Budget(a.max_minutes)
    state = jload(J / "proxy_state.json", {"done": [], "seconds_per_chunk": None})
    if state.get("chunk", chunk) != chunk:
        state = {"done": [], "seconds_per_chunk": None}
    state["chunk"] = chunk
    did = 0
    for i in range(n):
        if i in state["done"]:
            continue
        est = state["seconds_per_chunk"] or 90
        # always finish at least 1 chunk per run, so a slow machine still moves forward
        if did and not budget.ok(est * 1.3):
            say(f"PAUSED at chunk {i + 1}/{n} to stay inside the time limit. Run the same command again to continue.")
            sys.exit(3)
        t0 = time.time()
        ss = i * chunk
        t = min(chunk, dur - ss)
        vf = video_chain(probe, pw, ph, fit="crop", fps=30)
        # an exact frame count per chunk, or the picture drifts behind the sound at every boundary
        ff(["-ss", f"{ss:.3f}", "-t", f"{t + 0.5:.3f}", "-i", url, "-map", "0:v:0", "-an",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-g", "30",
            "-frames:v", str(max(1, round(t * 30))), str(C / f"v{i:04d}.mp4")])
        if probe.get("audio"):
            ff(["-ss", f"{ss:.3f}", "-t", f"{t:.3f}", "-i", url, "-map", "0:a:0", "-vn",
                "-af", f"asetpts=PTS-STARTPTS,aresample=48000,apad=whole_dur={round(t * 30) / 30:.4f}",
                "-ac", "1", "-c:a", "pcm_s16le", "-t", f"{round(t * 30) / 30:.4f}", str(C / f"a{i:04d}.wav")])
        took = time.time() - t0
        did += 1
        state["done"].append(i)
        state["seconds_per_chunk"] = round(max(took, state["seconds_per_chunk"] or 0) * 0.5 + took * 0.5, 1)
        state.setdefault("cost", []).append(round(took / max(t, 0.1), 3))
        jsave(J / "proxy_state.json", state)
        say(f"  chunk {i + 1}/{n} done in {took:.0f}s")
    # stitch
    (C / "v.txt").write_text("".join(f"file 'v{i:04d}.mp4'\n" for i in range(n)))
    ff(["-f", "concat", "-safe", "0", "-i", str(C / "v.txt"), "-c", "copy", str(J / "proxy_video.mp4")])
    if probe.get("audio"):
        (C / "a.txt").write_text("".join(f"file 'a{i:04d}.wav'\n" for i in range(n)))
        ff(["-f", "concat", "-safe", "0", "-i", str(C / "a.txt"), "-c", "copy", str(J / "audio.wav")])
        ff(["-i", str(J / "proxy_video.mp4"), "-i", str(J / "audio.wav"), "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-shortest",
            "-movflags", "+faststart", str(J / "proxy.mp4")])
    else:
        shutil.copy(J / "proxy_video.mp4", J / "proxy.mp4")
    spc = sum(state.get("cost", [])) / max(1, len(state.get("cost", [])))
    say(f"proxy ready: {J / 'proxy.mp4'} ({pw}x{ph}, 30 fps, SDR). Edits are planned on this; "
        f"the final render goes back to the original.")
    if spc:
        say(f"  cost on this machine: {spc:.2f} seconds of work per second of this footage "
            f"({probe['display_w']}x{probe['display_h']}, {probe['codec']}). Save this in taste/machine.md.")


# ----------------------------------------------------------------------------- transcribe

def _multipart(fields: list[tuple[str, str]], file_field: str, path: Path, mime: str):
    boundary = uuid.uuid4().hex
    out = []
    for k, v in fields:
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    out.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
               f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
    out.append(path.read_bytes())
    out.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def cmd_transcribe(a) -> None:
    J = job_dir(a.job)
    wav = J / "audio.wav"
    if not (jload(J / "probe.json") or {}).get("audio"):
        say("this video has no sound, so there is nothing to transcribe; captions stay off")
        jsave(J / "words.json", {"engine": "none (no audio)", "words": []})
        return
    if not wav.exists():
        die("no audio.wav: run proxy first")
    m4a = J / "audio_for_asr.m4a"
    if not m4a.exists():
        ff(["-i", str(wav), "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "48k", str(m4a)])
    keyterms = [k.strip() for k in (a.keyterms or "").split(",") if k.strip()]
    kfile = TASTE_DIR / "keyterms.txt"
    if kfile.exists():
        keyterms += [l.strip() for l in kfile.read_text().splitlines() if l.strip() and not l.startswith("#")]
    words, engine = None, None
    if os.environ.get("ELEVENLABS_API_KEY") and a.engine in ("auto", "scribe"):
        fields = [("model_id", "scribe_v2"), ("timestamps_granularity", "word"), ("diarize", "true")]
        fields += [("keyterms", k) for k in keyterms[:100]]
        body, ctype = _multipart(fields, "file", m4a, "audio/mp4")
        req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=body, method="POST",
                                     headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": ctype})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=1800).read())
            words = [{"w": x["text"], "start": x["start"], "end": x["end"], "speaker": x.get("speaker_id")}
                     for x in r.get("words", []) if x.get("type", "word") == "word"]
            engine = "elevenlabs scribe_v2" + (" + keyterms" if keyterms else "")
        except Exception as e:
            say(f"  Scribe failed ({e}); trying Deepgram")
    if words is None and os.environ.get("DEEPGRAM_API_KEY") and a.engine in ("auto", "deepgram"):
        q = [("model", "nova-3"), ("smart_format", "true"), ("punctuate", "true"), ("diarize", "true")]
        q += [("keyterm", k) for k in keyterms[:100]]
        req = urllib.request.Request("https://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(q),
                                     data=m4a.read_bytes(), method="POST",
                                     headers={"Authorization": "Token " + os.environ["DEEPGRAM_API_KEY"],
                                              "Content-Type": "audio/mp4"})
        r = json.loads(urllib.request.urlopen(req, timeout=1800).read())
        alt = r["results"]["channels"][0]["alternatives"][0]
        words = [{"w": x.get("punctuated_word", x["word"]), "start": x["start"], "end": x["end"],
                  "speaker": x.get("speaker")} for x in alt.get("words", [])]
        engine = "deepgram nova-3" + (" + keyterms" if keyterms else "") + " (word ends approximate)"
    if words is None:
        die("no transcription key. Add ELEVENLABS_API_KEY (or DEEPGRAM_API_KEY) to the cloud environment; "
            "SETUP.md step 4.")
    jsave(J / "words.json", {"engine": engine, "keyterms": keyterms, "words": words})
    # a readable transcript with timestamps every sentence, for the session to plan from
    lines, cur, t0 = [], [], None
    for w in words:
        if t0 is None:
            t0 = w["start"]
        cur.append(w["w"])
        if w["w"].endswith((".", "?", "!")) or len(cur) > 28:
            lines.append(f"[{fmt_t(t0)}] {' '.join(cur)}"); cur, t0 = [], None
    if cur:
        lines.append(f"[{fmt_t(t0)}] {' '.join(cur)}")
    (J / "transcript.txt").write_text(f"# engine: {engine}\n" + "\n".join(lines) + "\n")
    say(f"transcribed with {engine}: {len(words)} words -> {J / 'transcript.txt'}")


# ----------------------------------------------------------------------------- sheet

def cmd_sheet(a) -> None:
    J = job_dir(a.job)
    probe = jload(J / "probe.json") or die("run probe first")
    src = J / "proxy.mp4"
    if not src.exists():
        die("run proxy first")
    every = a.every or max(5, int(probe["duration_s"] / 48))
    out = J / "sheets"; out.mkdir(exist_ok=True)
    ff(["-i", str(src), "-vf", f"fps=1/{every},scale=320:-2,"
        f"drawtext=fontfile='{font_file()}':text='%{{pts\\:hms}}':x=6:y=6:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6,"
        "tile=4x3:padding=4", "-frames:v", "12", str(out / "sheet-%02d.jpg")])
    say(f"contact sheets every {every}s: {out}  (read them as images before planning the cut)")


# ----------------------------------------------------------------------------- scenes

def cmd_scenes(a) -> None:
    """Finds where the picture changes (the camera moves, a new shot starts, the person walks
    off) on the working copy. Cuts can then land between moments, not only between words."""
    J = job_dir(a.job)
    src = J / "proxy.mp4"
    if not src.exists():
        die("run proxy first")
    thr = a.threshold or (jload(TASTE_DIR / "edit-defaults.json", {}) or {}).get("scene_threshold", 0.3)
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(src), "-an", "-vf",
                        f"select='gt(scene,{thr})',metadata=print", "-f", "null", "-"],
                       capture_output=True, text=True)
    cuts, t = [], None
    for line in p.stderr.splitlines():
        if "pts_time:" in line:
            t = float(line.split("pts_time:")[1].split()[0])
        elif "lavfi.scene_score=" in line and t is not None:
            cuts.append({"t": round(t, 3), "score": round(float(line.split("=")[1]), 3)})
            t = None
    jsave(J / "scenes.json", {"threshold": thr, "changes": cuts})
    say(f"{len(cuts)} picture changes found (threshold {thr}) -> {J / 'scenes.json'}")
    for c in cuts[:30]:
        say(f"  {fmt_t(c['t'])}  score {c['score']}")


def _snap(t: float, points: list[float], words: list[dict], reach: float) -> float:
    """Moves a cut to the nearest picture change within `reach` seconds, unless that lands inside a word."""
    best = min(points, key=lambda p: abs(p - t), default=None)
    if best is None or abs(best - t) > reach:
        return t
    if any(w["start"] < best < w["end"] for w in words):
        return t
    return best


# ----------------------------------------------------------------------------- captions

def ass_time(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}"


def build_ass(words: list[dict], segments: list[dict], w: int, h: int, style: dict, path: Path,
              limit: float | None = None) -> int:
    """Maps source word times onto the output timeline through the kept segments."""
    # 78 px on a 1920-tall frame is the floor we learned the hard way; never smaller
    size = max(int(style.get("size_pct", 5.5) / 100 * h), int(78 * h / 1920))
    per = int(style.get("words_per_line", 3 if h > w else 6))
    ypos = style.get("y_pct", 70 if h > w else 85)
    upper = style.get("uppercase", False)
    color = style.get("color", "FFFFFF")
    hl = style.get("highlight", "")
    font = style.get("font", "Inter")
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: C,{font},{size},&H00{color[4:6]}{color[2:4]}{color[0:2]},&H000000FF,&H00000000,&H80000000,"
        f"1,0,0,0,100,100,0,0,1,{max(2, size // 12)},0,5,{w // 12},{w // 12},0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    events, out_t, count = [], 0.0, 0
    for seg in segments:
        sp = float(seg.get("speed", 1.0))
        inside = [x for x in words if x["start"] >= seg["in"] - 0.05 and x["end"] <= seg["out"] + 0.05
                  and not (style.get("hide_fillers", True) and _is_filler(x))]
        for i in range(0, len(inside), per):
            grp = inside[i: i + per]
            st = out_t + (max(grp[0]["start"], seg["in"]) - seg["in"]) / sp
            en = out_t + (min(grp[-1]["end"], seg["out"]) - seg["in"]) / sp
            nxt = inside[i + per]["start"] if i + per < len(inside) else None
            if nxt is not None:
                en = max(en, out_t + (min(nxt, seg["out"]) - seg["in"]) / sp - 0.02)
            if limit is not None and st >= limit:
                continue
            text = " ".join(x["w"] for x in grp)
            text = text.upper() if upper else text
            if hl:
                parts = text.split(" ")
                parts[-1] = "{\\c&H" + hl[4:6] + hl[2:4] + hl[0:2] + "&}" + parts[-1]
                text = " ".join(parts)
            events.append(f"Dialogue: 0,{ass_time(st)},{ass_time(en)},C,,0,0,0,,{{\\pos({w // 2},{int(h * ypos / 100)})}}{text}")
            count += 1
        out_t += (seg["out"] - seg["in"]) / sp
    path.write_text(header + "\n".join(events) + "\n")
    return count


# ----------------------------------------------------------------------------- edl

def load_edl(p: Path) -> dict:
    e = jload(p) or die(f"cannot read {p}")
    if not isinstance(e.get("segments"), list) or not e["segments"]:
        die("the edit list has no segments, or 'segments' is not a list of {\"in\": s, \"out\": s}")
    if not all(isinstance(x, dict) and "in" in x and "out" in x for x in e["segments"]):
        die("every segment needs an \"in\" and an \"out\" in seconds")
    for s in e["segments"]:
        if float(s["out"]) <= float(s["in"]):
            die(f"segment {s} ends before it starts")
    e.setdefault("format", "vertical")
    e.setdefault("fit", "crop")
    e.setdefault("framing", {"x": 0.5, "y": 0.5})
    e.setdefault("speed", 1.0)
    e.setdefault("captions", {"on": True})
    e.setdefault("loudness_lufs", -14)
    return e


def seg_list(e: dict, fps: int = 30) -> list[dict]:
    """Every segment is snapped to a whole number of output frames, so the picture and the sound
    of each piece are exactly the same length and nothing drifts across 50 cuts. Our long-form
    edits learned this the hard way: audio cut to the planned length and video cut to the frame
    grid disagree by up to half a frame per cut, and it adds up."""
    out = []
    for s in e["segments"]:
        sp = float(s.get("speed", e["speed"]))
        a, b = float(s["in"]), float(s["out"])
        frames = max(1, round((b - a) / sp * fps))
        out.append({"in": a, "out": a + frames / fps * sp, "speed": sp})
    return out


def out_duration(e: dict) -> float:
    return sum((s["out"] - s["in"]) / s["speed"] for s in seg_list(e))


FILLERS = {"um", "uh", "erm", "er", "ah", "hmm", "mm", "uhm", "umm"}


def _is_filler(w: dict) -> bool:
    return w["w"].lower().strip(".,!?;:-'\"") in FILLERS


def cmd_tighten(a) -> None:
    """Splits kept segments at pauses in the words: a gap of --gap seconds or more shrinks to --keep.
    --drop-fillers also cuts out um and uh. Cuts land between words, never inside one. A piece is
    dropped only when it carries no real word and is shorter than --min."""
    J = job_dir(a.job)
    words = (jload(J / "words.json") or die("transcribe first"))["words"]
    e = load_edl(Path(a.edl))
    prefs = jload(TASTE_DIR / "edit-defaults.json", {})
    gap = a.gap or prefs.get("pause_gap", 1.0)
    keep = a.keep or prefs.get("pause_keep", 0.6)
    mn = a.min or prefs.get("min_piece", 1.2)
    drop = a.drop_fillers or prefs.get("drop_fillers", False)
    pad = min(keep / 2, 0.12) if drop else keep / 2
    pieces = []
    for s in e["segments"]:
        ws = [w for w in words if w["start"] >= s["in"] - 0.05 and w["end"] <= s["out"] + 0.05]
        cur, prev = None, None
        for w in ws:
            if drop and _is_filler(w):
                if cur:
                    cur["out"] = min(prev["end"] + pad, w["start"])
                    pieces.append(cur); cur = None
                continue
            if cur is None:
                cur = {**s, "in": max(s["in"], w["start"] - pad, prev["end"] if prev else 0), "n": 0}
            elif w["start"] - prev["end"] >= gap:
                cur["out"] = prev["end"] + keep / 2
                pieces.append(cur)
                cur = {**s, "in": w["start"] - keep / 2, "n": 0}
            cur["n"] += 1
            prev = w
        if cur and prev:
            cur["out"] = min(s["out"], prev["end"] + keep / 2)
            pieces.append(cur)
    # a piece is dropped only when it carries no real word at all and is shorter than --min
    snap = a.snap or prefs.get("cut_on", "words")
    if snap == "scenes":
        sc = [c["t"] for c in (jload(J / "scenes.json") or {}).get("changes", [])]
        if not sc:
            say("  no scenes.json yet (run `scenes`), so cuts stay on the words")
        reach = prefs.get("scene_reach", 0.6)
        for p in pieces:
            p["in"], p["out"] = _snap(p["in"], sc, words, reach), _snap(p["out"], sc, words, reach)
    out = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items() if k != "n"}
           for p in pieces if p["out"] > p["in"] and (p["n"] >= 1 or p["out"] - p["in"] >= mn)]
    before, e["segments"] = out_duration(e), out
    jsave(Path(a.out), e)
    say(f"tightened: {fmt_t(before)} -> {fmt_t(out_duration(e))}, {len(out)} pieces "
        f"(pauses of {gap}s or more cut to {keep}s{', fillers cut' if drop else ''}) -> {a.out}")


# ----------------------------------------------------------------------------- render core

PIECE_SECONDS = 60


def _ass_s(t: str) -> float:
    h, m, sec = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def piece_ass(global_ass: Path, t0: float, d: float, out: Path) -> None:
    """The captions that fall inside one piece, moved onto that piece's own clock."""
    head, events = [], []
    for line in global_ass.read_text().splitlines():
        if not line.startswith("Dialogue:"):
            head.append(line); continue
        parts = line.split(",", 9)
        st, en = _ass_s(parts[1]) - t0, _ass_s(parts[2]) - t0
        if en <= 0 or st >= d:
            continue
        parts[1], parts[2] = ass_time(max(0.0, st)), ass_time(min(d, en))
        events.append(",".join(parts))
    out.write_text("\n".join(head + events) + "\n")


def render_pieces(segs: list[dict], fps: int) -> list[dict]:
    """Long kept stretches are split into pieces of at most PIECE_SECONDS of output, each a whole
    number of frames, so no single command can outlast the session's time limit."""
    out, t = [], 0.0
    for s in segs:
        total_frames = round((s["out"] - s["in"]) / s["speed"] * fps)
        step = PIECE_SECONDS * fps
        done = 0
        while done < total_frames:
            n = min(step, total_frames - done)
            a = s["in"] + done / fps * s["speed"]
            out.append({"in": a, "out": a + n / fps * s["speed"], "speed": s["speed"], "frames": n, "t": t})
            t += n / fps
            done += n
    return out


def render_cut(J: Path, e: dict, src: str, probe: dict, W: int, H: int, work: Path, out: Path,
               budget: Budget, crf: int, preset: str, limit: float | None = None, label: str | None = None,
               quiet: bool = False) -> bool:
    """Everything that touches pixels happens in the piece pass: framing, tone-map, captions and
    the option label are all burned per piece, resumable, each piece at most a minute of output.
    The finishing pass only joins the pieces (no re-encode) and treats the sound, so a long cut
    never needs one long command. Returns False if it paused to stay inside the time limit."""
    work.mkdir(parents=True, exist_ok=True)
    fr = e.get("framing", {})
    fps = probe["plan"]["constant_fps"]
    segs = seg_list(e, fps)
    if limit is not None:                       # a sample renders only the first `limit` output seconds
        kept, acc = [], 0.0
        for s in segs:
            d = (s["out"] - s["in"]) / s["speed"]
            if acc >= limit:
                break
            if acc + d > limit:
                s = dict(s, out=s["in"] + (limit - acc) * s["speed"])
                d = limit - acc
            kept.append(s); acc += d
        segs = kept
    pieces = render_pieces(segs, fps)
    total = sum(p["frames"] for p in pieces) / fps
    # captions are built once on the output timeline, so a caption can run across a cut or a piece
    cap = e.get("captions", {})
    words = (jload(J / "words.json") or {}).get("words")
    ass_path = work / "captions.ass"
    has_caps = bool(cap.get("on") and words and build_ass(words, segs, W, H, cap, ass_path, limit))
    overlay = []
    if has_caps:
        overlay.append(f"ass={ass_path}")
    if label:
        fs = int(H * 0.045)
        safe = label.replace(":", "\\:").replace("'", "")
        overlay.append(f"drawtext=fontfile='{font_file()}':text='{safe}':x=w*0.04:y=h*0.04:fontsize={fs}:"
                       f"fontcolor=white:box=1:boxcolor=0x000000@0.7:boxborderw={fs // 3}")
    state = jload(work / "state.json", {"done": [], "rate": None})
    # anything that changes the pixels invalidates finished pieces, including this file's own code
    sig = json.dumps([pieces, W, H, fr, e.get("fit"), crf, preset, src, probe.get("hdr"), overlay,
                      ass_path.read_text() if has_caps else "",
                      video_chain(probe, W, H, e.get("fit", "crop"), fr.get("x", 0.5), fr.get("y", 0.5)),
                      Path(__file__).stat().st_mtime])
    if state.get("sig") != sig:
        state = {"done": [], "rate": None, "sig": sig}
    did = 0
    for i, p in enumerate(pieces):
        if i in state["done"]:
            continue
        d = p["frames"] / fps
        est = d * (state["rate"] or 3.0)
        if did and not budget.ok(est + 30):
            say(f"PAUSED before piece {i + 1}/{len(pieces)} to stay inside the time limit. Run the same command again.")
            return False
        t0 = time.time()
        vf = video_chain(probe, W, H, e.get("fit", "crop"), fr.get("x", 0.5), fr.get("y", 0.5), p["speed"])
        if overlay:
            # each piece gets its own caption file on its own clock (shifting timestamps inside the
            # filter chain cost a frame per piece and broke sync; measured 2026-09-30)
            layers = list(overlay)
            if has_caps:
                piece_ass(ass_path, p["t"], d, work / f"s{i:04d}.ass")
                layers[0] = f"ass={work / f's{i:04d}.ass'}"
            vf += "," + ",".join(layers)
        ff(["-ss", f"{p['in']:.3f}", "-t", f"{p['out'] - p['in'] + 0.5:.3f}", "-i", src, "-map", "0:v:0", "-an",
            "-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", "60",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-frames:v", str(p["frames"]), str(work / f"s{i:04d}.mp4")])
        if probe.get("audio"):
            ff(["-ss", f"{p['in']:.3f}", "-t", f"{p['out'] - p['in']:.3f}", "-i", src, "-map", "0:a:0", "-vn",
                "-af", "asetpts=PTS-STARTPTS," + atempo(p["speed"]) + f",aresample=48000,apad=whole_dur={d:.4f}",
                "-ac", "2", "-c:a", "pcm_s16le", "-t", f"{d:.4f}", str(work / f"s{i:04d}.wav")])
        else:
            ff(["-f", "lavfi", "-t", f"{d:.4f}", "-i", "anullsrc=r=48000:cl=stereo", "-c:a", "pcm_s16le",
                str(work / f"s{i:04d}.wav")])
        did += 1
        state["done"].append(i)
        state["rate"] = round((time.time() - t0) / max(d, 0.1), 2)
        jsave(work / "state.json", state)
        if not quiet:
            say(f"  piece {i + 1}/{len(pieces)} ({d:.1f}s) done, {state['rate']}x realtime cost")
    n = len(pieces)
    (work / "v.txt").write_text("".join(f"file 's{i:04d}.mp4'\n" for i in range(n)))
    (work / "a.txt").write_text("".join(f"file 's{i:04d}.wav'\n" for i in range(n)))
    ff(["-f", "concat", "-safe", "0", "-i", str(work / "v.txt"), "-c", "copy", str(work / "joined.mp4")])
    ff(["-f", "concat", "-safe", "0", "-i", str(work / "a.txt"), "-c", "copy", str(work / "joined.wav")])
    # finishing pass: the picture is copied untouched; only the sound is treated
    inputs = ["-i", str(work / "joined.mp4"), "-i", str(work / "joined.wav")]
    af = f"[1:a]loudnorm=I={e['loudness_lufs']}:TP=-1.5:LRA=11,aresample=48000[voice]"
    amap = "[voice]"
    music = e.get("music")
    if music and music.get("file"):
        if not Path(music["file"]).exists():
            die(f"music file not found: {music['file']} (music lives in taste/music, pulled from Drive)")
        inputs += ["-stream_loop", "-1", "-i", music["file"]]
        db = float(music.get("db", -34))            # -34 dB is about 2% volume, our tested bed level
        af += (f";[2:a]volume={db}dB,aresample=48000,afade=t=out:st={max(0, total - 2):.2f}:d=2[bed];"
               f"[voice][bed]amix=inputs=2:duration=first:normalize=0[mix]")
        amap = "[mix]"
    ff(inputs + ["-filter_complex", af, "-map", "0:v", "-map", amap, "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{total:.4f}",
                 "-movflags", "+faststart", str(out)])
    return True


def cmd_samples(a) -> None:
    """Every option rendered short, small and labelled. Nothing here is the final cut."""
    need("ffmpeg")
    J = job_dir(a.job)
    probe = jload(J / "probe.json") or die("run probe first")
    spec = jload(Path(a.options)) or die(f"cannot read {a.options}")
    base = load_edl(Path(spec.get("base_edl", J / "edl.json")))
    src = str(J / "proxy.mp4") if (J / "proxy.mp4").exists() and not a.full else probe["url"]
    pprobe = dict(probe, hdr=None) if src.endswith("proxy.mp4") else probe   # proxy is already SDR
    budget = Budget(a.max_minutes)
    outdir = J / "options"; outdir.mkdir(exist_ok=True)
    seconds = float(spec.get("sample_seconds", 20))
    made = []
    for ch in spec["choices"]:
        for opt in ch["options"]:
            e = json.loads(json.dumps(base))
            for k, v in opt.get("set", {}).items():
                if isinstance(v, dict) and isinstance(e.get(k), dict):
                    e[k] = {**e[k], **v}           # change one caption setting, keep the rest
                else:
                    e[k] = v
            e = load_edl_obj(e)
            W, H = FORMATS[e["format"]]
            W, H = W // 2, H // 2                      # samples are half size: fast, and small on a phone
            label = f"{opt['label']}  {opt['name']}"
            fname = f"{ch['dimension']}-{opt['label']}-{slug(opt['name'])}.mp4"
            if (outdir / fname).exists():
                made.append(fname); continue
            ok = render_cut(J, e, src, pprobe, W, H, J / "work" / "samples" / fname[:-4], outdir / fname,
                            budget, crf=28, preset="veryfast", limit=seconds, label=label, quiet=True)
            if not ok:
                sys.exit(3)
            made.append(fname)
            say(f"  {fname}")
    # a page for a laptop, and the list for the phone
    rows = "".join(
        f"<section><h2>{ch['dimension']}: {ch.get('question', '')}</h2><div class=row>" + "".join(
            f"<figure><video src='{ch['dimension']}-{o['label']}-{slug(o['name'])}.mp4' controls playsinline preload=metadata></video>"
            f"<figcaption><b>{o['label']}</b> {o['name']}<br>{o.get('why', '')}</figcaption></figure>"
            for o in ch["options"]) + "</div></section>" for ch in spec["choices"])
    (outdir / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Options</title><style>body{font:18px/1.4 system-ui;margin:0;padding:clamp(16px,4vw,48px);"
        "background:#fff;color:#111}.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}"
        "video{width:100%;border-radius:8px;background:#000}figure{margin:0}</style>"
        f"<h1>Pick one per row</h1><p>Short, small samples. Nothing final renders until you pick.</p>{rows}")
    jsave(outdir / "offered.json", {"offered_at": now(), "spec": spec, "files": made})
    say(f"{len(made)} labelled samples in {outdir}. Run `vidkit.py deliver --job {a.job} --options` "
        f"to put them in {OUTBOX} on your phone.")


def load_edl_obj(e: dict) -> dict:
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"vidkit-{uuid.uuid4().hex}.json"
    tmp.write_text(json.dumps(e))
    try:
        return load_edl(tmp)
    finally:
        tmp.unlink()


def slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")[:40] or "x"


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="minutes")


def cmd_render(a) -> None:
    need("ffmpeg")
    J = job_dir(a.job)
    probe = jload(J / "probe.json") or die("run probe first")
    e = load_edl(Path(a.edl))
    picked = J / "picked.json"
    if not picked.exists() and not a.no_pick_needed:
        die("nothing has been picked yet. Show the options first (vidkit.py samples), record the pick "
            "with `vidkit.py learn record`, then render. --no-pick-needed overrides this for a re-render.")
    W, H = FORMATS[e["format"]]
    out = J / f"final-{e['format']}.mp4"
    ok = render_cut(J, e, probe["url"], probe, W, H, J / "work" / "final", out, Budget(a.max_minutes),
                    crf=a.crf, preset=a.preset)
    if not ok:
        sys.exit(3)
    say(f"rendered {out} ({W}x{H}, {fmt_t(out_duration(e))}). Now: vidkit.py verify --job {a.job}")


# ----------------------------------------------------------------------------- verify

def cmd_verify(a) -> None:
    J = job_dir(a.job)
    outs = sorted(J.glob("final-*.mp4"), key=lambda f: f.stat().st_mtime)
    if not outs:
        die("no final render in the job folder")
    out = outs[-1]
    probe = jload(J / "probe.json") or {}
    fps = (probe.get("plan") or {}).get("constant_fps", 30)
    has_audio = bool(probe.get("audio"))
    e = load_edl(Path(a.edl or J / "edl.json"))
    exp = out_duration(e)
    info = ffprobe_json(str(out))
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    au = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    dur = float(info["format"]["duration"])
    W, H = FORMATS[e["format"]]
    checks = []

    def c(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    c("duration", abs(dur - exp) < 0.25, f"{dur:.2f}s against {exp:.2f}s planned")
    nf = int(v.get("nb_frames") or 0)
    want = sum(round((x["out"] - x["in"]) / x["speed"] * fps) for x in seg_list(e, fps))
    c("every frame there", nf == want, f"{nf} frames against {want} planned")
    ad = float(au.get("duration", dur)) if au else 0.0
    # the muxer rounds AAC to 1024-sample frames (21 ms), so allow whichever is larger
    c("picture and sound same length", abs(nf / fps - ad) <= max(1.0 / fps, 0.025),
      f"video {nf / fps:.3f}s, audio {ad:.3f}s (a gap here means lips drift out of sync)")
    c("size", (int(v["width"]), int(v["height"])) == (W, H), f"{v['width']}x{v['height']}")
    c("frame rate", v.get("r_frame_rate") == f"{fps}/1", f"{v.get('r_frame_rate')}")
    c("SDR colour", v.get("color_transfer", "bt709") in ("bt709", None, "unknown"), f"{v.get('color_transfer')}")
    c("8-bit 4:2:0", v.get("pix_fmt") == "yuv420p", v.get("pix_fmt"))
    c("audio 48 kHz", au is not None and au.get("sample_rate") == "48000", au and au.get("sample_rate"))
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(out), "-t", "3", "-vf",
                        "blackdetect=d=0.5:pix_th=0.1", "-an", "-f", "null", "-"], capture_output=True, text=True)
    c("no black opening", "black_start" not in p.stderr, "first 3 seconds")
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(out), "-af",
                        "ebur128=framelog=quiet", "-vn", "-f", "null", "-"], capture_output=True, text=True)
    lufs = None
    for line in p.stderr.splitlines()[::-1]:
        if line.strip().startswith("I:") and "LUFS" in line:
            lufs = float(line.split()[1]); break
    if has_audio:
        c("loudness", lufs is not None and abs(lufs - e["loudness_lufs"]) <= 1.5, f"{lufs} LUFS, target {e['loudness_lufs']}")
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(out), "-af",
                        "silencedetect=n=-45dB:d=2.5", "-vn", "-f", "null", "-"], capture_output=True, text=True)
    gaps = p.stderr.count("silence_start")
    if has_audio:
        c("no dead air over 2.5s", gaps == 0, f"{gaps} gaps")
    head = out.read_bytes()[:64 * 1024]
    c("plays while downloading", b"moov" in head, "moov atom at the front")
    res = {"file": str(out), "checked_at": now(), "checks": checks, "all_pass": all(x["pass"] for x in checks)}
    jsave(J / "verify.json", res)
    for x in checks:
        say(f"  {'PASS' if x['pass'] else 'FAIL'}  {x['check']}: {x['detail']}")
    say("GREEN. Green means nothing mechanical is broken; it does not mean the cut is good. "
        "Watch the first 30 seconds before delivering." if res["all_pass"] else
        "RED: fix the failures before this goes anywhere.")
    sys.exit(0 if res["all_pass"] else 2)


# ----------------------------------------------------------------------------- deliver

def cmd_deliver(a) -> None:
    need("rclone")
    J = job_dir(a.job)
    name = J.name
    dest = f"{REMOTE}{OUTBOX}/{name}"
    if a.options:
        run(["rclone", "copy", str(J / "options"), f"{dest}/options", "--include", "*.mp4"])
        say(f"options uploaded to {OUTBOX}/{name}/options. Open that folder in the Drive app on your phone.")
        return
    v = jload(J / "verify.json")
    if not v or not v.get("all_pass"):
        die("verify has not passed on this render. Run vidkit.py verify first.")
    run(["rclone", "copy", v["file"], dest])
    say(f"delivered: {OUTBOX}/{name}/{Path(v['file']).name}")


# ----------------------------------------------------------------------------- learn

def cmd_learn(a) -> None:
    TASTE_DIR.mkdir(parents=True, exist_ok=True)
    log = TASTE_DIR / "edit-log.jsonl"
    # The taste lives on Drive, not in git: a cloud session can only push to a side branch,
    # so anything it learned would be gone next session unless somebody merged it.
    if a.action in ("pull", "push"):
        need("rclone")
        remote = f"{REMOTE}{OUTBOX}/_taste"
        if a.action == "pull":
            p = subprocess.run(["rclone", "lsf", remote], capture_output=True, text=True)
            if p.returncode != 0:
                say(f"nothing learned yet ({OUTBOX}/_taste does not exist on Drive); starting from taste/ in the repository")
                return
            run(["rclone", "copy", remote, str(TASTE_DIR)])
            names = sorted(x.name for x in TASTE_DIR.iterdir())
            say(f"taste pulled from {OUTBOX}/_taste: " + (", ".join(names) or "(empty)"))
        else:
            run(["rclone", "copy", str(TASTE_DIR), remote])
            say(f"taste saved to {OUTBOX}/_taste on Drive, where the next session reads it")
        return
    if a.action == "record":
        if not a.dimension or not a.choice:
            die("record needs --dimension and --choice")
        row = {"at": now(), "job": a.job and Path(a.job).name, "kind": a.kind, "dimension": a.dimension,
               "choice": a.choice, "offered": [o for o in (a.offered or "").split(",") if o],
               "words": a.words or ""}
        with log.open("a") as f:
            f.write(json.dumps(row) + "\n")
        if a.job and a.kind == "pick":
            p = Path(a.job) / "picked.json"
            d = jload(p, {})
            d[a.dimension] = {"choice": a.choice, "at": row["at"]}
            jsave(p, d)
        say(f"recorded {a.kind}: {a.dimension} = {a.choice}")
        return
    rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
    qfile = TASTE_DIR / "questions.json"
    if a.action == "observe":
        if not a.words:
            die("observe needs --words: what you noticed about how they film or edit, in plain words")
        obs = TASTE_DIR / "observations.md"
        if not obs.exists():
            obs.write_text("# How I film (noticed by Claude, not rules)\n\nEach line is something a session noticed. "
                           "Guidelines until I confirm them.\n\n")
        with obs.open("a") as f:
            f.write(f"- {now()[:10]} · {a.dimension or 'general'} · {a.words}\n")
        say("noted in taste/observations.md")
        return
    if a.action in ("next-question", "answer"):
        qs = jload(qfile, [])
        if a.action == "answer":
            q = next((x for x in qs if x["id"] == a.qid), None) or die(f"no question {a.qid}")
            q.update(status="answered", answered_at=now(), answer=a.words or "")
            jsave(qfile, qs)
            with log.open("a") as f:
                f.write(json.dumps({"at": now(), "kind": "correction", "dimension": q["dimension"],
                                    "choice": "answer", "words": a.words or ""}) + "\n")
            say(f"recorded. Now write it into edit-preferences.md, in their words, dated.")
            return
        recent = [x for x in qs if x.get("asked_at") and x["asked_at"] >= (
            _dt.datetime.now().astimezone() - _dt.timedelta(hours=20)).isoformat()]
        if recent:
            say("NONE: a question was already asked in the last 20 hours. Ask nothing.")
            return
        prefs_text = (TASTE_DIR / "edit-preferences.md").read_text().lower() if (TASTE_DIR / "edit-preferences.md").exists() else ""
        settled = {r["dimension"] for r in rows if r.get("kind") == "accepted"}
        picks = {}
        for r in rows:
            if r.get("kind", "pick") == "pick":
                picks[r["dimension"]] = picks.get(r["dimension"], 0) + 1
        for q in sorted(qs, key=lambda x: x["rank"]):
            if q.get("status") != "open":
                continue
            if q["dimension"] in settled or f"· {q['dimension']} ·" in prefs_text:
                q["status"] = "answered-by-picks"; continue
            if picks.get(q["dimension"], 0) >= 3:
                q["status"] = "answered-by-picks"; continue     # their picks already say it
            if q.get("after_videos", 1) > len({r.get("job") for r in rows if r.get("job")}):
                continue                                       # too early for this one
            if a.mark:
                q["asked_at"] = now()
            jsave(qfile, qs)
            say(f"ASK ({q['id']}): {q['question']}")
            say(f"  why it matters: {q['why']}")
            return
        jsave(qfile, qs)
        say("NONE: nothing worth asking right now.")
        return
    if a.action == "show":
        prefs = TASTE_DIR / "edit-preferences.md"
        say(prefs.read_text() if prefs.exists() else "(no edit-preferences.md yet)")
        say(f"\n{len(rows)} logged picks and corrections")
        return
    if a.action == "propose":
        # a pattern is only proposed when the same answer won on 3 separate days; one day is a guideline
        # only picks count toward a default; a yes stops the question for good, a no for 30 days
        by, closed = {}, set()
        cutoff = (_dt.datetime.now().astimezone() - _dt.timedelta(days=30)).isoformat()
        for r in rows:
            if r.get("kind") == "accepted" or (r.get("kind") == "declined" and r["at"] >= cutoff):
                closed.add((r["dimension"], r["choice"]))
            if r.get("kind", "pick") == "pick":
                by.setdefault(r["dimension"], []).append(r)
        props = []
        for dim, rs in by.items():
            last = rs[-5:]
            top = max(set(r["choice"] for r in last), key=lambda c: sum(r["choice"] == c for r in last))
            wins = [r for r in last if r["choice"] == top]
            days = {r["at"][:10] for r in wins}
            if len(wins) >= 3 and len(days) >= 2 and (dim, top) not in closed:
                props.append({"dimension": dim, "default": top, "evidence": f"{len(wins)} of last {len(last)}, "
                              f"on {len(days)} days", "their_words": [r["words"] for r in wins if r["words"]][:3]})
        if not props:
            say("nothing to propose yet: a default needs the same pick 3 times, on at least 2 different days.")
            return
        for p in props:
            say(f"PROPOSE  {p['dimension']}: default to '{p['default']}'  ({p['evidence']})")
            for w in p["their_words"]:
                say(f"         their words: \"{w}\"")
        say("Ask the person before writing any of these into edit-preferences.md. Record the answer with "
            "`learn record --kind accepted` or `--kind declined` and the same --dimension and --choice.")


# ----------------------------------------------------------------------------- selftest

def cmd_selftest(a) -> None:
    """Builds a fake phone clip (10-bit HEVC flagged HLG, 12 seconds by default) and runs the
    whole chain on it with no network. Proves the machine, not the taste."""
    need("ffmpeg")
    J = job_dir(a.job or os.path.join(os.environ.get("TMPDIR", "/tmp"), "vidkit-selftest"))
    size = "3840x2160" if a.uhd else "1920x1080"
    secs = max(12, a.seconds)
    src = J / "fake-phone.mov"
    if not src.exists():
        ff(["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=44100", "-t", str(secs),
            "-vf", "format=yuv420p10le", "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error",
            "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
            "-c:a", "aac", "-ar", "44100", str(src)])
    t_start = time.time()
    a.source, a.keep60 = str(src), False
    cmd_probe(a)
    a.max_minutes = 10
    cmd_proxy(a)
    jsave(J / "words.json", {"engine": "selftest", "words": [
        {"w": w, "start": 0.5 + i * 0.6, "end": 0.5 + i * 0.6 + 0.5} for i, w in
        enumerate("this is a fake phone clip that proves the whole chain works end to end".split())]})
    say(f"  timing: probe+proxy of {secs}s at {size} took {time.time() - t_start:.0f}s")
    jsave(J / "edl.json", {"format": "vertical", "segments": [{"in": 0.5, "out": 4.0}, {"in": 6.0, "out": secs - 2.5}],
                           "captions": {"on": True}, "loudness_lufs": -14})
    jsave(J / "options.json", {"sample_seconds": 5, "choices": [{"dimension": "captions", "question": "captions?",
          "options": [{"label": "A", "name": "on", "set": {}},
                      {"label": "B", "name": "off", "set": {"captions": {"on": False}}}]}]})
    a.options, a.full = str(J / "options.json"), False
    cmd_samples(a)
    jsave(J / "picked.json", {"captions": {"choice": "A"}})
    a.edl, a.crf, a.preset, a.no_pick_needed = str(J / "edl.json"), 20, "veryfast", False
    t_r = time.time()
    cmd_render(a)
    say(f"  timing: final render of {out_duration(load_edl(J / 'edl.json')):.0f}s output took {time.time() - t_r:.0f}s")
    try:
        cmd_verify(a)
    except SystemExit as ex:
        if ex.code:
            die("selftest render failed verify")
    say(f"SELFTEST PASSED in {J}")


# ----------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(prog="vidkit.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("connect", help="connect Google Drive from a phone")
    p.add_argument("--finish", help="the address from the error page after tapping Allow"); p.set_defaults(fn=cmd_connect)
    sub.add_parser("folders", help="create the Drive folders").set_defaults(fn=cmd_folders)
    p = sub.add_parser("settings", help="print the one block of environment variables to paste")
    p.add_argument("--elevenlabs"); p.add_argument("--deepgram"); p.set_defaults(fn=cmd_settings)
    sub.add_parser("mount").set_defaults(fn=cmd_mount)
    p = sub.add_parser("find"); p.add_argument("--folder"); p.add_argument("-n", type=int, default=10); p.set_defaults(fn=cmd_find)
    p = sub.add_parser("probe"); p.add_argument("source"); p.add_argument("--job", required=True)
    p.add_argument("--keep60", action="store_true", help="keep 60 fps footage at 60 instead of 30"); p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("proxy"); p.add_argument("--job", required=True); p.add_argument("--max-minutes", type=float, default=8); p.set_defaults(fn=cmd_proxy)
    p = sub.add_parser("transcribe"); p.add_argument("--job", required=True); p.add_argument("--keyterms")
    p.add_argument("--engine", choices=["auto", "scribe", "deepgram"], default="auto"); p.set_defaults(fn=cmd_transcribe)
    p = sub.add_parser("sheet"); p.add_argument("--job", required=True); p.add_argument("--every", type=int); p.set_defaults(fn=cmd_sheet)
    p = sub.add_parser("tighten"); p.add_argument("--job", required=True); p.add_argument("--edl", required=True)
    p.add_argument("--out", required=True); p.add_argument("--gap", type=float); p.add_argument("--keep", type=float)
    p.add_argument("--min", type=float); p.add_argument("--drop-fillers", action="store_true")
    p.add_argument("--snap", choices=["words", "scenes"], help="where cuts land; default from the taste file")
    p.set_defaults(fn=cmd_tighten)
    p = sub.add_parser("scenes"); p.add_argument("--job", required=True); p.add_argument("--threshold", type=float)
    p.set_defaults(fn=cmd_scenes)
    p = sub.add_parser("samples"); p.add_argument("--job", required=True); p.add_argument("--options", required=True)
    p.add_argument("--max-minutes", type=float, default=8); p.add_argument("--full", action="store_true",
                   help="sample from the original instead of the proxy (slower, exact colour)"); p.set_defaults(fn=cmd_samples)
    p = sub.add_parser("render"); p.add_argument("--job", required=True); p.add_argument("--edl", required=True)
    p.add_argument("--max-minutes", type=float, default=8); p.add_argument("--crf", type=int, default=18)
    p.add_argument("--preset", default="medium"); p.add_argument("--no-pick-needed", action="store_true"); p.set_defaults(fn=cmd_render)
    p = sub.add_parser("verify"); p.add_argument("--job", required=True); p.add_argument("--edl"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("deliver"); p.add_argument("--job", required=True); p.add_argument("--options", action="store_true"); p.set_defaults(fn=cmd_deliver)
    p = sub.add_parser("learn"); p.add_argument("action", choices=["pull", "record", "show", "propose", "push", "observe", "next-question", "answer"])
    p.add_argument("--qid"); p.add_argument("--mark", action="store_true", help="with next-question: record that it was asked")
    p.add_argument("--job"); p.add_argument("--kind", choices=["pick", "correction", "accepted", "declined"], default="pick")
    p.add_argument("--dimension"); p.add_argument("--choice"); p.add_argument("--offered"); p.add_argument("--words")
    p.set_defaults(fn=cmd_learn)
    p = sub.add_parser("selftest"); p.add_argument("--job")
    p.add_argument("--uhd", action="store_true", help="fake a 4K clip, to time this machine")
    p.add_argument("--seconds", type=int, default=12); p.set_defaults(fn=cmd_selftest)
    a = ap.parse_args()
    signal.signal(signal.SIGINT, lambda *_: die("interrupted; run the same command again to resume", 130))
    a.fn(a)


if __name__ == "__main__":
    main()
