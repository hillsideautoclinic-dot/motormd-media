#!/usr/bin/env python3
"""MotorMD builder v2: one matched shot per sentence, cut on sentence boundaries,
varied camera moves, optional motion hook clip for shot 0, progress bar.
Usage: python3 build2.py <week_dir> [ids...]
Shots: <week>/episodes.json entry 'shots' = list of image paths (relative to week dir), one per sentence.
Hook:  <week>/clips/<id>_hook.mp4 (optional) replaces shot 0.
Outputs out/<id>.mp4 and, for 'tod', out/<id>_quick.mp4.
"""
import json, os, sys, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import (W, H, FPS, ACCENT, SERIES, run, dur, sentence_times,
                   overlay_frame, caption_png, compose)

MOVES = ["zin", "pan_lr", "zout", "pan_tb", "pan_rl", "punch", "pan_bt"]
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def img_shot(p, T, move, out):
    frames = int(T * FPS) + 2
    if move in ("zin", "zout", "punch"):
        if move == "zin":   z = "min(1.0+0.10*on/%d,1.10)" % frames
        elif move == "zout": z = "max(1.14-0.12*on/%d,1.0)" % frames
        else:               z = "min(1.0+0.22*on/%d,1.22)" % frames
        vf = (f"scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}")
    else:
        SW, SH = int(W * 1.18) // 2 * 2, int(H * 1.18) // 2 * 2
        dx, dy = SW - W, SH - H
        x, y = f"{dx//2}", f"{dy//2}"
        if move == "pan_lr": x = f"{dx}*t/{T:.3f}"
        if move == "pan_rl": x = f"{dx}-{dx}*t/{T:.3f}"
        if move == "pan_tb": y = f"{dy}*t/{T:.3f}"
        if move == "pan_bt": y = f"{dy}-{dy}*t/{T:.3f}"
        vf = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},"
              f"crop={W}:{H}:x='{x}':y='{y}'")
    vf += ",eq=brightness=-0.03:saturation=1.08,format=yuv420p"
    run(["ffmpeg", "-y", "-loop", "1", "-i", p, "-vf", vf, "-frames:v", str(int(round(T * FPS))), *ENC, out])


def clip_shot(p, T, out, tmp):
    cd = dur(p)
    base = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=brightness=-0.03:saturation=1.08,format=yuv420p,fps={FPS}"
    n = int(round(T * FPS))
    if cd >= T:
        run(["ffmpeg", "-y", "-i", p, "-an", "-vf", base, "-frames:v", str(n), *ENC, out]); return
    # ping-pong, then loop to fill exactly n frames
    pp = f"{tmp}/pp.mp4"
    run(["ffmpeg", "-y", "-i", p, "-an", "-filter_complex",
         f"[0:v]{base},split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1[o]", "-map", "[o]", *ENC, pp])
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", pp, "-frames:v", str(n), *ENC, out])


def progress_bar(src, D, out):
    run(["ffmpeg", "-y", "-i", src, "-f", "lavfi", "-i", f"color=c=0x{ACCENT[0]:02x}{ACCENT[1]:02x}{ACCENT[2]:02x}:s={W}x12:r={FPS}",
         "-filter_complex", f"[0:v][1:v]overlay=x='-{W}+{W}*t/{D:.3f}':y=0:shortest=1,format=yuv420p",
         "-t", f"{D:.3f}", *ENC, out])


def build(week, e):
    tmp = f"{week}/tmp/v2_{e['id']}"; os.makedirs(tmp, exist_ok=True)
    audio = f"{week}/vo/{e['id']}.mp3"
    sents = e["sentences"]; shots = e["shots"]
    assert len(shots) == len(sents)
    times, AD, _ = sentence_times(sents, audio)
    D = min(times[-1][1] + 1.2, AD + 0.8)
    starts = [0.0] + [times[i][0] for i in range(1, len(sents))]
    ends = starts[1:] + [D]
    rnd = random.Random(e["id"]); off = rnd.randrange(len(MOVES))
    hook = f"{week}/clips/{e['id']}_hook.mp4"
    parts = []
    for k, (p, a, b) in enumerate(zip(shots, starts, ends)):
        T = (round(b * FPS) - round(a * FPS)) / FPS; o = f"{tmp}/shot{k}.mp4"
        if k == 0 and os.path.exists(hook): clip_shot(hook, T, o, tmp)
        else: img_shot(f"{week}/{p}", T, MOVES[(k + off) % len(MOVES)], o)
        parts.append(o)
    lst = f"{tmp}/list.txt"; open(lst, "w").write("".join(f"file '{os.path.abspath(x)}'\n" for x in parts))
    raw = f"{tmp}/bg_raw.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", raw])
    ov = f"{tmp}/ov.png"; overlay_frame(SERIES[e["series"]], e["title"], ov, e.get("ep"))
    caps = []
    for i, (s, (a, b)) in enumerate(zip(sents, times)):
        cp = f"{tmp}/cap{i}.png"; caption_png(s, cp); caps.append((cp, a, b if i < len(sents) - 1 else D))
    bg = f"{tmp}/bg.mp4"; progress_bar(raw, D, bg)
    out = f"{week}/out2/{e['id']}.mp4"
    compose(bg, audio, ov, caps, D, out, tmp)
    res = {"id": e["id"], "master": out, "dur": round(D, 2), "shots": len(parts)}
    if e["series"] == "tod":
        QMAX = float(os.environ.get("QMAX", "15.5"))
        cands = [(abs(b - 12), i + 1, b) for i, (a, b) in enumerate(times) if b <= QMAX]
        _, k, end = min(cands) if cands else (0, 1, times[0][1])
        QD = end + 0.6
        qraw = f"{tmp}/qraw.mp4"; run(["ffmpeg", "-y", "-i", raw, "-t", f"{QD:.3f}", *ENC, qraw])
        qbg = f"{tmp}/qbg.mp4"; progress_bar(qraw, QD, qbg)
        ovq = f"{tmp}/ovq.png"; overlay_frame(SERIES["quick"], e["title"], ovq)
        qcaps = [(caps[i][0], times[i][0], times[i][1] if i < k - 1 else QD) for i in range(k)]
        qout = f"{week}/out2/{e['id']}_quick.mp4"
        compose(qbg, audio, ovq, qcaps, QD, qout, tmp)
        res.update(quick=qout, quick_dur=round(QD, 2))
    return res


if __name__ == "__main__":
    week = sys.argv[1]; want = set(sys.argv[2:])
    for e in json.load(open(f"{week}/episodes.json")):
        if want and e["id"] not in want: continue
        if "shots" not in e: continue
        print(json.dumps(build(week, e)), flush=True)
