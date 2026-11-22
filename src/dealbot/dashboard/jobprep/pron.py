"""⑧ 발음 채점 — 딜봇 프로세스 안에서는 모델을 돌리지 않는다. 녹음 → ffmpeg 16k wav → 'queued' → 일꾼(pron_worker)을 그때그때 하나 띄움.

일꾼: `nice -n 19 python -m dealbot.dashboard.jobprep.pron_worker <models> <wav> <job.json> <out.json>` · 한 번에 1개 · 여유 메모리가
pron_min_free_mb(1000MB) 아래면 띄우지 않고 기다림(tick 이 나중에) · 60초 넘으면 죽임 · systemd 가 되면 메모리 1200MB 상한 범위(scope)로 감쌈
(같은 일반 사용자 — 특권 없음). 모델·CMUdict 가 없으면(tools/jobprep_setup.py 를 아직 안 돌림) 발음 칸은 '준비 중'.
목표 음소: 문장 → CMUdict(ARPAbet·강세) → 고정 표(ARPA_IPA, 39개) → espeak IPA. 연습 문장은 모든 낱말이 CMUdict 에 있는 것만.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.jobprep import store as storemod
from dealbot.dashboard.jobprep.store import JpError, clip

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
MODEL = "w2v_espeak_int8.onnx"
VOCAB = "vocab.json"
CMU = "cmudict.dict"
WORKER_S = 60
MAX_AUDIO_S = 15
ARPA_IPA = {"AA": "ɑː", "AE": "æ", "AH": "ʌ", "AO": "ɔː", "AW": "aʊ", "AY": "aɪ", "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "EH": "ɛ",
            "ER": "ɜː", "EY": "eɪ", "F": "f", "G": "ɡ", "HH": "h", "IH": "ɪ", "IY": "iː", "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n",
            "NG": "ŋ", "OW": "oʊ", "OY": "ɔɪ", "P": "p", "R": "ɹ", "S": "s", "SH": "ʃ", "T": "t", "TH": "θ", "UH": "ʊ", "UW": "uː", "V": "v",
            "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ"}
UNSTRESSED = {"AH": "ə", "ER": "ɚ"}
GROUPS = {"r/l": ("R", "L"), "th": ("TH", "DH"), "f/p": ("F", "P"), "v/b": ("V", "B"), "z": ("Z",), "긴/짧은 모음": ("IY", "IH", "UW", "UH"),
          "æ/ɛ": ("AE", "EH")}
SEED = [
    "The red lorry rolled along the long road.", "Really light rain fell all over the rural lane.", "Larry rarely reads the library rules.",
    "Laura will rent a little room near the river.", "The royal family lived in a large castle.", "Please bring the blue glass to the right.",
    "I think three things are worth the effort.", "They gathered with their brothers on Thursday.", "Thank you for the thoughtful birthday gift.",
    "This is the third time this month.", "The weather there is rather warm.", "Both of them think it is a myth.",
    "Five people found a fine coffee place.", "Please put the paper in the folder.", "The fan is full of fresh flowers.",
    "Philip feels proud of his perfect plan.", "A fox ran past the pink fence.", "Fill the cup with fresh milk first.",
    "Very few voters believe the vote was fair.", "The van brought a big box of vegetables.", "Bob loves the view from the boat.",
    "Victor visited a beautiful valley in November.", "My brother drives a black van every Sunday.", "Leave the bag by the big oven.",
    "The zoo is busy on these days.", "His zipper is broken again.", "Zoe was amazed by the size of the prize.",
    "These cheese pizzas are easy to freeze.", "Is his cousin still in the music business?", "The bees buzz around the roses.",
    "Please sit in this seat.", "The ship left the sheep on the hill.", "Fill the wheel with clean air.", "I will leave it for a little while.",
    "Look at the moon over the pool.", "The full moon looks good tonight.", "He should put the food in the cool room.",
    "Did you read the book about the sea?", "The bad man sat on the bed.", "Ten men had a pen and a pan.", "The cat sat on the black mat.",
    "Send the bag back to Dan.", "Get the red apple from the bag.", "Many families went to the beach after lunch.",
    "My friend works at a bank in the city.", "We should meet at the station at seven.", "Could you tell me where the nearest bus stop is?",
    "I would like to order a cup of coffee.", "She is studying electrical engineering at university.", "Our team finished the project last week.",
    "It is important to listen carefully.", "The weather report said it would rain tomorrow.", "I usually walk to work in the morning.",
    "He plays the violin very well.", "They asked a lot of questions about the plan.", "What do you think about this idea?",
    "I really appreciate your help with the report.", "The interview will start in ten minutes.", "Let me think about it for a moment.",
    "Thank you very much for your time today.",
]
_cmu_cache: tuple[float, dict[str, list[str]]] | None = None


def models_dir(jp: Any) -> Path:
    return jp.store.root / "models"


def ready(jp: Any) -> bool:
    d = models_dir(jp)
    return all((d / n).is_file() for n in (MODEL, VOCAB, CMU))


def load_cmu(path: Path) -> dict[str, list[str]]:
    global _cmu_cache
    try:
        mt = path.stat().st_mtime
    except OSError:
        return {}
    if _cmu_cache and _cmu_cache[0] == mt:
        return _cmu_cache[1]
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith(";;;"):
            continue
        parts = line.split("#", 1)[0].split()
        if len(parts) < 2:
            continue
        w = re.sub(r"\(\d+\)$", "", parts[0].lower())
        out.setdefault(w, parts[1:])
    _cmu_cache = (mt, out)
    return out


def words_of(sentence: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z']+", sentence or "") if w.strip("'")]


def sentence_ok(sentence: str, cmu: dict[str, list[str]]) -> bool:
    ws = words_of(sentence)
    return bool(ws) and all(w in cmu or w.strip("'") in cmu for w in ws)


def target(sentence: str, cmu: dict[str, list[str]]) -> list[dict[str, Any]]:
    """문장 → [{arpa, ipa, stress, word}] (CMUdict 에 없는 낱말이 있으면 ValueError)."""
    out = []
    for w in words_of(sentence):
        pr = cmu.get(w) or cmu.get(w.strip("'"))
        if not pr:
            raise ValueError(w)
        for ph in pr:
            m = re.fullmatch(r"([A-Z]+)([012])?", ph)
            if not m or m.group(1) not in ARPA_IPA:
                continue
            base, st = m.group(1), m.group(2)
            ipa = UNSTRESSED[base] if (base in UNSTRESSED and st == "0") else ARPA_IPA[base]
            out.append({"arpa": base, "ipa": ipa, "stress": int(st) if st else None, "word": w})
    return out


def seed_sentences(jp: Any) -> int:
    if jp.store.one("SELECT id FROM pron_sents LIMIT 1"):
        return 0
    cmu = load_cmu(models_dir(jp) / CMU)
    n = 0
    for s in SEED:
        if not cmu or sentence_ok(s, cmu):
            focus = ",".join(g for g, arps in GROUPS.items() if cmu and any(p["arpa"] in arps for p in target(s, cmu)))
            jp.store.x("INSERT OR IGNORE INTO pron_sents (text, focus, src, at) VALUES (?,?,?,?)", (s, focus, "seed", jp.clock()))
            n += 1
    return n


def weak_groups(jp: Any, n: int = 2) -> list[str]:
    sums: dict[str, list[float]] = {}
    for r in jp.store.q("SELECT per_phone FROM pron_takes WHERE state='done' ORDER BY at DESC LIMIT 60"):
        try:
            groups = (json.loads(r["per_phone"] or "{}") or {}).get("groups") or {}
        except ValueError:
            continue
        for g, v in groups.items():
            if isinstance(v, (int, float)):
                sums.setdefault(g, []).append(float(v))
    avg = sorted(((sum(v) / len(v), g) for g, v in sums.items() if v))
    return [g for _a, g in avg[:n]]


def todays_set(jp: Any, k: int = 8) -> list[dict[str, Any]]:
    seed_sentences(jp)
    weak = weak_groups(jp)
    rows = jp.store.q("SELECT * FROM pron_sents ORDER BY id")
    if not rows:
        return []
    day = int(datetime.fromtimestamp(jp.clock(), KST).strftime("%j"))
    picked = [r for r in rows if weak and any(w in (r["focus"] or "") for w in weak)][:k // 2]
    rest = [r for r in rows if r not in picked]
    start = (day * 5) % max(1, len(rest))
    picked += (rest[start:] + rest[:start])[:k - len(picked)]
    return picked


def view_set(jp: Any) -> dict[str, Any]:
    return {"ready": ready(jp), "sentences": todays_set(jp) if ready(jp) else [], "weak": weak_groups(jp),
            "queued": (jp.store.one("SELECT COUNT(*) n FROM pron_takes WHERE state='queued'") or {}).get("n", 0)}


def history(jp: Any) -> dict[str, Any]:
    rows = jp.store.q("SELECT date(at, 'unixepoch', '+9 hours') d, AVG(score) s, COUNT(*) n FROM pron_takes WHERE state='done' GROUP BY d ORDER BY d DESC LIMIT 60")
    return {"days": list(reversed(rows))}


def take_view(jp: Any, tid: int) -> dict[str, Any]:
    r = jp.store.one("SELECT * FROM pron_takes WHERE id=?", (tid,))
    if r is None:
        raise JpError(404, "gone", "그 녹음이 없어요")
    out = {"id": r["id"], "state": r["state"], "score": r["score"], "heard": r["heard"] or ""}
    if r["state"] == "done":
        try:
            out["detail"] = json.loads(r["per_phone"] or "{}")
        except ValueError:
            out["detail"] = {}
    return out


def enqueue(jp: Any, sent_id: int, wav_name: str) -> int:
    s = jp.store.one("SELECT id FROM pron_sents WHERE id=?", (sent_id,))
    if s is None:
        raise JpError(404, "gone", "그 문장이 없어요")
    return jp.store.x("INSERT INTO pron_takes (sent_id, at, score, per_phone, state, file, heard) VALUES (?,?,NULL,'','queued',?,'')",
                      (sent_id, jp.clock(), wav_name))


def worker_cmd(jp: Any, wav: Path, job: Path, out: Path) -> list[str]:
    base = ["nice", "-n", "19", sys.executable, "-I", "-m", "dealbot.dashboard.jobprep.pron_worker", str(models_dir(jp)), str(wav), str(job), str(out)]
    if jp.sandbox.mode() == "systemd":
        return ["sudo", "-n", "systemd-run", "--quiet", "--scope", "-p", "MemoryMax=1200M", "-p", "MemorySwapMax=0",
                f"--uid={os.getuid()}", f"--gid={os.getgid()}", *base]
    return base


async def run_worker(cmd: list[str], timeout: float = WORKER_S) -> int:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, 9)
        await proc.wait()
        return -9


STALE_S = 120
PRON_TRIES = 2


def reset_stale(jp: Any) -> int:
    """일꾼이 없는데 'running' 으로 STALE_S(2분) 넘게 남은 것 (재시작 등) → 한 번 더 기다림, 이미 두 번 해 봤으면 오류."""
    t = jp.bg.get("pron")
    if t is not None and not t.done():
        return 0
    n = 0
    for r in jp.store.q("SELECT id, tries, started_at FROM pron_takes WHERE state='running'"):
        if jp.clock() - float(r["started_at"] or 0) < STALE_S:
            continue
        if int(r["tries"] or 0) < PRON_TRIES:
            jp.store.x("UPDATE pron_takes SET state='queued' WHERE id=?", (r["id"],))
        else:
            jp.store.x("UPDATE pron_takes SET state='error', heard='채점이 두 번 끊겼어요 — 다시 녹음해 주세요' WHERE id=?", (r["id"],))
        n += 1
    return n


def pump(jp: Any, *, meminfo: str | None = None) -> str:
    """기다리는 채점 하나를 일꾼에게 (도는 중·메모리 부족·모델 없음이면 그대로). 돌려줌: started|busy|low_mem|not_ready|idle."""
    t = jp.bg.get("pron")
    if t is not None and not t.done():
        return "busy"
    reset_stale(jp)
    r = jp.store.one("SELECT * FROM pron_takes WHERE state='queued' ORDER BY at LIMIT 1")
    if r is None:
        return "idle"
    if not ready(jp):
        return "not_ready"
    if storemod.mem_free_mb(meminfo) < int(jp.cfg().pron_min_free_mb):
        return "low_mem"
    jp.store.x("UPDATE pron_takes SET state='running', started_at=?, tries=COALESCE(tries,0)+1 WHERE id=?", (jp.clock(), r["id"]))
    jp.bg["pron"] = asyncio.ensure_future(_score(jp, r))
    return "started"


async def _score(jp: Any, r: dict[str, Any]) -> None:
    tmp = jp.store.ensure_dir("tmp")
    job, out = tmp / f"pron_{r['id']}_job.json", tmp / f"pron_{r['id']}_out.json"
    try:
        s = jp.store.one("SELECT text FROM pron_sents WHERE id=?", (r["sent_id"],))
        cmu = load_cmu(models_dir(jp) / CMU)
        phones = target(s["text"] if s else "", cmu)
        job.write_text(json.dumps({"phones": phones, "groups": {g: list(a) for g, a in GROUPS.items()}}), encoding="utf-8")
        wav = jp.store.path("pron", r["file"])
        code = await jp.run_worker(worker_cmd(jp, wav, job, out))
        res = json.loads(out.read_text(encoding="utf-8")) if code == 0 and out.exists() else None
        if not isinstance(res, dict) or "score" not in res:
            jp.store.x("UPDATE pron_takes SET state='error', heard=? WHERE id=?", ("채점하지 못했어요" if code != -9 else "시간 초과", r["id"]))
            return
        jp.store.x("UPDATE pron_takes SET state='done', score=?, per_phone=?, heard=? WHERE id=?",
                   (float(res["score"]), json.dumps(res, ensure_ascii=False), clip(res.get("heard"), 400), r["id"]))
    except Exception as e:  # noqa: BLE001
        log.info("jobprep pron %s: %s", r["id"], type(e).__name__)
        jp.store.x("UPDATE pron_takes SET state='error', heard=? WHERE id=?", ("채점하지 못했어요", r["id"]))
    finally:
        for p in (job, out):
            with contextlib.suppress(OSError):
                p.unlink()


PRACTICE_SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {"type": "string"}}}, "required": ["sentences"]}


async def weekly_practice(jp: Any) -> int:
    weak = weak_groups(jp)
    if not weak or not ready(jp):
        return 0
    cmu = load_cmu(models_dir(jp) / CMU)
    out = await jp.llm.ask(f"pron.practice:{','.join(weak)}", "good", "You write short, natural English practice sentences using only common words.",
                              f"Write 10 sentences (6-12 words) that contain many of these sounds: {', '.join(weak)}.", schema=PRACTICE_SCHEMA)
    n = 0
    for s in out.get("sentences") or []:
        s = clip(s, 160)
        if sentence_ok(s, cmu):
            n += jp.store.x("INSERT OR IGNORE INTO pron_sents (text, focus, src, at) VALUES (?,?,?,?)", (s, ",".join(weak), "gemini", jp.clock())) and 1
    return n
