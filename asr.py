#!/usr/bin/env python3
"""用 faster-whisper 识别英文语音，按词级时间戳切成完整句子，输出 SRT。

用法: asr.py <视频或音频> <输出.srt> [--model medium] [--cpu] [--hotwords "Claude, Anthropic"]
需要在 pyVideoTrans 的虚拟环境里运行（自带 faster-whisper 和 CUDA 库）。
"""

import argparse
import re

from faster_whisper import WhisperModel, decode_audio

MAX_LEN = 12.0   # 每条字幕最长秒数
MIN_LEN = 1.5    # 句末标点处断开时，至少要这么长
SOFT_LEN = 7.0   # 超过这个长度，遇到逗号也可以断
GAP = 0.8        # 词之间停顿超过这个秒数就断开
SAMPLE = 300     # 用热词前，先用开头这么多秒试识别
SR = 16000


def fmt(x):
    ms = int(round(x * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def sentences(words):
    out, cur = [], []

    def flush():
        if cur:
            text = re.sub(r"\s+", " ", "".join(w.word for w in cur)).strip()
            if text:
                out.append((cur[0].start, cur[-1].end, text))
            cur.clear()

    for w in words:
        if cur and (w.start - cur[-1].end > GAP or w.end - cur[0].start > MAX_LEN):
            flush()
        cur.append(w)
        dur = cur[-1].end - cur[0].start
        tail = w.word.strip()
        if (re.search(r"[.?!][\"')\]]*$", tail) and dur >= MIN_LEN) or (tail.endswith(",") and dur >= SOFT_LEN):
            flush()
    flush()
    return out


def suspicious(subs, words, duration, hotwords):
    """热词有时会让 Whisper 只输出热词本身、漏掉正文。出现下面任一情况就判定结果不可信：
    只由热词组成的字幕超过 2%，或语速低于每分钟 90 词（正常讲话约 120-180 词）。"""
    vocab = {w for h in hotwords.lower().split(",") for w in re.findall(r"[a-z0-9']+", h)}
    pure = sum(1 for _, _, t in subs
               if (ws := re.findall(r"[a-z0-9']+", t.lower())) and len(ws) <= 8 and all(w in vocab for w in ws))
    wpm = len(words) / max(duration / 60, 0.1)
    return pure > max(2, 0.02 * len(subs)) or wpm < 90, pure, wpm


def transcribe(model, media, hotwords):
    segs, info = model.transcribe(
        media, language="en", beam_size=5, vad_filter=True, word_timestamps=True,
        condition_on_previous_text=False, hotwords=hotwords or None)
    words = [w for s in segs for w in (s.words or [])]
    return words, sentences(words), info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("out")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--hotwords", default="")
    ap.add_argument("--models-dir", default="/vol_1t/pyvideotrans/models")
    a = ap.parse_args()

    model = WhisperModel(a.model, device="cpu" if a.cpu else "cuda", compute_type="int8", download_root=a.models_dir)
    audio = decode_audio(a.media, sampling_rate=SR)
    duration = len(audio) / SR
    note = ""
    if a.hotwords and duration > SAMPLE * 2:
        # 先试识别开头一段，热词出问题时不必等整段识别完再重来
        sample = audio[int(min(60, duration * 0.05) * SR):int((min(60, duration * 0.05) + SAMPLE) * SR)]
        w_hw, s_hw, _ = transcribe(model, sample, a.hotwords)
        bad, pure, wpm = suspicious(s_hw, w_hw, len(sample) / SR, a.hotwords)
        if bad:
            w_no, _, _ = transcribe(model, sample, "")
            # 只有不加热词时明显正常，才判定是热词的问题
            if len(w_no) > len(w_hw) * 1.3:
                print(f"试识别 {SAMPLE // 60} 分钟：加热词只识别出 {len(w_hw)} 个词，不加有 {len(w_no)} 个，"
                      "去掉热词", flush=True)
                a.hotwords, note = "", "，试识别发现热词异常，未使用热词"
    words, subs, info = transcribe(model, audio, a.hotwords)
    if a.hotwords:
        bad, pure, wpm = suspicious(subs, words, duration, a.hotwords)
        if bad:
            print(f"加热词的结果异常（纯热词字幕 {pure} 条，每分钟 {wpm:.0f} 词），去掉热词重新识别", flush=True)
            words, subs, info = transcribe(model, audio, "")
            note = "，已去掉热词重新识别"
    with open(a.out, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(subs, 1):
            f.write(f"{i}\n{fmt(s)} --> {fmt(e)}\n{t}\n\n")
    print(f"识别 {duration / 60:.1f} 分钟音频，{len(words)} 个词，{len(subs)} 条字幕{note}")


if __name__ == "__main__":
    main()
