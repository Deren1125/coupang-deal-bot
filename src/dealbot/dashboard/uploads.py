"""대시보드로 받는 파일 (사진·정리본) — POST /dashboard/api/upload, 한 번에 파일 하나, 본문 그대로 (multipart 없음).

- 사진(X-Upload-Kind: photo): image/jpeg·png·webp, 20MB 까지. 받은 뒤 Pillow 로 다시 저장해서 EXIF·GPS·XMP·ICC 를 모두 없앤다
  (방향은 먼저 반영: exif_transpose). JPEG → JPEG 92, PNG → PNG (캡처는 손실 없이), WEBP → JPEG 92.
  5천만 화소 넘는 그림(압축 폭탄)·짧은 변 300px 미만은 받지 않음 (딜 사진 600px 기준은 attach_link 가 그대로 본다)
- 정리본(X-Upload-Kind: ledger): text/markdown·text/plain, 1MB 까지, UTF-8, 50자 이상
- 이름은 서버가 만든 32자 16진수 + 확장자 (files/<id>.<ext>), 썸네일 320px JPEG 80 (thumbs/<id>.jpg). 머리글의 이름은 쓰지 않는다
- 표 dash_uploads 에 기록. 안 쓴 파일은 24시간, 쓴 파일은 그 일이 끝나고 72시간 뒤, tmp/ 는 1시간 뒤 지운다
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
LEDGER_TYPES = {"text/markdown": "md", "text/plain": "txt", "text/x-markdown": "md"}
PHOTO_MAX = 20 * 1024 * 1024
LEDGER_MAX = 1024 * 1024
MAX_PIXELS = 50_000_000
MIN_SIDE = 300
THUMB_SIDE = 320
DEAL_PHOTO_MAX = 10 * 1024 * 1024  # attach_link·attach_late_photo 가 보는 크기
LEDGER_MIN_CHARS = 50
ID_RE = re.compile(r"^[0-9a-f]{32}$")
FILE_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp|md|txt)$")
UNUSED_S = 24 * 3600
USED_S = 72 * 3600
TMP_S = 3600


class UploadError(Exception):
    """받지 않는 파일 — code: bad_upload | too_big | bad_type."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


def max_bytes(kind: str) -> int:
    return PHOTO_MAX if kind == "photo" else LEDGER_MAX if kind == "ledger" else 0


def media_type(headers: dict[str, str]) -> str:
    return (headers.get("content-type") or "").split(";", 1)[0].strip().lower()


