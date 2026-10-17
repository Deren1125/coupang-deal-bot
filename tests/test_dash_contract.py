"""두 저장소가 같이 쓰는 모양 (tests/fixtures/dash_contract — Blog-Auto 와 같은 파일). 딜봇 쪽이 쓰고·읽는 것이 맞는지 본다."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

from dash_helpers import fixture
from dealbot.bridge import spool, status
from dealbot.bridge.actions_out import ActionsOut
from dealbot.bridge.kakao_out import KakaoOut, fit
from dealbot.dashboard.blogauto import parse_export
from dealbot.notify import iid as iidmod
from dealbot.storage.db import Database


def test_iid_map_matches_the_fixture() -> None:
    for key, blog, want in fixture("iid_map.json"):
        assert iidmod.iid_for(key, blog, src="dealbot" if want != "sys-blogauto" else "blogauto") == want, key
    assert iidmod.iid_for("hot:x", "issue", 31) == "post-issue-31"
    assert iidmod.digest_iid(__import__("datetime").date(2026, 10, 18), "pm") == "digest-20261018-pm"
    assert iidmod.ok("deal-12") and not iidmod.ok("../x") and not iidmod.ok("a" * 65)


def test_spool_write_is_atomic_0600_and_names_are_checked(tmp_path: Path) -> None:
    r = spool.root(tmp_path, spool.DASH_ACTIONS)
    assert stat.S_IMODE(os.stat(r / "new").st_mode) == 0o700
    aid = spool.new_id("a")
    assert spool.NAME_RE.match(aid) and aid < spool.new_id("a", time.time() + 1)  # 이름순 = 시간순
    p = spool.write(r, aid, {"v": 1})
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and list((r / "tmp").iterdir()) == []
    assert spool.claim(r, aid) is not None and spool.claim(r, aid) is None  # 한쪽만 이긴다
    try:
        spool.write(r, "../x", {})
        raise AssertionError("bad name accepted")
    except ValueError:
        pass


def test_action_file_has_the_contract_shape(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    st = status.BlogAutoStatus(at=time.time(), bridge=1)
    out = ActionsOut(db, tmp_path, status=lambda: st)
    aid, existed = out.submit(action="publish", blog="issue", args={"blog": "issue", "pid": 31}, target="post-issue-31",
                              confirmed=True, session="3fa9c2d1", cid="c_8b1f2e")
    assert not existed
    written = spool.read(out.root() / "new" / f"{aid}.json")
    want = fixture("action_publish.json")
    assert set(written) == set(want)  # type: ignore[arg-type]
    assert {k: written[k] for k in ("v", "action", "blog", "args", "target", "confirmed")} == {  # type: ignore[index]
        k: want[k] for k in ("v", "action", "blog", "args", "target", "confirmed")}
    assert written["origin"] == {"via": "dashboard", "session": "3fa9c2d1", "cid": "c_8b1f2e"}  # type: ignore[index]
    assert written["expires_at"] - written["ts"] == 600  # type: ignore[index]
    again, existed = out.submit(action="publish", blog="issue", args={"blog": "issue", "pid": 31}, target="post-issue-31",
                                confirmed=True, session="other", cid="c_zzzzzz")
    assert existed and again == aid  # 같은 일이 아직 안 끝났으면 그 id
    db.close()


def test_result_fixtures_are_ingested(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    st = status.BlogAutoStatus(at=time.time(), bridge=1)
    out = ActionsOut(db, tmp_path, status=lambda: st)
    for name, action in (("result_ok.json", "publish"), ("result_rejected.json", "fix"), ("result_running.json", "photos"),
                         ("result_kakao_auth_url.json", "kakao_auth_url")):
        res = fixture(name)
        out.store.insert(aid=res["id"], ts=time.time(), blog="issue", action=f"blog.{action}", target="", args={}, status="queued",
                         cid=None, session="s")
        spool.write(out.root(), res["id"], res, sub="results")
    out.ingest()
    ok = out.store.get(fixture("result_ok.json")["id"])
    assert ok["status"] == "ok" and json.loads(ok["result"])["code"] == "published"  # type: ignore[index]
    rej = out.store.get(fixture("result_rejected.json")["id"])
    assert rej["status"] == "rejected" and json.loads(rej["result"])["code"] == "owner_edited"  # type: ignore[index]
    run = out.store.get(fixture("result_running.json")["id"])
    assert run["status"] == "running"  # type: ignore[index]
    names = spool.names(out.root(), "results")
    assert names == [fixture("result_running.json")["id"]]  # 끝난 결과 파일은 지움, running 은 남김
    k = out.store.get(fixture("result_kakao_auth_url.json")["id"])
    assert "kauth" not in (k["result"] or "") and "https://" not in (k["result"] or "")  # type: ignore[index]  # 주소는 DB 에 없음
    url = out.take_url(k["id"])  # type: ignore[index]
    assert url and url.startswith("https://kauth.kakao.com/") and out.take_url(k["id"]) is None  # type: ignore[index]
    db.close()


def test_kakao_out_shapes_match_the_fixtures(tmp_path: Path) -> None:
    k = KakaoOut(tmp_path, bridge_ok=lambda: True)
    want = fixture("kakao_out_urgent.json")
    assert k.send("urgent", key=want["key"], text=want["text"], link=want["link"])
    k.resolve(["link:123"])
    r = spool.root(tmp_path, spool.KAKAO_OUT)
    files = [spool.read(r / "new" / f"{n}.json") for n in spool.names(r, "new")]
    assert set(files[0]) == set(want) and {x: files[0][x] for x in ("op", "kind", "key", "text", "link", "button")} == {  # type: ignore[index]
        x: want[x] for x in ("op", "kind", "key", "text", "link", "button")}
    assert set(files[1]) == set(fixture("kakao_out_resolve.json")) and files[1]["keys"] == ["link:123"]  # type: ignore[index]
    assert set(fixture("kakao_out_daily.json")) == set(files[0])


def test_kakao_text_fits_at_a_sentence_end() -> None:
    text = "첫 문장이에요. " * 30
    got = fit(text)
    assert len(got) <= 200 and got.endswith("…") and "요." in got


def test_export_v2_fixture_parses_with_ids_posts_and_digest() -> None:
    e = parse_export(fixture("export_v2.json"), "Asia/Seoul")
    assert e.version == 2 and e.ok
    assert e.waiting.blogs["issue"].top[0].id == 46  # type: ignore[union-attr]
    p = e.v2["posts"]["issue"][0]
    assert p["pid"] == 46 and p["needs"] == ["photos"] and p["photo_request"]["must"] == ["정면 전신 1장", "신발 클로즈업 1장"]
    assert e.v2["posts"]["finance"][0]["questions"][0]["q"] == "언제 받으셨나요?"
    assert e.v2["recent"]["issue"][0]["url"].startswith("https://m.blog.naver.com/")
    assert e.v2["scan"]["sid"] == "261018084012" and e.v2["scan"]["items"][0]["n"] == 1
    assert e.v2["status"]["blogs"]["issue"]["login"] == "ok" and e.v2["status"]["kakao"]["state"] == "ok"
    assert e.v2["digest"]["waiting"] and e.v2["digest"]["blogs"]["issue"]


def test_export_v1_still_parses_and_v2_drops_bad_rows() -> None:
    v1 = {"written_at": "2026-10-18T09:00:00+09:00", "blogs": {"issue": {"posts_published_today": 1}}}
    e = parse_export(v1, "Asia/Seoul")
    assert e.ok and e.version == 1 and e.v2 == {}
    bad = fixture("export_v2.json")
    bad["posts"]["issue"].append({"pid": "x", "title": "no"})
    bad["posts"]["issue"].append({"pid": 7, "title": "t", "naver_url": "https://evil.example.com/x", "needs": ["publish", "rm -rf"],
                                  "slots": [{"slot": 1, "thumb": "../../etc/passwd"}]})
    e = parse_export(bad, "Asia/Seoul")
    p7 = [p for p in e.v2["posts"]["issue"] if p["pid"] == 7][0]
    assert p7["naver_url"] is None and p7["needs"] == ["publish"] and p7["slots"][0]["thumb"] is None
    assert len(e.v2["posts"]["issue"]) == 2


def test_reader_size_cap_is_4mb(tmp_path: Path) -> None:
    from dealbot.dashboard.blogauto import MAX_BYTES, BlogAutoReader

    assert MAX_BYTES == 4_000_000
    p = tmp_path / "b.json"
    p.write_text(json.dumps(fixture("export_v2.json")) + " " * 2_000_000)
    from datetime import UTC, datetime

    r = BlogAutoReader(p, tz="Asia/Seoul", stale_hours=10**6, now=lambda: datetime(2026, 10, 18, 1, tzinfo=UTC))
    assert r.read().version == 2


def test_status_files(tmp_path: Path) -> None:
    (tmp_path / "blogauto_status.json").write_text(json.dumps(dict(fixture("status_blogauto.json"), at=time.time())))
    st = status.read_blogauto(tmp_path)
    assert st.bridge_ok() and st.supports("publish") and st.kakao_state == "ok" and st.route == "push"
    (tmp_path / "blogauto_status.json").write_text(json.dumps(dict(fixture("status_blogauto.json"), at=time.time() - 3600)))
    assert not status.read_blogauto(tmp_path).bridge_ok()
    status.write_dashboard(tmp_path, push_devices=2, push_ok_at=1760771900.0, now=1760772000.0)
    got = json.loads((tmp_path / "dashboard_status.json").read_text())
    assert set(got) == set(fixture("status_dashboard.json")) and got["push_router"] == 1 and got["actions_v"] == 1
    assert stat.S_IMODE(os.stat(tmp_path / "dashboard_status.json").st_mode) == 0o600


def test_caddy_compresses_the_dashboard() -> None:
    text = (Path(__file__).resolve().parents[1] / "deploy" / "lightsail" / "https.sh").read_text(encoding="utf-8")
    assert "encode @dash zstd gzip" in text and "@dash path /dashboard /dashboard/*" in text


def test_ids_written_in_the_same_millisecond_keep_their_order() -> None:
    now = 1_760_772_000.0
    ids = [spool.new_id("k", now) for _ in range(50)]
    assert ids == sorted(ids) and len(set(ids)) == 50
