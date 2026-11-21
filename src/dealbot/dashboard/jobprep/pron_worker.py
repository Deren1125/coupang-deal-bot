"""발음 채점 일꾼 — 따로 뜨는 짧은 프로세스 (onnxruntime·numpy 는 여기서만 import). pron.py 가 띄운다.

    python -m dealbot.dashboard.jobprep.pron_worker <models_dir> <wav> <job.json> <out.json>

① 로짓 → 목표 음소열 CTC 강제 정렬 (numpy Viterbi) ② GOP = 구간 안 목표 음소 평균 로그확률 − 가장 높은 다른 음소 → 0~100 (상수 GOP_MID·GOP_SCALE)
③ 자유 인식(greedy) = '이렇게 들렸어요' · 소리 묶음별 평균 · 강세(강세 모음 구간이 이웃 모음보다 긴가 — 참고).
"""

from __future__ import annotations

import json
import math
import sys
import wave
from pathlib import Path
from typing import Any

GOP_MID = -1.0
GOP_SCALE = 1.5


def log_softmax(x: Any) -> Any:
    import numpy as np

    m = x.max(axis=-1, keepdims=True)
    return x - m - np.log(np.exp(x - m).sum(axis=-1, keepdims=True))


def ctc_align(logp: Any, targets: list[int], blank: int) -> list[tuple[int, int]]:
    """CTC 강제 정렬 (Viterbi) → 목표마다 (시작 프레임, 끝 프레임 포함). 프레임이 모자라면 []."""
    import numpy as np

    t_len = logp.shape[0]
    ext = [blank]
    for t in targets:
        ext += [t, blank]
    s_len = len(ext)
    if t_len < len(targets):
        return []
    neg = -1e30
    dp = np.full((t_len, s_len), neg)
    bp = np.zeros((t_len, s_len), dtype=np.int32)
    dp[0, 0] = logp[0, ext[0]]
    if s_len > 1:
        dp[0, 1] = logp[0, ext[1]]
    for t in range(1, t_len):
        for s in range(s_len):
            best, arg = dp[t - 1, s], s
            if s >= 1 and dp[t - 1, s - 1] > best:
                best, arg = dp[t - 1, s - 1], s - 1
            if s >= 2 and ext[s] != blank and ext[s] != ext[s - 2] and dp[t - 1, s - 2] > best:
                best, arg = dp[t - 1, s - 2], s - 2
            dp[t, s] = best + logp[t, ext[s]]
            bp[t, s] = arg
    s = s_len - 1 if s_len < 2 or dp[-1, s_len - 1] >= dp[-1, s_len - 2] else s_len - 2
    path = [0] * t_len
    for t in range(t_len - 1, -1, -1):
        path[t] = s
        s = bp[t, s]
    spans: list[list[int]] = [[-1, -1] for _ in targets]
    for t, s in enumerate(path):
        if s % 2 == 1:
            k = s // 2
            if spans[k][0] < 0:
                spans[k][0] = t
            spans[k][1] = t
    return [(a, b) for a, b in spans]


def gop_scores(logp: Any, targets: list[int], spans: list[tuple[int, int]], blank: int) -> list[float]:
    import numpy as np

    out = []
    for tid, (a, b) in zip(targets, spans, strict=False):
        if a < 0:
            out.append(0.0)
            continue
        seg = logp[a:b + 1]
        mask = np.ones(seg.shape[1], dtype=bool)
        mask[[tid, blank]] = False
        other = seg[:, mask].max(axis=1)
        out.append(float((seg[:, tid] - other).mean()))
    return out


def to_score(gop: float) -> float:
    return round(100.0 / (1.0 + math.exp(-(gop - GOP_MID) / GOP_SCALE)), 1)


def greedy(logp: Any, id2tok: dict[int, str], blank: int) -> str:
    ids = logp.argmax(axis=1).tolist()
    out, prev = [], None
    for i in ids:
        if i != prev and i != blank:
            out.append(id2tok.get(i, ""))
        prev = i
    return " ".join(x for x in out if x)


def target_ids(ipa: str, vocab: dict[str, int]) -> list[int]:
    if ipa in vocab:
        return [vocab[ipa]]
    return [vocab[c] for c in ipa if c in vocab]


def score(logp: Any, job: dict[str, Any], vocab: dict[str, int], blank: int) -> dict[str, Any]:
    phones = job["phones"]
    ids, owner = [], []
    for k, p in enumerate(phones):
        for i in target_ids(p["ipa"], vocab):
            ids.append(i)
            owner.append(k)
    spans = ctc_align(logp, ids, blank)
    if not spans:
        return {"score": 0.0, "phones": [], "groups": {}, "heard": "", "note": "소리가 너무 짧아요"}
    gops = gop_scores(logp, ids, spans, blank)
    per: list[dict[str, Any]] = []
    for k, p in enumerate(phones):
        gs = [g for g, o in zip(gops, owner, strict=False) if o == k]
        sp = [s for s, o in zip(spans, owner, strict=False) if o == k]
        frames = sum(b - a + 1 for a, b in sp if a >= 0)
        per.append({"arpa": p["arpa"], "ipa": p["ipa"], "word": p["word"], "score": to_score(sum(gs) / len(gs)) if gs else 0.0, "frames": frames,
                    "stress": p.get("stress")})
    groups = {}
    for g, arps in (job.get("groups") or {}).items():
        v = [x["score"] for x in per if x["arpa"] in arps]
        if v:
            groups[g] = round(sum(v) / len(v), 1)
    vowels = [x for x in per if x["stress"] is not None]
    stress_ok = None
    if len(vowels) >= 2:
        hits = [v for v in vowels if v["stress"] == 1]
        others = [v["frames"] for v in vowels if v["stress"] != 1]
        if hits and others:
            stress_ok = sum(v["frames"] for v in hits) / len(hits) >= sum(others) / len(others)
    inv = {i: t for t, i in vocab.items()}
    return {"score": round(sum(x["score"] for x in per) / max(1, len(per)), 1), "phones": per, "groups": groups,
            "heard": greedy(logp, inv, blank), "stress_ok": stress_ok}


def read_wav(path: Path) -> Any:
    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("16k 모노 s16 만")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    x = x[: 16000 * 15]
    return (x - x.mean()) / (x.std() + 1e-7)


def main(argv: list[str]) -> int:
    import numpy as np
    import onnxruntime as ort

    models, wav, job_p, out_p = (Path(a) for a in argv[1:5])
    vocab = json.loads((models / "vocab.json").read_text(encoding="utf-8"))
    blank = int(vocab.get("<pad>", 0))
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(models / "w2v_espeak_int8.onnx"), so, providers=["CPUExecutionProvider"])
    x = read_wav(wav)[None, :].astype(np.float32)
    logits = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
    res = score(log_softmax(logits.astype(np.float64)), json.loads(job_p.read_text(encoding="utf-8")), vocab, blank)
    out_p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
