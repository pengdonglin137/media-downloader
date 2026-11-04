#!/usr/bin/env python3
"""按字幕时间轴生成中文配音。

每句先用正常语速合成；放不进时间段时，用 Edge-TTS 自带的语速参数重新合成（比事后变速自然），
仍超出时再用 ffmpeg atempo 做少量压缩。句子可以占用到下一句开始前的空隙。

用法: dub.py <zh.srt> <out.wav> <缓存目录> --voices voices.json
voices.json 是与字幕等长的音色列表。需要在 pyVideoTrans 的虚拟环境里运行（自带 edge-tts、numpy）。
"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path

import edge_tts
import numpy as np

SR = 24000
MAX_RATE = 40      # Edge-TTS 语速最多提高 40%
MAX_TEMPO = 1.15   # 之后再允许 atempo 压缩 15%
LEAD = 0.05        # 和下一句之间至少留 50ms
TARGET_DB = -20.0  # 每句统一到这个响度（有声部分的 RMS），避免换音色时音量跳变


def ts(s):
    h, m, rest = s.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def parse_srt(path):
    out = []
    for b in re.split(r"\n\s*\n", Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()):
        lines = b.strip().split("\n")
        if len(lines) >= 2 and "-->" in lines[1]:
            a, c = (ts(x) for x in lines[1].split("-->"))
            out.append((a, c, " ".join(l.strip() for l in lines[2:]).strip()))
    return out


def level(y):
    """把一句话的有声部分 RMS 调到 TARGET_DB，峰值不超过 -1 dBFS。"""
    voiced = y[np.abs(y) > 0.01]
    if len(voiced) < 240:
        return y
    gain = 10 ** ((TARGET_DB - 20 * np.log10(np.sqrt(np.mean(voiced ** 2)))) / 20)
    peak = np.max(np.abs(y)) * gain
    if peak > 0.89:
        gain *= 0.89 / peak
    return (y * gain).astype(np.float32)


def decode(path, tempo=1.0):
    if path is None:
        return np.zeros(0, np.float32)
    af = ["-af", f"atempo={tempo:.4f}"] if tempo > 1.001 else []
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), *af, "-f", "f32le", "-ac", "1",
                        "-ar", str(SR), "-"], capture_output=True, check=True)
    y = np.frombuffer(r.stdout, dtype=np.float32)
    # 去掉首尾静音，让时长更准确
    nz = np.nonzero(np.abs(y) > 0.01)[0]
    return y[max(0, nz[0] - 240):nz[-1] + 480] if len(nz) else y[:0]


async def synth(text, voice, rate, cache, sem):
    key = hashlib.md5(f"{voice}|{rate}|{text}".encode()).hexdigest()
    path = cache / f"{key}.mp3"
    if path.exists() and path.stat().st_size > 0:
        return path
    async with sem:
        for attempt in range(5):
            try:
                tmp = path.with_suffix(".tmp")
                await edge_tts.Communicate(text, voice, rate=f"{rate:+d}%").save(str(tmp))
                if tmp.stat().st_size < 200:
                    raise RuntimeError("没有返回音频")
                tmp.replace(path)
                return path
            except Exception as e:  # 网络抖动、限流或没有返回音频，退避重试
                if attempt == 4:
                    print(f"  警告：Edge-TTS 合成失败，这句留空: {text[:30]} ({e})", flush=True)
                    return None
                await asyncio.sleep(2 * (attempt + 1))


async def dub_one(i, entries, voice, cache, sem):
    a, c, text = entries[i]
    nxt = entries[i + 1][0] if i + 1 < len(entries) else c + 2.0
    slot = max(c - a, nxt - a - LEAD)
    if not re.search(r"\w", text):
        return i, np.zeros(0, np.float32), 0, 1.0, 0.0, slot
    y = decode(await synth(text, voice, 0, cache, sem))
    rate, tempo = 0, 1.0
    need = need0 = len(y) / SR / slot
    if len(y) == 0:
        return i, y, 0, 1.0, 0.0, slot
    if need > 1.0:
        rate = min(MAX_RATE, int(np.ceil((need - 1) * 100 / 5)) * 5)
        y = decode(await synth(text, voice, rate, cache, sem))
        need = len(y) / SR / slot
        if need > 1.0:
            tempo = min(MAX_TEMPO, need)
            y = decode(await synth(text, voice, rate, cache, sem), tempo)
    return i, level(y), rate, tempo, need0, slot


async def run(entries, voices, cache, workers):
    sem = asyncio.Semaphore(workers)
    tasks = [dub_one(i, entries, voices[i], cache, sem) for i in range(len(entries))]
    results, done = [None] * len(entries), 0
    for coro in asyncio.as_completed(tasks):
        i, y, rate, tempo, need0, slot = await coro
        results[i] = (y, rate, tempo, need0, slot)
        done += 1
        if done % 100 == 0:
            print(f"  配音 {done}/{len(entries)}", flush=True)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srt")
    ap.add_argument("out")
    ap.add_argument("cache")
    ap.add_argument("--voices", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--total", type=float, default=0, help="输出音频总时长（秒）")
    a = ap.parse_args()

    entries = parse_srt(a.srt)
    voices = json.loads(Path(a.voices).read_text(encoding="utf-8"))
    assert len(voices) == len(entries), "音色列表和字幕条数不一致"
    cache = Path(a.cache)
    cache.mkdir(parents=True, exist_ok=True)

    results = asyncio.run(run(entries, voices, cache, a.workers))

    total = max(a.total, entries[-1][1] + 2)
    out = np.zeros(int(total * SR) + SR, np.float32)
    overflow = 0
    for (start, end, _), (y, *_) in zip(entries, results):
        s = int(start * SR)
        e = min(s + len(y), len(out))
        out[s:e] += y[:e - s]
    # 检查句子之间是否重叠
    for i in range(len(entries) - 1):
        if entries[i][0] + len(results[i][0]) / SR > entries[i + 1][0] + 0.01:
            overflow += 1
    out = np.clip(out, -1, 1)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-c:a", "pcm_s16le", a.out], input=out[:int(total * SR)].tobytes(), check=True)

    rates = [r[1] for r in results]
    tempos = [r[2] for r in results]
    stats = {
        "entries": len(entries),
        "normal": sum(r == 0 for r in rates),
        "rate_up": sum(r > 0 for r in rates),
        "rate_ge_25": sum(r >= 25 for r in rates),
        "atempo": sum(t > 1.001 for t in tempos),
        "silent": sum(len(r[0]) == 0 and re.search(r"\w", e[2]) is not None for r, e in zip(results, entries)),
        "overlap": overflow,
        # 每句：Edge-TTS 提速百分比、额外压缩倍数、正常语速时的 时长/可用时长、可用时长（秒）
        "per": [[r[1], round(r[2], 3), round(r[3], 3), round(r[4], 2)] for r in results],
    }
    Path(a.out).with_suffix(".stats.json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    print(f"共 {stats['entries']} 句：正常语速 {stats['normal']}，提速 {stats['rate_up']}"
          f"（其中 ≥25% 的 {stats['rate_ge_25']}），额外压缩 {stats['atempo']}，与下一句重叠 {stats['overlap']}"
          + (f"，合成失败留空 {stats['silent']}" if stats["silent"] else ""))


if __name__ == "__main__":
    main()