class Uploads:
    def __init__(self, db: Any, data_dir: Path | str, *, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.base = Path(data_dir) / "dash_uploads"
        self.clock = clock
        for sub in ("tmp", "files", "thumbs"):
            d = self.base / sub
            d.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(d, 0o700)
        with contextlib.suppress(OSError):
            os.chmod(self.base, 0o700)

    @property
    def files_dir(self) -> Path:
        return self.base / "files"

    def tmp_path(self) -> Path:
        return self.base / "tmp" / f"up_{secrets.token_hex(12)}"

    # ------------------------------------------------------------ 받기
    def process(self, tmp: Path, *, kind: str, ctype: str, session: str) -> dict[str, Any]:
        """tmp 에 받은 본문 → 걸러서 files/ 에. 응답 {upload_id, w, h, bytes, thumb}. 못 받으면 UploadError. tmp 는 늘 지운다."""
        try:
            raw = tmp.read_bytes()
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        if kind == "photo":
            if ctype not in PHOTO_TYPES:
                raise UploadError("bad_type", "사진은 JPG·PNG·WEBP 만 받아요")
            if len(raw) > PHOTO_MAX:
                raise UploadError("too_big", "사진이 너무 커요 (20MB 까지)")
            data, ext, w, h, thumb = self._photo(raw)
        elif kind == "ledger":
            if ctype not in LEDGER_TYPES:
                raise UploadError("bad_type", "정리본은 .md·.txt 만 받아요")
            if len(raw) > LEDGER_MAX:
                raise UploadError("too_big", "정리본이 너무 커요 (1MB 까지)")
            data, ext = self._ledger(raw, LEDGER_TYPES[ctype])
            w = h = None
            thumb = None
        else:
            raise UploadError("bad_type", "받지 않는 종류예요")
        uid = secrets.token_hex(16)
        self._write(self.files_dir / f"{uid}.{ext}", data)
        if thumb is not None:
            self._write(self.base / "thumbs" / f"{uid}.jpg", thumb)
        with self.db._lock:
            self.db._conn.execute(
                "INSERT INTO dash_uploads (id, ts, kind, ext, bytes, w, h, sha256, session) VALUES (?,?,?,?,?,?,?,?,?)",
                (uid, self.clock(), kind, ext, len(data), w, h, hashlib.sha256(data).hexdigest(), session))
        out: dict[str, Any] = {"upload_id": uid, "w": w, "h": h, "bytes": len(data)}
        if thumb is not None:
            out["thumb"] = f"/dashboard/upload/{uid}/thumb"
        return out

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)

    @staticmethod
    def _photo(raw: bytes) -> tuple[bytes, str, int, int, bytes]:
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                fmt = (probe.format or "").upper()
                w, h = probe.size
                if w * h > MAX_PIXELS:
                    raise UploadError("bad_upload", "사진이 너무 커요 (화소 수)")
                probe.verify()
            im = Image.open(io.BytesIO(raw))
            im.load()
        except UploadError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise UploadError("bad_upload", "사진을 읽을 수 없어요") from None
        if fmt not in ("JPEG", "PNG", "WEBP", "MPO"):
            raise UploadError("bad_type", "사진은 JPG·PNG·WEBP 만 받아요")
        im = ImageOps.exif_transpose(im)
        if min(im.size) < MIN_SIDE:
            raise UploadError("bad_upload", f"사진이 너무 작아요 (짧은 변 {MIN_SIDE}px 이상)")
        buf = io.BytesIO()
        if fmt == "PNG":
            keep = im if im.mode in ("RGB", "RGBA", "L", "LA", "P") else im.convert("RGBA")
            # PNG 저장은 im.info 의 icc_profile 을 그대로 다시 넣는다 — 기기·회사 이름이 든 ICC 까지 빼려면 info 를 비움 (투명색만 남김)
            tr = keep.info.get("transparency")
            keep.info.clear()
            keep.save(buf, "PNG", optimize=True, **({"transparency": tr} if tr is not None else {}))  # pnginfo 도 넘기지 않음
            ext = "png"
        else:
            _rgb(im).save(buf, "JPEG", quality=92, optimize=True)  # exif·icc 를 넘기지 않음 → 메타데이터 없음
            ext = "jpg"
        thumb = _rgb(im.copy())
        thumb.thumbnail((THUMB_SIDE, THUMB_SIDE))
        tb = io.BytesIO()
        thumb.save(tb, "JPEG", quality=80)
        return buf.getvalue(), ext, im.size[0], im.size[1], tb.getvalue()

    @staticmethod
    def _ledger(raw: bytes, ext: str) -> tuple[bytes, str]:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UploadError("bad_upload", "UTF-8 글자 파일만 받아요") from None
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if "\x00" in text or len(text.strip()) < LEDGER_MIN_CHARS:
            raise UploadError("bad_upload", f"정리본 내용이 너무 짧아요 ({LEDGER_MIN_CHARS}자 이상)")
        return text.encode("utf-8"), ext

    # ------------------------------------------------------------ 쓰기
    def row(self, uid: str) -> dict[str, Any] | None:
        if not isinstance(uid, str) or not ID_RE.match(uid):
            return None
        r = self.db._one("SELECT * FROM dash_uploads WHERE id = ? AND deleted = 0", (uid,))
        return dict(r) if r else None

    def path(self, uid: str, *, kind: str | None = None, session: str | None = None) -> Path | None:
        """받은 파일 경로 (id 모양·종류·파일 확인 — 바깥에서 경로를 받지 않는다). 다른 세션이 올린 파일은 None."""
        row = self.row(uid)
        if row is None or (kind is not None and row["kind"] != kind) or (session is not None and row["session"] != session):
            return None
        p = self.files_dir / f"{uid}.{row['ext']}"
        try:
            st = os.lstat(p)
        except OSError:
            return None
        if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(self.files_dir.resolve()) or st.st_size <= 0:
            return None
        return p

    def thumb_path(self, uid: str) -> Path | None:
        if not isinstance(uid, str) or not ID_RE.match(uid) or self.row(uid) is None:
            return None
        p = self.base / "thumbs" / f"{uid}.jpg"
        return p if p.is_file() and not p.is_symlink() else None

    def deal_jpeg(self, uid: str, *, session: str | None = None) -> bytes | None:
        """딜 사진으로 넘길 JPEG (10MB 이하 — 크거나 PNG 면 JPEG 88 로 다시)."""
        p = self.path(uid, kind="photo", session=session)
        if p is None:
            return None
        data = p.read_bytes()
        if p.suffix == ".jpg" and len(data) <= DEAL_PHOTO_MAX:
            return data
        with Image.open(io.BytesIO(data)) as im:
            out = _rgb(im)
            for side in (None, 3000, 2400):
                if side is not None:
                    out.thumbnail((side, side))
                buf = io.BytesIO()
                out.save(buf, "JPEG", quality=88, optimize=True)
                if buf.tell() <= DEAL_PHOTO_MAX:
                    return buf.getvalue()
        return None

    def mark_used(self, ids: list[str], action_id: str) -> None:
        with self.db._lock:
            for uid in ids:
                self.db._conn.execute("UPDATE dash_uploads SET used_by = ? WHERE id = ?", (action_id, uid))

    # ------------------------------------------------------------ 정리
    def gc(self) -> int:
        """안 쓴 파일 24시간 · 쓴 파일은 그 일이 끝나고 72시간 · tmp 1시간."""
        now = self.clock()
        gone = 0
        rows = self.db._q("SELECT u.id, u.ext, u.ts, u.used_by, a.status, a.updated_at FROM dash_uploads u "
                          "LEFT JOIN dash_actions a ON a.id = u.used_by WHERE u.deleted = 0")
        for r in rows:
            if r["used_by"] is None:
                old = now - r["ts"] > UNUSED_S
            else:
                old = (r["status"] is None and now - r["ts"] > USED_S) or (
                    r["status"] in ("ok", "error", "rejected", "expired", "unknown") and now - (r["updated_at"] or r["ts"]) > USED_S)
            if not old:
                continue
            for p in (self.files_dir / f"{r['id']}.{r['ext']}", self.base / "thumbs" / f"{r['id']}.jpg"):
                with contextlib.suppress(OSError):
                    p.unlink()
            with self.db._lock:
                self.db._conn.execute("UPDATE dash_uploads SET deleted = 1 WHERE id = ?", (r["id"],))
            gone += 1
        for p in (self.base / "tmp").glob("*"):
            try:
                if now - p.stat().st_mtime > TMP_S:
                    p.unlink()
                    gone += 1
            except OSError:
                pass
        return gone


def _rgb(im: Image.Image) -> Image.Image:
    """JPEG 로 저장할 수 있게 RGB (투명은 흰 바탕)."""
    if im.mode == "RGB":
        return im
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return im.convert("RGB")
