#!/usr/bin/env python3
"""按基频估计每条字幕说话人的性别，输出 JSON 列表（"M"/"F"）。

用法: voice_gender.py <视频或音频> <字幕.srt> <输出.json> [阈值Hz]
需要 librosa（pyVideoTrans 的虚拟环境里已有）。
"""

import json
import re
import subprocess
import sys
import tempfile

import librosa
import numpy as np

SR = 16000
MAX_SEG = 4.0
RATIO = 1.35  # 女声片段的基频至少是男声基准的这么多倍  # 每条字幕最多分析 4 秒，长句取中间部分


def ts(s):
    h, m, rest = s.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def runs_of(labels):
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            yield start, i
            start = i


def filter_runs(labels, f0):
    """男声激动时音调会升高，常被误判成短暂的女声片段。
    女声片段要么足够长（≥5 条且中位基频 ≥165Hz），要么音调明显偏高（≥3 条且 ≥180Hz），否则改回男声。
    夹在女声中间、不超过 2 条的男声片段改回女声。"""
    out = list(labels)
    for a, b in list(runs_of(out)):
        if out[a] != "F":
            continue
        n = b - a
        med = np.nanmedian(f0[a:b]) if np.any(~np.isnan(f0[a:b])) else 0
        if not ((n >= 5 and med >= 165) or (n >= 3 and med >= 180)):
            out[a:b] = ["M"] * n
    # 女声通常比同场男声高 40% 以上；只比男声基准高一点的片段多半是男声情绪激动，改回男声
    male = f0[[i for i, x in enumerate(out) if x == "M"]]
    base = np.nanmedian(male) if np.any(~np.isnan(male)) else np.nanmedian(f0)
    for a, b in list(runs_of(out)):
        if out[a] == "F" and np.nanmedian(f0[a:b]) < base * RATIO:
            out[a:b] = ["M"] * (b - a)
    for a, b in list(runs_of(out)):
        if out[a] == "M" and b - a <= 2 and a > 0 and b < len(out) and out[a - 1] == out[b] == "F":
            out[a:b] = ["F"] * (b - a)
    return out


def main():
    media, srt, out = sys.argv[1:4]
    threshold = float(sys.argv[4]) if len(sys.argv) > 4 else 160.0

    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", media, "-ac", "1", "-ar", str(SR), tmp.name],
                       check=True)
        y, _ = librosa.load(tmp.name, sr=SR)

    blocks = re.split(r"\n\s*\n", open(srt, encoding="utf-8-sig").read().replace("\r\n", "\n").strip())
    f0 = []
    for b in blocks:
        lines = b.strip().split("\n")
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, c = (ts(x) for x in lines[1].split("-->"))
        if c - a > MAX_SEG:
            mid = (a + c) / 2
            a, c = mid - MAX_SEG / 2, mid + MAX_SEG / 2
        seg = y[int(a * SR):int(c * SR)]
        if len(seg) < SR * 0.3:
            f0.append(np.nan)
            continue
        f, _, _ = librosa.pyin(seg, fmin=60, fmax=400, sr=SR, frame_length=1024, hop_length=320)
        f0.append(float(np.nanmedian(f)) if np.any(~np.isnan(f)) else np.nan)

    f0 = np.array(f0, dtype=float)
    raw = [-1 if np.isnan(x) else int(x > threshold) for x in f0]
    # 前后各 2 条做多数表决，去掉零星误判
    smooth = []
    for i in range(len(raw)):
        w = [x for x in raw[max(0, i - 2):i + 3] if x >= 0]
        smooth.append("F" if w and sum(w) * 2 > len(w) else "M")
    smooth = filter_runs(smooth, f0)
    json.dump({"gender": smooth, "f0": [None if np.isnan(x) else round(x, 1) for x in f0]},
              open(out, "w", encoding="utf-8"))
    print(f"男声 {smooth.count('M')} 条，女声 {smooth.count('F')} 条")


if __name__ == "__main__":
    main()
