"""취준 발음 채점 준비 — 모델·vocab·CMUdict 를 <data>/models/ 에 받기 (한 번 · 손으로 · nice 19 · 이어 받기).

    nice -n 19 python tools/jobprep_setup.py [--data ~/jobprep-data] [--small]

받는 것 (모두 공개 파일 · 로그인 없음):
  w2v_espeak_int8.onnx  onnx-community/wav2vec2-lv-60-espeak-cv-ft-ONNX 의 onnx/model_int8.onnx (약 318MB · Apache-2.0)
  vocab.json            같은 저장소의 vocab.json (음소 IPA → 번호)
  cmudict.dict          cmusphinx/cmudict (약 3.5MB · BSD)
--small 이면 vocab.json·cmudict.dict 만 (모델은 건너뜀). 받은 파일의 sha256 은 models/SHA256SUMS 에 적는다.
pip 의존성(onnxruntime·numpy)은 pyproject 에 있음 — 서버 auto-update 가 pip install -e . 을 한다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

HF = "https://huggingface.co/onnx-community/wav2vec2-lv-60-espeak-cv-ft-ONNX/resolve/main/"
FILES = [("vocab.json", HF + "vocab.json", False), ("cmudict.dict", "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict", False),
         ("w2v_espeak_int8.onnx", HF + "onnx/model_int8.onnx", True)]
UA = "jobprep-setup/1"
CHUNK = 1 << 20


def fetch(url: str, dst: Path) -> None:
    part = dst.with_suffix(dst.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Range": f"bytes={have}-"} if have else {})})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 — 정해진 공개 주소만
        mode = "ab" if have and r.status == 206 else "wb"
        with open(part, mode) as f:
            while True:
                b = r.read(CHUNK)
                if not b:
                    break
                f.write(b)
    os.chmod(part, 0o600)
    part.replace(dst)


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("JOBPREP_DATA_DIR") or "~/jobprep-data")
    ap.add_argument("--small", action="store_true")
    a = ap.parse_args()
    try:
        os.nice(19)
    except OSError:
        pass
    d = Path(os.path.expanduser(a.data)) / "models"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    sums = []
    for name, url, big in FILES:
        p = d / name
        if big and a.small:
            print(f"건너뜀: {name} (--small)")
            continue
        if p.exists() and p.stat().st_size > 0:
            print(f"있음: {name}")
        else:
            print(f"받는 중: {name}")
            try:
                fetch(url, p)
            except Exception as e:  # noqa: BLE001
                print(f"  실패 ({type(e).__name__}) — 다시 돌리면 이어 받음")
                return 1
        sums.append(f"{sha(p)}  {name}")
    (d / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    os.chmod(d / "SHA256SUMS", 0o600)
    print("끝")
    return 0


if __name__ == "__main__":
    sys.exit(main())
