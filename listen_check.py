#!/usr/bin/env python3
"""回听检查：用 faster-whisper 识别中文配音，逐句和译文对比，找出读错、漏读、被截断的句子。

用法: listen_check.py <配音.wav> <zh.srt> <输出.json> [--model medium]
"""
import argparse
import json
import re

from faster_whisper import WhisperModel, decode_audio

SR = 16000


def ts(s):
    h, m, rest = s.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def norm(t):
    t = t.lower()
    try:
        import zhconv
        t = zhconv.convert(t, "zh-cn")
    except ImportError:
        pass
    return "".join(re.findall(r"[\u4e00-\u9fff]|[a-z0-9]", t))


def cer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("srt")
    ap.add_argument("out")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--models-dir", default="/vol_1t/pyvideotrans/models")
    a = ap.parse_args()

    entries = []
    for b in re.split(r"\n\s*\n", open(a.srt, encoding="utf-8-sig").read().strip()):
        l = b.strip().split("\n")
        if len(l) >= 3:
            s, e = (ts(x) for x in l[1].split("-->"))
            entries.append((s, e, " ".join(l[2:])))

    audio = decode_audio(a.wav, sampling_rate=SR)
    model = WhisperModel(a.model, device="cuda", compute_type="int8", download_root=a.models_dir)
    segs, _ = model.transcribe(audio, language="zh", beam_size=5, word_timestamps=True,
                               vad_filter=True, condition_on_previous_text=False,
                               initial_prompt="以下是普通话技术讲座的配音。")
    words = [w for s in segs for w in (s.words or [])]

    out = []
    for i, (s, e, text) in enumerate(entries):
        nxt = entries[i + 1][0] if i + 1 < len(entries) else e + 2
        heard = "".join(w.word for w in words if s - 0.1 <= (w.start + w.end) / 2 < nxt)
        out.append({"n": i + 1, "text": text, "heard": heard, "cer": round(cer(text, heard), 3)})
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    c = sorted(x["cer"] for x in out)
    print(f"{len(out)} 句，字错误率中位 {c[len(c) // 2]:.1%}，超过 30% 的 {sum(x > 0.3 for x in c)} 句")


if __name__ == "__main__":
    main()
