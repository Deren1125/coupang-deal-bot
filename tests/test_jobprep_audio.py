"""취준 소리 — silencedetect 글 파싱 · 쉼 계산 · 자막 낱말 시각 → 쉼 (ffmpeg 는 가짜 stderr)."""

from __future__ import annotations

from dealbot.dashboard.jobprep import audio

STDERR = """Input #0, wav, from 'x.wav':
  Duration: 00:00:06.00, bitrate: 256 kb/s
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 0.4 | silence_duration: 0.4
[silencedetect @ 0x1] silence_start: 2.0
[silencedetect @ 0x1] silence_end: 2.6 | silence_duration: 0.6
[silencedetect @ 0x1] silence_start: 3.5
[silencedetect @ 0x1] silence_end: 3.6 | silence_duration: 0.1
[silencedetect @ 0x1] silence_start: 5.5
"""


def test_parse_and_measure() -> None:
    sil, dur = audio.parse_silence(STDERR)
    assert dur == 6.0 and sil[0] == (0.0, 0.4) and sil[-1] == (5.5, None)
    m = audio.measure_from(sil, dur, words=10)
    assert m["pauses"] == [{"at": 1.6, "len": 0.6}]  # 0.1초는 쉼 아님 · 앞뒤 무음 뺌
    assert m["speech_s"] == 6.0 - 0.4 - 0.6 - 0.1 - 0.5
    assert m["wps"] == round(10 / m["speech_s"], 2) and m["talk_span"] == 5.1


def test_json3_words_and_pauses() -> None:
    data = {"events": [{"tStartMs": 1000, "segs": [{"utf8": "I"}, {"utf8": " told", "tOffsetMs": 300}, {"utf8": " my", "tOffsetMs": 600}]},
                       {"tStartMs": 3000, "segs": [{"utf8": "wife"}, {"utf8": "\n"}]}]}
    ws = audio.json3_words(data)
    assert [w["w"] for w in ws] == ["I", "told", "my", "wife"] and ws[1]["t"] == 1.3
    p = audio.word_pauses(ws, 0.5, 5.0)
    assert len(p["pauses"]) == 1 and p["pauses"][0]["before"] == "wife" and p["pauses"][0]["len"] == round(3.0 - 1.6 - 0.35, 2)
    assert audio.pause_before(ws, "Wife,", 1.0) == round(3.0 - 1.6 - 0.35, 2)
    c = audio.compare({"wps": 2.0, "pauses": [1]}, {"wps": 4.0, "pauses": []}, 0.8, 0.5)
    assert c == {"wps_ratio": 0.5, "pauses_mine": 1, "pauses_ref": 0, "twist_pause_diff": -0.3}
