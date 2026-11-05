#!/usr/bin/env python3
"""把英文视频转成中文配音视频。

流程：下载（可选）→ 网站英文字幕或 faster-whisper 识别（带专有名词热词，按句切分）
→ 生成术语表 → codex/claude 分段翻译 → 按音高区分男女声 → Edge-TTS 逐句配音 → ffmpeg 合成。
每一步的产物都保存在工作目录里，中途失败后重新运行会跳过已完成的步骤。
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PVT_DIR = Path(os.environ.get("PVT_DIR", "/vol_1t/pyvideotrans"))
DEFAULT_OUT = Path(os.environ.get("ZH_VIDEO_DIR", "/vol_1t/Videos"))
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "opus")
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv", ".avi"}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, logfile=None, cwd=None, timeout=None):
    """运行命令；输出写入日志文件，失败时打印日志末尾。"""
    if logfile:
        with open(logfile, "w", encoding="utf-8") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=cwd, timeout=timeout)
        if r.returncode != 0:
            tail = Path(logfile).read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
            die(f"命令失败（退出码 {r.returncode}）: {cmd[0]}\n日志: {logfile}\n" + "\n".join(tail))
    else:
        r = subprocess.run(cmd, cwd=cwd, timeout=timeout)
        if r.returncode != 0:
            die(f"命令失败（退出码 {r.returncode}）: {' '.join(map(str, cmd[:3]))} ...")


# ---------------------------------------------------------------- SRT
def parse_srt(path):
    entries = []
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.strip().split("\n")]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        entries.append({"time": lines[1].strip(), "text": " ".join(l.strip() for l in lines[2:]).strip()})
    return entries


def srt_sec(t):
    h, m, rest = t.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def sec_srt(x):
    ms = int(round(x * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def merge_sentences(entries, max_len=12.0, max_gap=0.8):
    """把识别出的碎片字幕合并成完整句子，使每条的时长和内容更匹配，配音语速更均匀。"""
    out, cur = [], None
    for e in entries:
        a, b = (srt_sec(x) for x in e["time"].split("-->"))
        if cur and (a - cur["b"] > max_gap or b - cur["a"] > max_len):
            out.append(cur)
            cur = None
        if cur is None:
            cur = {"a": a, "b": b, "text": e["text"]}
        else:
            cur["b"], cur["text"] = b, f"{cur['text']} {e['text']}"
        if re.search(r"[.?!][\"')\]]*$", cur["text"].strip()) and cur["b"] - cur["a"] >= 1.5:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return [{"time": f"{sec_srt(c['a'])} --> {sec_srt(c['b'])}", "text": re.sub(r"\s+", " ", c["text"]).strip()}
            for c in out]


def write_srt(path, entries, key="text"):
    blocks = [f"{i}\n{e['time']}\n{e[key]}" for i, e in enumerate(entries, 1)]
    Path(path).write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 步骤
def step_download(url, work):
    dl = work / "download"
    found = [p for p in dl.glob("*") if p.suffix.lower() in VIDEO_EXTS] if dl.exists() else []
    if not found:
        log("下载视频 ...")
        dl.mkdir(parents=True, exist_ok=True)
        run([str(SCRIPT_DIR / "download_media.sh"), url, str(dl)])
        found = [p for p in dl.glob("*") if p.suffix.lower() in VIDEO_EXTS]
    if not found:
        die(f"下载目录里没有找到视频文件: {dl}")
    return max(found, key=lambda p: p.stat().st_size)


def ytdlp_bin():
    for c in (SCRIPT_DIR / "bin/yt-dlp", SCRIPT_DIR / ".venv/bin/yt-dlp"):
        if c.exists():
            return str(c)
    return shutil.which("yt-dlp")


def is_x_url(url):
    return re.match(r"^https?://(www\.|mobile\.)?(x|twitter)\.com/", url) is not None


def step_meta(url, work):
    """读取视频标题、简介和是否有人工英文字幕，结果缓存在 meta.json。"""
    cache = work / "meta.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    meta = {"title": "", "description": "", "uploader": "", "en_subs": False}
    try:
        if is_x_url(url):
            page = subprocess.run(
                ["curl", "-sL", "--compressed", "--max-time", "60", "-A", "Mozilla/5.0",
                 "-H", "Accept-Language: en-US,en;q=0.8", "--referer", "https://x.com/", url.split("/video/")[0]],
                capture_output=True, text=True, timeout=90).stdout

            def og(name):
                m = re.search(rf'<meta (?:property|name)="{name}" content="([^"]*)"', page)
                return html.unescape(m.group(1)).strip() if m else ""
            meta["description"] = og("og:description")
            meta["uploader"] = re.sub(r"\s*\(@.*$", "", og("og:title"))
            first = re.split(r"[\n:：]", meta["description"])[0].strip()
            meta["title"] = first[:60]
        elif ytdlp_bin():
            r = subprocess.run([ytdlp_bin(), "-j", "--no-playlist", "--no-warnings", url],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                d = json.loads(r.stdout)
                meta.update(title=d.get("title") or "", description=(d.get("description") or "")[:1500],
                            uploader=d.get("uploader") or d.get("channel") or "",
                            en_subs=any(k == "en" or k.startswith("en-") for k in (d.get("subtitles") or {})))
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    cache.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def clean_source_subs(path):
    """整理网站提供的字幕：去掉样式标签和 [Music] 之类的音效标注，合并为单行。"""
    entries = []
    for e in parse_srt(path):
        t = re.sub(r"<[^>]+>", "", html.unescape(e["text"]))
        t = re.sub(r"\[[^\]]*\]|\([^)]*(music|applause|laughter)[^)]*\)", "", t, flags=re.I)
        t = re.sub(r"\s+", " ", t).strip(" -")
        if t:
            entries.append({"time": e["time"], "text": t})
    return entries


def step_source_subs(url, work):
    """下载网站上人工上传的英文字幕，成功则写入 en.srt 并返回 True。"""
    sub_dir = work / "source_subs"
    shutil.rmtree(sub_dir, ignore_errors=True)
    sub_dir.mkdir()
    log("下载网站提供的英文字幕 ...")
    r = subprocess.run([ytdlp_bin(), "--skip-download", "--no-playlist", "--write-subs", "--sub-langs", "en,en-.*",
                        "--convert-subs", "srt", "-o", str(sub_dir / "sub.%(ext)s"), url],
                       capture_output=True, text=True, timeout=300)
    srts = sorted(sub_dir.glob("*.srt"), key=lambda p: (".en." not in p.name, p.name))
    if r.returncode != 0 or not srts:
        log("  没拿到英文字幕，改用语音识别")
        return False
    entries = merge_sentences(clean_source_subs(srts[0]))
    if len(entries) < 5:
        log("  英文字幕内容太少，改用语音识别")
        return False
    write_srt(work / "en.srt", entries)
    (work / "en_from_site").touch()
    log(f"  使用网站字幕 {srts[0].name}，共 {len(entries)} 条")
    return True


def step_stt(video, work, model, use_cuda, hotwords):
    out = work / "en.srt"
    if out.exists():
        log("英文字幕已存在，跳过识别")
        return out
    log(f"语音识别（faster-whisper {model}）...")
    if hotwords:
        log(f"  热词: {hotwords}")
    tmp = work / "en_asr.srt"
    cmd = [str(PVT_DIR / ".venv/bin/python"), str(SCRIPT_DIR / "asr.py"), str(video), str(tmp),
           "--model", model, "--hotwords", hotwords, "--models-dir", str(PVT_DIR / "models")]
    if not use_cuda:
        cmd.append("--cpu")
    run(cmd, logfile=work / "stt.log")
    log("  " + (work / "stt.log").read_text(encoding="utf-8").strip().splitlines()[-1])
    tmp.replace(out)
    return out


def llm_call(engine, prompt, effort, workdir, attempt_log):
    """调用 codex 或 claude 命令行，返回模型输出文本，失败返回 None。"""
    with open(attempt_log, "a", encoding="utf-8") as f:
        f.write(f"\n===== {engine} {time.strftime('%H:%M:%S')} =====\n")
        f.flush()
        try:
            if engine == "codex":
                out_file = Path(workdir) / "last.txt"
                out_file.unlink(missing_ok=True)
                r = subprocess.run(
                    ["codex", "exec", "--skip-git-repo-check", "-s", "read-only",
                     "-c", f"model_reasoning_effort={effort}", "--output-last-message", str(out_file), prompt],
                    stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=workdir, timeout=900)
                if r.returncode != 0 or not out_file.exists():
                    return None
                return out_file.read_text(encoding="utf-8")
            # claude：提示词走 stdin，禁用所有工具，不保存会话
            r = subprocess.run(
                ["claude", "-p", "--model", CLAUDE_MODEL, "--no-session-persistence", "--tools="],
                input=prompt, capture_output=True, text=True, cwd=workdir, timeout=900)
            f.write(r.stderr)
            f.write(r.stdout)
            if r.returncode != 0:
                return None
            return r.stdout
        except subprocess.TimeoutExpired:
            f.write("超时\n")
            return None


def step_hotwords(work, meta, title, engine, effort):
    """从标题和简介里提取专有名词，作为语音识别的热词。"""
    out = work / "hotwords.txt"
    if out.exists():
        return out.read_text(encoding="utf-8").strip()
    words = ""
    text = f"{title}\n{meta.get('uploader', '')}\n{meta.get('description', '')}".strip()
    if len(text) > 20:
        log("从标题和简介提取专有名词 ...")
        prompt = ("下面是一个英文视频的标题、发布者和简介。列出其中出现的、语音识别容易听错的专有名词"
                  "（产品名、公司名、人名、技术名词、缩写），最多 20 个，用英文逗号分隔，保持原文拼写。"
                  "只输出这一行，不要解释。\n\n" + text[:2000])
        with tempfile.TemporaryDirectory(prefix="llm_hw_") as tmp:
            r = llm_call(engine, prompt, effort, tmp, work / f"{engine}.log") or ""
        line = next((l for l in r.strip().splitlines() if "," in l or len(r.strip().splitlines()) == 1), "")
        words = ", ".join(w.strip(" .\"'`") for w in line.split(",") if 1 < len(w.strip()) < 40)[:400]
    out.write_text(words, encoding="utf-8")
    return words


def step_glossary(en_srt, work, info, engine, effort):
    """通读全文，生成统一的术语译法表，并列出明显的识别错误，翻译每一段时都带上。"""
    out = work / "glossary.txt"
    if out.exists():
        return out.read_text(encoding="utf-8").strip()
    log("通读全文生成术语表 ...")
    full = " ".join(e["text"] for e in parse_srt(en_srt))
    if len(full) > 120000:  # 太长时取开头、中间、结尾三段
        k = 40000
        full = full[:k] + " ... " + full[len(full) // 2 - k // 2:len(full) // 2 + k // 2] + " ... " + full[-k:]
    prompt = (
        f"{info['background']}\n\n下面是这个视频的完整英文字幕{'（来自语音识别，可能有听错的词）' if info['asr_note'] else ''}。"
        "请为中文翻译整理一份术语表，供后续分段翻译时统一用词：\n"
        "1. 列出反复出现的专业术语、产品名、概念，最多 40 条，每行一条，格式「英文 => 中文译法」；"
        "产品名、API 名、缩写这类应保持英文的，写成「英文 => 英文」；\n"
        "2. 如果发现语音识别把专有名词听错了（结合标题、简介和上下文判断），每行一条，"
        "格式「识别错误 => 正确写法」，并在行尾加「（识别错误）」；\n"
        "只输出这些行，不要标题和解释。\n\n" + full)
    with tempfile.TemporaryDirectory(prefix="llm_gl_") as tmp:
        r = llm_call(engine, prompt, effort, tmp, work / f"{engine}.log") or ""
    lines = [l.strip(" -*") for l in r.splitlines() if "=>" in l][:60]
    text = "\n".join(lines)
    out.write_text(text, encoding="utf-8")
    log(f"  术语表 {len(lines)} 条，保存在 {out.name}")
    return text


def translate_chunk(engine, items, context, info, effort, workdir, attempt_log, budget=None):
    budget = budget or {}
    """items: [(编号, 英文)]；返回 {编号: 中文}，校验失败返回 None。"""
    numbered = "\n".join(f"{n} [≤{budget.get(n, 99)}字]: {t}" for n, t in items)
    ctx = ""
    if context:
        ctx = "前文（仅供理解上下文，不要输出）：\n" + "\n".join(f"{en} => {zh}" for en, zh in context) + "\n\n"
    prompt = (
        f"你是专业的技术视频字幕译者。\n{info['background']}\n"
        "把下面每行英文字幕翻译成简体中文。要求：\n"
        "1. 每行格式为「编号 [≤N字]: 英文」，输出格式为「编号: 中文」，编号不变，行数不变，一行对一行；\n"
        "   [≤N字] 是按这句话的时长算出的配音字数上限（一个英文单词按 2 字计），超出会让配音被迫加速。"
        "请意译、省略口头禅和重复的话，让译文不超过上限，但不要丢掉关键信息；\n"
        "2. 一句话被拆到相邻几行时，结合上下文翻译，让每行读起来自然，但不要把内容挪到别的行；"
        "每一行都必须有非空译文，即使只是半句话的片段，也要单独译出这个片段；\n"
        "3. 术语按中文技术圈习惯翻译，产品名、函数名、命令、缩写保持英文；\n"
        "4. 语言简洁口语化，适合配音朗读；\n"
        f"{info['asr_note']}"
        + (f"术语表（必须统一按此翻译；标了「识别错误」的，原文里出现左边的写法时按右边理解）：\n{info['glossary']}\n\n"
           if info.get("glossary") else "") +
        "只输出译文行，不要任何解释、标题或代码块标记。\n\n"
        f"{ctx}待翻译：\n{numbered}\n"
    )
    text = llm_call(engine, prompt, effort, workdir, attempt_log)
    if text is None:
        return {}
    result = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\s*[:：]\s*(.*)$", line)
        if m:
            result[int(m.group(1))] = m.group(2).strip()
    want = {n for n, _ in items}
    # 只保留本批编号且非空的译文，缺失的由调用方单独重试
    return {n: t for n, t in result.items() if n in want and t}


ZH_RATE = 4.5  # 按原句时长每秒 4.5 字给译文限长。实测放宽到占用句间空隙后，译文质量没有提高，提速的句子反而翻倍


def char_budget(entries):
    """每句可用的配音字数。"""
    out = {}
    for i, e in enumerate(entries):
        a, b = (srt_sec(x) for x in e["time"].split("-->"))
        out[i + 1] = max(4, int((b - a) * ZH_RATE))
    return out


def step_translate(en_srt, work, info, chunk, effort, engine):
    out = work / "zh.srt"
    if out.exists():
        log("中文字幕已存在，跳过翻译")
        return out
    entries = parse_srt(en_srt)
    if not entries:
        die(f"英文字幕为空: {en_srt}")
    cache = work / "translate_parts"
    cache.mkdir(exist_ok=True)
    attempt_log = work / f"{engine}.log"
    budget = char_budget(entries)
    zh = {}
    with tempfile.TemporaryDirectory(prefix="llm_tr_") as tmp:
        for start in range(0, len(entries), chunk):
            items = [(i + 1, entries[i]["text"]) for i in range(start, min(start + chunk, len(entries)))]
            part = cache / f"{items[0][0]:05d}.tsv"
            if part.exists():
                for n, t in ((int(a), b) for a, b in (l.split("\t", 1) for l in part.read_text(encoding="utf-8").splitlines() if "\t" in l)):
                    zh[n] = t
                if all(n in zh for n, _ in items):
                    continue
            context = [(entries[i]["text"], zh[i + 1]) for i in range(max(0, start - 3), start) if (i + 1) in zh]
            log(f"{engine} 翻译第 {items[0][0]}-{items[-1][0]} 条（共 {len(entries)} 条）...")
            res = {}
            todo = items
            for attempt in range(4):
                # 第 3 次起把剩余条目拆成每批 10 条，降低模型错位的概率
                size = len(todo) if attempt < 2 else 10
                for k in range(0, len(todo), size):
                    batch = todo[k:k + size]
                    first = batch[0][0] - 1
                    ctx = [(entries[i]["text"], res.get(i + 1) or zh.get(i + 1))
                           for i in range(max(0, first - 3), first) if res.get(i + 1) or zh.get(i + 1)]
                    res.update(translate_chunk(engine, batch, ctx, info, effort, tmp, attempt_log, budget))
                todo = [(n, t) for n, t in items if n not in res]
                if not todo:
                    break
                log(f"  还有 {len(todo)} 条没有译文，重试（{attempt + 1}/4）")
            if todo:
                die(f"{engine} 翻译失败，第 {', '.join(str(n) for n, _ in todo[:10])} 条没有译文，日志: {attempt_log}")
            part.write_text("\n".join(f"{n}\t{res[n]}" for n, _ in items) + "\n", encoding="utf-8")
            zh.update(res)
    for i, e in enumerate(entries, 1):
        e["zh"] = zh[i]
        e["bi"] = f"{zh[i]}\n{e['text']}"
    write_srt(out, entries, "zh")
    write_srt(work / "bilingual.srt", entries, "bi")
    return out


def step_review(en_srt, zh_srt, work, info, engine, effort, chunk=80):
    """对照英文逐句校对译文，只改有实质问题的句子：误译、漏掉关键信息、术语不统一、明显不通顺、超出字数。"""
    done = work / "reviewed.json"
    if done.exists():
        return json.loads(done.read_text(encoding="utf-8"))
    en, zh = parse_srt(en_srt), parse_srt(zh_srt)
    budget = char_budget(en)
    parts = work / "review_parts"
    parts.mkdir(exist_ok=True)
    fixed = {}
    with tempfile.TemporaryDirectory(prefix="llm_rv_") as tmp:
        for start in range(0, len(en), chunk):
            idx = range(start + 1, min(start + chunk, len(en)) + 1)
            part = parts / f"{start + 1:05d}.json"
            if part.exists():
                fixed.update({int(k): v for k, v in json.loads(part.read_text(encoding="utf-8")).items()})
                continue
            log(f"{engine} 校对第 {idx[0]}-{idx[-1]} 条 ...")
            lines = "\n".join(f"{n} [≤{budget[n]}字]\n  EN: {en[n - 1]['text']}\n  ZH: {zh[n - 1]['text']}" for n in idx)
            prompt = (
                f"你是资深技术翻译审校。\n{info['background']}\n"
                + (f"术语表：\n{info['glossary']}\n" if info.get("glossary") else "") +
                "下面是视频字幕的英文原文和中文配音译文，请逐句对照检查，只找出有实质问题的句子：\n"
                "- 误译：意思和原文不同、理解错了技术概念或说话人意图；\n"
                "- 漏译：丢掉了影响理解的关键信息（数字、条件、否定、因果、对比）；口头禅、重复可以省略；\n"
                "- 术语和术语表不一致，或同一术语前后译法不同；\n"
                "- 读起来明显不通顺、不像中文口语；\n"
                "- 超出 [≤N字] 的字数上限（一个英文单词按 2 字计）。\n"
                + (f"{info['asr_note']}" if info.get("asr_note") else "") +
                "风格差异、可改可不改的不要改。对有问题的句子给出修改后的译文，仍须遵守字数上限，"
                "一句话被拆到相邻几行时不要把内容挪到别的行。\n"
                "输出格式为每行「编号: 新译文」，只输出需要修改的行；全部没问题就输出「无」。\n\n" + lines)
            r = llm_call(engine, prompt, effort, tmp, work / f"{engine}.log") or ""
            got = {}
            for line in r.splitlines():
                m = re.match(r"^\s*(\d+)\s*[:：]\s*(.+)$", line)
                if m and int(m.group(1)) in idx:
                    n, t = int(m.group(1)), m.group(2).strip()
                    if t and t != zh[n - 1]["text"]:
                        got[n] = t
            part.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
            fixed.update(got)
    for n, t in fixed.items():
        zh[n - 1]["text"] = t
    write_srt(zh_srt, zh)
    write_srt(work / "bilingual.srt", [{"time": z["time"], "text": f"{z['text']}\n{e['text']}"} for z, e in zip(zh, en)])
    done.write_text(json.dumps(sorted(fixed)), encoding="utf-8")
    log(f"  校对修改了 {len(fixed)} 句（共 {len(zh)} 句）")
    return sorted(fixed)


def step_gender(video, work):
    out = work / "gender.json"
    if not out.exists():
        log("按音高判断每句话的说话人性别 ...")
        run([str(PVT_DIR / ".venv/bin/python"), str(SCRIPT_DIR / "voice_gender.py"), str(video),
             str(work / "en.srt"), str(out)], logfile=work / "gender.log")
        log("  " + (work / "gender.log").read_text(encoding="utf-8").strip().splitlines()[-1])
    return json.loads(out.read_text(encoding="utf-8"))["gender"]


def run_dub(zh_srt, work, video, vfile, out):
    tmp = out.with_suffix(".part.wav")
    run([str(PVT_DIR / ".venv/bin/python"), str(SCRIPT_DIR / "dub.py"), str(zh_srt), str(tmp),
         str(work / "tts_cache"), "--voices", str(vfile), "--total", f"{probe_duration(video):.3f}"],
        logfile=work / "tts.log")
    log("  " + (work / "tts.log").read_text(encoding="utf-8").strip().splitlines()[-1])
    tmp.with_suffix(".stats.json").replace(work / "tts_stats.json")
    tmp.replace(out)
    return json.loads((work / "tts_stats.json").read_text(encoding="utf-8"))


def step_shorten(zh_srt, work, stats, info, engine, effort, limit):
    """正常语速下读不完、需要提速超过 limit 的句子，让模型按可用时长重写得更短。返回改写的条数。"""
    entries = parse_srt(zh_srt)
    en = parse_srt(work / "en.srt")
    tried_file = work / "shorten_tried.json"
    tried = set(json.loads(tried_file.read_text(encoding="utf-8"))) if tried_file.exists() else set()
    todo = [(i + 1, p[2]) for i, p in enumerate(stats["per"])
            if p[2] > 1 + limit and entries[i]["text"] not in tried]
    if not todo:
        return 0
    tried_file.write_text(json.dumps(sorted(tried | {entries[n - 1]["text"] for n, _ in todo}), ensure_ascii=False),
                          encoding="utf-8")
    log(f"有 {len(todo)} 句需要提速 {int(limit * 100)}% 以上才读得完，请 {engine} 缩短 ...")
    lines = []
    for n, need in todo:
        zh = entries[n - 1]["text"]
        want = max(4, int(len(zh) / need * 0.9))
        lines.append(f"{n} [≤{want}字]: {en[n - 1]['text']}\n   当前译文（{len(zh)}字）: {zh}")
    prompt = (f"你是专业的技术视频配音译者。\n{info['background']}\n"
              + (f"术语表：\n{info['glossary']}\n" if info.get("glossary") else "") +
              "下面这些句子的中文译文太长，配音放不进原视频的时间段。请在 [≤N字] 的字数内重写译文"
              "（一个英文单词按 2 字计），保留关键信息，删掉口头禅、重复和可以省略的修饰。\n"
              "输出格式为每行「编号: 新译文」，只输出这些行。\n\n" + "\n".join(lines))
    with tempfile.TemporaryDirectory(prefix="llm_sh_") as tmp:
        r = llm_call(engine, prompt, effort, tmp, work / f"{engine}.log") or ""
    new = {}
    for line in r.splitlines():
        m = re.match(r"^\s*(\d+)\s*[:：]\s*(.+)$", line)
        if m and int(m.group(1)) in dict(todo):
            n, t = int(m.group(1)), m.group(2).strip()
            if 0 < len(t) < len(entries[n - 1]["text"]):
                new[n] = t
    if not new:
        return 0
    for n, t in new.items():
        entries[n - 1]["text"] = t
    write_srt(zh_srt, entries)
    write_srt(work / "bilingual.srt", [{"time": z["time"], "text": f"{z['text']}\n{e['text']}"}
                                       for z, e in zip(entries, en)])
    return len(new)


def step_tts(zh_srt, work, video, voice, male, female, info, engine, effort, shorten):
    entries = parse_srt(zh_srt)
    tag = voice if voice != "auto" else male + "+" + female
    key = hashlib.md5((Path(zh_srt).read_text(encoding="utf-8") + tag).encode()).hexdigest()[:10]
    final = work / f"dub_{key}.wav"
    if final.exists():
        log("配音已存在，跳过")
        return final
    for old in work.glob("dub_*.wav"):
        old.unlink()
    voices = [voice] * len(entries)
    if voice == "auto":
        gender = step_gender(video, work)
        if len(gender) != len(entries):
            die("性别判断结果和字幕条数不一致，删除工作目录里的 gender.json 后重试")
        voices = [female if g == "F" else male for g in gender]
    vfile = work / "voices.json"
    vfile.write_text(json.dumps(voices), encoding="utf-8")
    log(f"Edge-TTS 逐句配音（男声 {sum(v == male for v in voices)} 句，女声 {sum(v == female for v in voices)} 句）...")
    stats = run_dub(zh_srt, work, video, vfile, final)
    if shorten and step_shorten(zh_srt, work, stats, info, engine, effort, shorten):
        log("用缩短后的译文重新配音（其余句子直接用缓存）...")
        run_dub(zh_srt, work, video, vfile, final)
    # 记下最终译文对应的配音，下次运行可以直接复用
    key = hashlib.md5((Path(zh_srt).read_text(encoding="utf-8") + tag).encode()).hexdigest()[:10]
    if final.name != f"dub_{key}.wav":
        final = final.replace(work / f"dub_{key}.wav")
    return final


ZH_SCREEN = 26   # 中文字幕每屏最多字数
EN_SCREEN = 90   # 英文字幕每屏最多字符数


def split_at(text, k, cjk):
    """把文本切成 k 段，尽量在标点（英文时在空格）处断开，各段长度接近。"""
    if k <= 1:
        return [text]
    if cjk:
        pref = set("，。！？；：、,.!?;:")
        cuts, last = [], 0
        for j in range(1, k):
            target = round(len(text) * j / k)
            best = min((p for p in range(last + 1, len(text)) if text[p - 1] in pref),
                       key=lambda p: abs(p - target), default=None)
            if best is None or abs(best - target) > len(text) / k / 2:
                best = max(last + 1, target)
            cuts.append(best)
            last = best
        parts = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)])]
    else:
        words = text.split()
        sizes = np.cumsum([len(w) + 1 for w in words]) if words else []
        cuts, last = [], 0
        for j in range(1, k):
            target = sizes[-1] * j / k
            cand = [p for p in range(last + 1, len(words)) if words[p - 1][-1] in ",.!?;:"]
            p = min(cand, key=lambda p: abs(sizes[p - 1] - target), default=None)
            if p is None or abs(sizes[p - 1] - target) > sizes[-1] / k / 2:
                p = max(last + 1, int(np.searchsorted(sizes, target)) + 1)
            p = min(p, len(words) - (k - j))
            cuts.append(p)
            last = p
        parts = [" ".join(words[a:b]) for a, b in zip([0] + cuts, cuts + [len(words)])]
    return [x.strip() for x in parts]


def step_display_subs(work):
    """生成播放器显示用的字幕：长句按字数拆成几屏，三条字幕轨使用相同的时间切分。"""
    zh, en = parse_srt(work / "zh.srt"), parse_srt(work / "en.srt")
    outs = {"zh": [], "en": [], "bi": []}
    for z, e in zip(zh, en):
        a, b = (srt_sec(x) for x in z["time"].split("-->"))
        k = max(1, -(-len(z["text"]) // ZH_SCREEN), -(-len(e["text"]) // EN_SCREEN))
        # 每屏至少显示 1.2 秒；时间太短的句子宁可一屏多放几个字
        k = min(k, max(1, len(e["text"].split())), max(1, int((b - a) / 1.2)))
        zp, ep = split_at(z["text"], k, True), split_at(e["text"], k, False)
        w = np.array([max(1, len(x)) for x in zp], float)
        edges = a + (b - a) * np.concatenate([[0], np.cumsum(w) / w.sum()])
        for j in range(k):
            t = f"{sec_srt(edges[j])} --> {sec_srt(edges[j + 1])}"
            outs["zh"].append({"time": t, "text": zp[j]})
            outs["en"].append({"time": t, "text": ep[j]})
            outs["bi"].append({"time": t, "text": f"{zp[j]}\n{ep[j]}"})
    # 太短的字幕延长显示，最多到下一条开始前，避免一闪而过
    for i, cue in enumerate(outs["zh"]):
        a, b = (srt_sec(x) for x in cue["time"].split("-->"))
        nxt = srt_sec(outs["zh"][i + 1]["time"].split("-->")[0]) - 0.05 if i + 1 < len(outs["zh"]) else b + 1.5
        if b - a < 1.5:
            t = f"{sec_srt(a)} --> {sec_srt(max(b, min(a + 1.5, nxt)))}"
            for key in outs:
                outs[key][i]["time"] = t
    for key, name in (("zh", "zh_display.srt"), ("en", "en_display.srt"), ("bi", "bilingual_display.srt")):
        write_srt(work / name, outs[key])
    return len(zh), len(outs["zh"])


def probe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def probe_streams(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,duration,bit_rate,width,height",
                        "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


def measure_lufs(path, stream="0:a:0"):
    """用 EBU R128 测整体响度（LUFS），失败返回 None。"""
    r = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-map", stream,
                        "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m and float(m[-1]) > -70 else None


def step_loudness(video, wav, work):
    """测中文配音和英文原声的响度，结果缓存在 loudness.json。"""
    cache = work / "loudness.json"
    key = f"{wav.name}:{wav.stat().st_mtime:.0f}"
    if cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("key") == key:
            return d["zh"], d["en"]
    log("测量中文配音和英文原声的响度 ...")
    zh, en = measure_lufs(wav), measure_lufs(video)
    cache.write_text(json.dumps({"key": key, "zh": zh, "en": en}), encoding="utf-8")
    return zh, en


# MP4 里可以直接复制、不用重新编码的音频格式
MP4_AUDIO_COPY = {"aac", "mp3", "ac3", "eac3", "alac"}


def step_mux(video, wav, work, out, subs, sub_pos, crf, zh_bitrate, match_loudness=True, max_height=1080):
    """音轨：中文配音（默认）+ 英文原声；字幕：soft 模式下为中文、English、中英双语三条可选轨道。"""
    log(f"合成视频（字幕: {subs}，画面: {'重新压缩 crf ' + str(crf) if crf or subs == 'hard' else '直接复制'}）...")
    info = probe_streams(video)
    dur = f"{float(info['format']['duration']):.3f}"
    src_audio = next((st["codec_name"] for st in info["streams"] if st["codec_type"] == "audio"), None)

    tmp_out = out.with_name(out.stem + ".part.mp4")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav)]
    maps = ["-map", "0:v:0", "-map", "1:a:0"] + (["-map", "0:a:0"] if src_audio else [])
    vcodec = ["-c:v", "copy"]
    vf = []
    if subs == "hard" or crf:
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf or 20), "-pix_fmt", "yuv420p"]
        v = next((st for st in info["streams"] if st["codec_type"] == "video"), {})
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if max_height and h > max_height:
            # 按短边缩小，宽度保持偶数；竖屏视频同样按短边判断
            vf.append(f"scale=-2:{max_height}" if w >= h else f"scale={max_height}:-2")
            vf[-1] += ":flags=lanczos"
            log(f"  画面 {w}x{h} 缩小到短边 {max_height}")
    elif max_height:
        log("  --crf 0 直接复制画面，不缩小分辨率")
    if subs != "hard" and vf:
        vcodec = ["-vf", ",".join(vf)] + vcodec
    if subs == "hard":
        # 在工作目录里运行并使用相对路径，避免 subtitles 滤镜转义特殊字符
        # SRT 经 ffmpeg 转换后按旧版 SSA 编号解释 Alignment，6 表示顶部居中，用来避开原视频自带的底部字幕
        style = "FontName=Noto Sans CJK SC,FontSize=16,Outline=1,MarginV=20" + (",Alignment=6" if sub_pos == "top" else "")
        cmd += maps + ["-vf", ",".join(vf + [f"subtitles=bilingual_display.srt:force_style='{style}'"])] + vcodec
    elif subs == "soft":
        tracks = (("zh_display.srt", "chi", "中文"), ("en_display.srt", "eng", "English"),
                  ("bilingual_display.srt", "chi", "中英双语"))
        for f, _, _ in tracks:
            cmd += ["-i", f]
        cmd += maps + sum((["-map", f"{i + 2}:0"] for i in range(len(tracks))), []) + vcodec + ["-c:s", "mov_text"]
        for i, (_, lang, title) in enumerate(tracks):
            cmd += [f"-metadata:s:s:{i}", f"language={lang}", f"-metadata:s:s:{i}", f"title={title}",
                    f"-disposition:s:{i}", "0"]
    else:
        cmd += maps + vcodec
    # 中文配音：Edge-TTS 人声用 64k AAC 足够；英文原声能复制就直接复制，避免二次压缩
    cmd += ["-c:a:0", "aac", "-b:a:0", zh_bitrate, "-filter:a:0", "apad",
            "-metadata:s:a:0", "language=chi", "-metadata:s:a:0", "title=中文配音", "-disposition:a:0", "default"]
    if src_audio:
        gain = 0.0
        if match_loudness:
            zh_lufs, en_lufs = step_loudness(video, wav, work)
            if zh_lufs is not None and en_lufs is not None:
                gain = zh_lufs - en_lufs
        if abs(gain) >= 1.0:
            # 英文原声调到和中文配音相同的响度，切换音轨时音量一致；限幅器防止调大后削波
            log(f"  英文原声 {en_lufs:.1f} LUFS → {zh_lufs:.1f} LUFS（{gain:+.1f} dB）")
            src_rate = next((st.get("bit_rate") for st in info["streams"] if st["codec_type"] == "audio"), None)
            br = f"{min(192, max(96, int(src_rate or 128000) // 1000))}k"
            cmd += ["-filter:a:1", f"volume={gain:.2f}dB,alimiter=limit=0.89:level=false",
                    "-c:a:1", "aac", "-b:a:1", br]
        else:
            cmd += (["-c:a:1", "copy"] if src_audio in MP4_AUDIO_COPY else ["-c:a:1", "aac", "-b:a:1", "128k"])
        cmd += ["-metadata:s:a:1", "language=eng", "-metadata:s:a:1", "title=English 原声", "-disposition:a:1", "0"]
    cmd += ["-t", dur, "-movflags", "+faststart", str(tmp_out)]
    run(cmd, cwd=work)
    verify_output(tmp_out, float(dur), subs, bool(src_audio))
    tmp_out.replace(out)


def verify_output(path, want_dur, subs, has_src_audio):
    """检查成品的轨道数量和时长，避免把有问题的文件当成成品。"""
    info = probe_streams(path)
    kinds = [st["codec_type"] for st in info.get("streams", [])]
    want = {"video": 1, "audio": 1 + int(has_src_audio), "subtitle": 3 if subs == "soft" else 0}
    for k, n in want.items():
        if kinds.count(k) != n:
            die(f"成品轨道不对：{k} 应有 {n} 条，实际 {kinds.count(k)} 条: {path}")
    for st in info["streams"]:
        if st["codec_type"] in ("video", "audio") and abs(float(st.get("duration", want_dur)) - want_dur) > 1.5:
            die(f"成品 {st['codec_type']} 轨时长 {st.get('duration')} 秒，和原视频 {want_dur:.1f} 秒不一致: {path}")


# ---------------------------------------------------------------- 主流程
def work_key(url):
    """按链接生成工作目录名。YouTube 用视频 ID，其他网站用去掉查询参数的路径。"""
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if m:
        return f"yt_{m.group(1)}"
    return re.sub(r"[^\w-]+", "_", re.sub(r"^https?://", "", url.split("?")[0]))[-60:]


def safe_stem(path):
    m = re.findall(r"\[([A-Za-z0-9_-]{6,})\]", path.stem)
    if m:
        return m[-1]
    s = re.sub(r"[^\w.-]+", "_", path.stem).strip("_")
    return s[:60] or "video"


def clean_title(path):
    return re.sub(r"\s*\[[A-Za-z0-9_-]{6,}\]\s*$", "", path.stem).strip()


def main():
    ap = argparse.ArgumentParser(description="把英文视频转成中文配音视频（codex/claude 翻译 + Edge-TTS）")
    ap.add_argument("inputs", nargs="+", metavar="input",
                    help="一个或多个视频链接（YouTube、X 等）或本地视频文件；多个时依次处理，单个失败不影响后面的")
    ap.add_argument("-o", "--out-dir", type=Path, default=DEFAULT_OUT, help=f"输出目录，默认 {DEFAULT_OUT}")
    ap.add_argument("--subs", choices=["soft", "hard", "none"], default="soft",
                    help="soft=中文/English/双语三条可选字幕轨（默认），hard=双语字幕烧进画面，none=不加字幕；"
                         "音轨始终包含中文配音和英文原声")
    ap.add_argument("--sub-pos", choices=["bottom", "top"], default="bottom",
                    help="硬字幕位置；原视频底部已有字幕时用 top")
    ap.add_argument("--voice", default="auto",
                    help="auto=按说话人音高自动选男声或女声（默认）；也可指定一个 Edge-TTS 音色用于全部字幕")
    ap.add_argument("--male-voice", default="zh-CN-YunxiNeural", help="auto 模式下的男声，默认 zh-CN-YunxiNeural")
    ap.add_argument("--female-voice", default="zh-CN-XiaoxiaoNeural", help="auto 模式下的女声，默认 zh-CN-XiaoxiaoNeural")
    ap.add_argument("--no-source-subs", action="store_true", help="不用网站提供的英文字幕，始终做语音识别")
    ap.add_argument("--model", default="medium", help="faster-whisper 模型：small/medium/large-v3")
    ap.add_argument("--cpu", action="store_true", help="语音识别不用显卡")
    ap.add_argument("--hotwords", help="语音识别热词，逗号分隔；默认由模型从标题和简介中提取，传空字符串关闭")
    ap.add_argument("--no-glossary", action="store_true", help="翻译前不生成术语表")
    ap.add_argument("--no-review", action="store_true", help="翻译后不做逐句校对")
    ap.add_argument("--shorten", type=int, default=15,
                    help="配音需要提速超过这个百分比的句子交给模型缩短后重配，默认 15；0 关闭")
    ap.add_argument("--engine", choices=["codex", "claude"], default="codex",
                    help="翻译用的命令行工具，默认 codex；claude 的模型由 CLAUDE_MODEL 指定，默认 opus")
    ap.add_argument("--chunk", type=int, default=100, help="每次交给模型翻译的字幕条数")
    ap.add_argument("--effort", default="low", help="codex 推理强度：low/medium/high（仅 codex）")
    ap.add_argument("--title", help="视频主题，帮助模型选对术语；默认用网页标题或文件名")
    ap.add_argument("--crf", type=int, default=23,
                    help="画面重新压缩的质量，默认 23（数值越大文件越小、画质越差）；0 表示直接复制原画面，不压缩")
    ap.add_argument("--max-height", type=int, default=1080,
                    help="画面短边超过这个像素就缩小，默认 1080（2K/4K 视频缩到 1080p）；0 表示保持原分辨率")
    ap.add_argument("--zh-bitrate", default="64k", help="中文配音音轨码率，默认 64k")
    ap.add_argument("--keep-en-volume", action="store_true",
                    help="英文原声保持原始音量直接复制；默认调到和中文配音相同的响度")
    ap.add_argument("--clean", action="store_true", help="成功后删除工作目录（之后无法从断点续跑）")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe", args.engine):
        if not shutil.which(tool):
            die(f"找不到 {tool}")
    if not (PVT_DIR / ".venv/bin/python").exists():
        die(f"找不到 pyVideoTrans：{PVT_DIR}（可用环境变量 PVT_DIR 指定）")

    args.out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for n, src in enumerate(args.inputs, 1):
        if len(args.inputs) > 1:
            log(f"===== [{n}/{len(args.inputs)}] {src}")
        try:
            results.append((src, process_one(src, args)))
        except SystemExit as e:
            if len(args.inputs) == 1:
                raise
            results.append((src, None))
            log(f"处理失败，继续下一个（{e}）")
    if len(args.inputs) > 1:
        print("\n汇总:")
        for src, out in results:
            print(f"  {'成功' if out else '失败'}  {src}" + (f"\n        → {out}" if out else ""))
        if not all(out for _, out in results):
            sys.exit(1)


def process_one(src, args):
    t0 = time.time()
    meta = {"title": "", "description": "", "uploader": "", "en_subs": False}
    is_url = re.match(r"^https?://", src) is not None
    if is_url:
        key = work_key(src)
        work = args.out_dir / f".zh_work_{key}"
        work.mkdir(parents=True, exist_ok=True)
        meta = step_meta(src, work)
        video = step_download(src, work)
    else:
        video = Path(src).expanduser().resolve()
        if not video.is_file():
            die(f"文件不存在: {video}")
        work = args.out_dir / f".zh_work_{safe_stem(video)}"
        work.mkdir(parents=True, exist_ok=True)

    title = (args.title if len(args.inputs) == 1 else None) or meta["title"] or clean_title(video)
    log(f"视频: {title}（{probe_duration(video) / 60:.1f} 分钟）")
    log(f"工作目录: {work}")

    from_site = (work / "en_from_site").exists()
    if is_url and meta["en_subs"] and not args.no_source_subs and not (work / "en.srt").exists():
        from_site = step_source_subs(src, work)
    hotwords = args.hotwords if args.hotwords is not None else (
        "" if from_site else step_hotwords(work, meta, title, args.engine, args.effort))
    en = step_stt(video, work, args.model, not args.cpu, hotwords)

    background = [f"视频标题：{title}"]
    if meta["uploader"]:
        background.append(f"发布者：{meta['uploader']}")
    if meta["description"]:
        background.append("视频简介（帮助理解主题和专有名词）：" + re.sub(r"\s+", " ", meta["description"])[:800])
    asr_note = "" if from_site else (
        "5. 英文字幕来自语音识别，可能有同音或近音错误，专有名词尤其容易被识别成拼写相近的普通单词。"
        "请结合标题、简介和上下文判断说话人的原意并按正确意思翻译；\n")
    info = {"background": "\n".join(background), "asr_note": asr_note}
    if not args.no_glossary:
        info["glossary"] = step_glossary(en, work, info, args.engine, args.effort)
    zh = step_translate(en, work, info, args.chunk, args.effort, args.engine)
    if not args.no_review:
        step_review(en, zh, work, info, args.engine, args.effort)
    wav = step_tts(zh, work, video, args.voice, args.male_voice, args.female_voice,
                   info, args.engine, args.effort, args.shorten / 100)
    n_src, n_disp = step_display_subs(work)
    if n_disp > n_src:
        log(f"显示字幕：{n_src} 句拆成 {n_disp} 屏，每屏中文不超过 {ZH_SCREEN} 字")

    # 去掉 emoji 等符号和文件名非法字符
    base = re.sub(r'[\\/:*?"<>|]+', "_", re.sub(r"[^\w\s.,'()&+\-_/:|\u3000-\u303f\uff00-\uffef]", "", title))
    base = re.sub(r"\s+", " ", base)[:80].strip(" ._") or safe_stem(video)
    out = args.out_dir / f"{base}_中文配音.mp4"
    step_mux(video, wav, work, out, args.subs, args.sub_pos, args.crf, args.zh_bitrate, not args.keep_en_volume,
             args.max_height)
    for name, dst in (("zh_display.srt", f"{base}.zh.srt"), ("en_display.srt", f"{base}.en.srt")):
        # 外挂字幕加 UTF-8 BOM，Windows 上的播放器才不会按 GBK 解码出乱码
        (args.out_dir / dst).write_text((work / name).read_text(encoding="utf-8"), encoding="utf-8-sig")

    size = out.stat().st_size / 1e6
    if args.clean:
        shutil.rmtree(work, ignore_errors=True)
    log(f"完成，用时 {(time.time() - t0) / 60:.1f} 分钟")
    print(f"\n成品: {out}（{size:.0f} MB）\n字幕: {args.out_dir / (base + '.zh.srt')}"
          + ("" if args.clean else f"\n工作目录（可删除）: {work}"))
    return out


if __name__ == "__main__":
    main()
