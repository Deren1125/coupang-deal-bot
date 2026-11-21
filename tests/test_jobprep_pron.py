"""취준 발음 — ARPAbet→IPA 표 39개 · CMUdict 에 없는 낱말 문장 거절 · 가짜 로짓으로 정렬 · GOP 점수 순서 · 메모리 부족이면 기다림(일꾼 안 띄움)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dealbot.dashboard.jobprep import pron
from jobprep_helpers import make_jp

CMU = "HELLO  HH AH0 L OW1\nWORLD  W ER1 L D\nTHINK  TH IH1 NG K\nREAD  R IY1 D\nREAD(2)  R EH1 D\n"


def test_table_has_all_39() -> None:
    arpa = {"AA", "AE", "AH", "AO", "AW", "AY", "B", "CH", "D", "DH", "EH", "ER", "EY", "F", "G", "HH", "IH", "IY", "JH", "K", "L", "M", "N", "NG",
            "OW", "OY", "P", "R", "S", "SH", "T", "TH", "UH", "UW", "V", "W", "Y", "Z", "ZH"}
    assert set(pron.ARPA_IPA) == arpa and len(arpa) == 39
    assert len(pron.SEED) == 60


def test_cmudict_check_and_target(tmp_path: Path) -> None:
    p = tmp_path / "c.dict"
    p.write_text(CMU)
    cmu = pron.load_cmu(p)
    assert pron.sentence_ok("Hello world!", cmu) and not pron.sentence_ok("Hello qwzx", cmu)
    t = pron.target("hello world", cmu)
    assert [x["ipa"] for x in t] == ["h", "ə", "l", "oʊ", "w", "ɜː", "l", "d"] and t[3]["stress"] == 1
    with pytest.raises(ValueError):
        pron.target("hello qwzx", cmu)
    assert cmu["read"] == ["R", "IY1", "D"]  # 첫 발음


def test_align_and_gop_order() -> None:
    np = pytest.importorskip("numpy")
    from dealbot.dashboard.jobprep import pron_worker as pw

    vocab = {"<pad>": 0, "a": 1, "b": 2, "c": 3}
    t = 12
    logits = np.full((t, 4), -5.0)
    frames = [0] * 2 + [1] * 3 + [0] + [2] * 3 + [3] * 3  # 'a' 'b' 는 맞게, 'c' 자리에 진짜는 'c' — 목표 'a b a' 로 마지막을 틀리게
    for i, k in enumerate(frames):
        logits[i, k] = 5.0
    logp = pw.log_softmax(logits)
    spans = pw.ctc_align(logp, [1, 2, 1], 0)
    assert len(spans) == 3 and spans[0][0] >= 2 and spans[1][0] >= spans[0][1]
    g = pw.gop_scores(logp, [1, 2, 1], spans, 0)
    assert g[0] > g[2] and g[1] > g[2]
    assert pw.to_score(g[0]) > 90 > pw.to_score(g[2])
    job = {"phones": [{"arpa": "AA", "ipa": "a", "word": "x", "stress": 1}, {"arpa": "B", "ipa": "b", "word": "x", "stress": None},
                      {"arpa": "AA", "ipa": "a", "word": "x", "stress": 0}], "groups": {"v/b": ["V", "B"]}}
    res = pw.score(logp, job, vocab, 0)
    assert res["groups"]["v/b"] > 90 and "c" in res["heard"] and res["phones"][2]["score"] < 50


def test_low_memory_waits_without_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jp = make_jp(tmp_path, monkeypatch)
    d = jp.store.ensure_dir("models")
    for n in (pron.MODEL, pron.VOCAB):
        (d / n).write_text("{}")
    (d / pron.CMU).write_text(CMU)
    sid = jp.store.x("INSERT INTO pron_sents (text, focus, src, at) VALUES ('hello world', '', 'seed', 0)")
    tid = pron.enqueue(jp, sid, "x.wav")
    started = []

    async def fake_worker(cmd: list[str], timeout: float = 60) -> int:
        started.append(cmd)
        return 0

    jp.run_worker = fake_worker
    assert pron.pump(jp, meminfo="MemAvailable: 512000 kB\n") == "low_mem"
    assert started == [] and pron.take_view(jp, tid)["state"] == "queued"


async def test_worker_result_is_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jp = make_jp(tmp_path, monkeypatch)
    d = jp.store.ensure_dir("models")
    for n in (pron.MODEL, pron.VOCAB):
        (d / n).write_text("{}")
    (d / pron.CMU).write_text(CMU)
    jp.store.write("pron", "x.wav", b"RIFF")
    sid = jp.store.x("INSERT INTO pron_sents (text, focus, src, at) VALUES ('hello world', '', 'seed', 0)")
    tid = pron.enqueue(jp, sid, "x.wav")

    async def fake_worker(cmd: list[str], timeout: float = 60) -> int:
        assert cmd[:3] == ["nice", "-n", "19"] and "dealbot.dashboard.jobprep.pron_worker" in cmd
        job = json.loads(Path(cmd[-2]).read_text())
        assert [p["arpa"] for p in job["phones"]][:2] == ["HH", "AH"]
        Path(cmd[-1]).write_text(json.dumps({"score": 77.5, "groups": {"r/l": 60.0}, "heard": "h ə l oʊ", "phones": []}))
        return 0

    jp.run_worker = fake_worker
    assert pron.pump(jp, meminfo="MemAvailable: 4000000 kB\n") == "started"
    await asyncio.wait_for(jp.bg["pron"], 5)
    v = pron.take_view(jp, tid)
    assert v["state"] == "done" and v["score"] == 77.5 and v["detail"]["groups"]["r/l"] == 60.0
    assert pron.weak_groups(jp) == ["r/l"]
    assert not any(p.name.startswith("pron_") for p in jp.store.path("tmp").iterdir())


def test_not_ready_without_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jp = make_jp(tmp_path, monkeypatch)
    assert pron.view_set(jp) == {"ready": False, "sentences": [], "weak": [], "queued": 0}
