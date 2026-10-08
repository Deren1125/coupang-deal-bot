"""deploy/lightsail 스크립트 — 가짜 GitHub 원격·pip·systemctl·텔레그램으로 서버를 흉내 내서 확인한다.

서버(클론)에서 auto-update.sh 를 그대로 돌리고, 가짜 systemctl 이 '봇 시작'을 흉내 낸다:
재시작하면 그 순간의 HEAD 를 var/running_version 에 적는다 (진짜 봇이 시작할 때 하는 일).
커밋에 CRASH 파일이 있으면 시작하자마자 죽는 코드, BROKEN 파일이 있으면 점검(selftest)에 떨어지는 코드.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LIGHTSAIL = ROOT / "deploy" / "lightsail"

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")), reason="git·bash 필요")

FAKE_BIN = {
    # sudo: tee 로 /etc 에 쓰는 건 $SIM/etc 로 돌리고, 나머지는 기록만 (systemctl 은 가짜로 넘김)
    "sudo": r"""#!/usr/bin/env bash
echo "sudo $*" >> "$SIM/calls.log"
case "$1" in
  tee) shift; [ "${1:-}" = "-a" ] && { shift; cat >> "$SIM/etc/$(basename "$1")"; exit 0; }; cat > "$SIM/etc/$(basename "$1")"; exit 0 ;;
  systemctl) shift; exec systemctl "$@" ;;
  cp) shift; exec cp "$@" ;;
esac
exit 0
""",
    "sleep": "#!/usr/bin/env bash\nexit 0\n",
    "caddy": '#!/usr/bin/env bash\necho "caddy $*" >> "$SIM/calls.log"\nexit 0\n',
    # 텔레그램 알림은 보낸 문구만 기록, 공인 IP·health 확인은 가짜 응답
    "curl": r"""#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in
    text=*) printf '%s\n----\n' "${a#text=}" >> "$SIM/notify.log" ;;
    https://checkip.amazonaws.com) echo "${FAKE_IP:-3.35.1.2}"; exit 0 ;;
    */health) printf '200'; exit 0 ;;
  esac
done
exit 0
""",
    "systemctl": r"""#!/usr/bin/env bash
echo "systemctl $*" >> "$SIM/calls.log"
case "$1" in
  restart)
    [ "${2:-}" = dealbot ] || exit 0
    if [ -f "$SIM/restart_fail_once" ]; then rm -f "$SIM/restart_fail_once"; exit 1; fi
    echo "RESTART $(git rev-parse --short HEAD)" >> "$SIM/restarts.log"
    if [ -f CRASH ]; then echo crashed > "$SIM/state"
    else echo active > "$SIM/state"; mkdir -p var; git rev-parse --short HEAD > var/running_version; fi
    ;;
  is-active)
    s="$(cat "$SIM/state" 2>/dev/null)"
    case " $* " in *" --quiet "*) ;; *) echo "${s:-inactive}" ;; esac
    [ "$s" = active ] ;;
  show) if [ "$(cat "$SIM/state" 2>/dev/null)" = crashed ]; then echo 3; else echo 0; fi ;;
esac
""",
}

FAKE_VENV = {
    "pip": r"""#!/usr/bin/env bash
echo "pip $*" >> "$SIM/calls.log"
if [ -f "$SIM/pip_fail_once" ]; then rm -f "$SIM/pip_fail_once"; echo "pip: network error" >&2; exit 1; fi
exit 0
""",
    # import 만 하는 옛 점검은 설정·템플릿 오류를 못 잡는다 (진짜와 같게: import 는 항상 성공)
    "python": r"""#!/usr/bin/env bash
echo "python $*" >> "$SIM/calls.log"
case "$*" in
  *selftest.py*) if [ -f BROKEN ]; then echo "deal_threads.j2: UndefinedError: 'dict object' has no attribute 'short_name'" >&2; exit 1; fi ;;
esac
exit 0
""",
}


def _write_exec(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


class Server:
    """가짜 GitHub(bare 저장소) + 서버 클론 + 가짜 명령들."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.sim = tmp / "sim"
        (self.sim / "etc").mkdir(parents=True)
        self.origin = tmp / "origin.git"
        self.dev = tmp / "dev"  # 개발자 PC (여기서 push)
        self.srv = tmp / "server"
        self.env = {
            **os.environ,
            "SIM": str(self.sim),
            "PATH": f"{self.sim / 'bin'}:{os.environ['PATH']}",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        (tmp / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n", encoding="utf-8")
        for name, body in FAKE_BIN.items():
            _write_exec(self.sim / "bin" / name, body)

        self.git("init", "-q", "--bare", str(self.origin), cwd=tmp)
        self.git("clone", "-q", str(self.origin), str(self.dev), cwd=tmp)
        (self.dev / "deploy" / "lightsail").mkdir(parents=True)
        for f in LIGHTSAIL.iterdir():
            if f.is_file():
                shutil.copy2(f, self.dev / "deploy" / "lightsail" / f.name)
        (self.dev / ".gitignore").write_text(".env\n.venv/\nvar/\n", encoding="utf-8")
        (self.dev / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["a"]\n', encoding="utf-8")
        (self.dev / "config.yaml").write_text("app:\n  name: x\n", encoding="utf-8")
        (self.dev / "version.txt").write_text("v1\n", encoding="utf-8")
        self.commit("v1")

        self.git("clone", "-q", str(self.origin), str(self.srv), cwd=tmp)
        for name, body in FAKE_VENV.items():
            _write_exec(self.srv / ".venv" / "bin" / name, body)
        (self.srv / ".env").write_text("TELEGRAM_BOT_TOKEN=fake\nTELEGRAM_ADMIN_CHAT_ID=1\n", encoding="utf-8")
        # 서버에서 v1 이 돌고 있음 (봇이 시작할 때 적은 짧은 커밋)
        (self.srv / "var").mkdir()
        (self.srv / "var" / "running_version").write_text(self.git("rev-parse", "--short", "HEAD") + "\n", encoding="utf-8")
        (self.sim / "state").write_text("active", encoding="utf-8")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        r = subprocess.run(["git", *args], cwd=cwd or self.srv, env=self.env, capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def commit(self, msg: str, files: dict[str, str | None] | None = None) -> str:
        for name, content in (files or {}).items():
            p = self.dev / name
            if content is None:
                p.unlink()
            else:
                p.write_text(content, encoding="utf-8")
        (self.dev / "version.txt").write_text(msg + "\n", encoding="utf-8")
        self.git("add", "-A", cwd=self.dev)
        self.git("commit", "-q", "-m", msg, cwd=self.dev)
        self.git("push", "-q", "origin", "HEAD:main", cwd=self.dev)
        return self.git("rev-parse", "HEAD", cwd=self.dev)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def remote(self) -> str:
        return self.git("rev-parse", "origin/main", cwd=self.dev)

    def running(self) -> str:
        p = self.srv / "var" / "running_version"
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def runs(self, sha: str) -> bool:
        """var/running_version(짧은 커밋)이 이 커밋인지."""
        r = self.running()
        return bool(r) and sha.startswith(r)

    def run(self, *args: str, script: str = "auto-update.sh", stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.srv / "deploy" / "lightsail" / script), *args],
            cwd=self.tmp, env=self.env, capture_output=True, text=True, timeout=60, input=stdin,
        )

    def log(self, name: str) -> str:
        p = self.sim / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def messages(self) -> list[str]:
        return [x.strip() for x in self.log("notify.log").split("\n----\n") if x.strip()]

    def notices(self) -> list[str]:
        """실패 알림 (앞머리 뗀 본문)."""
        head = "⚠️ 핫딜 봇 자동 업데이트: "
        return [m[len(head):] for m in self.messages() if m.startswith(head)]

    def restarts(self) -> int:
        return self.log("restarts.log").count("RESTART")

    def age_markers(self, minutes: int) -> None:
        """실패 표식을 N분 전 것으로 (다시 시도까지 기다리는 시간 흉내)."""
        for p in (self.srv / "var").glob("update_*"):
            t = p.stat().st_mtime - minutes * 60
            os.utime(p, (t, t))


@pytest.fixture
def server(tmp_path: Path) -> Server:
    return Server(tmp_path)


# ---- auto-update.sh


def test_new_commit_is_checked_and_restarted(server: Server) -> None:
    v2 = server.commit("v2")
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.head() == v2 and server.runs(v2)
    assert "selftest.py" in server.log("calls.log")  # 재시작 전에 설정·템플릿 점검
    assert server.notices() == []  # 성공은 조용히 (봇 시작 알림에 코드 버전이 나옴)
    # 이미 돌고 있는 버전이면 아무것도 안 함
    r = server.run()
    assert r.returncode == 0 and server.restarts() == 1


def test_transient_pip_failure_is_retried_until_the_new_code_runs(server: Server) -> None:
    """설치가 한 번 실패해도 같은 커밋을 다시 시도해서 결국 새 코드로 재시작한다 (예전엔 다음 push 까지 옛 코드)."""
    v1 = server.head()
    server.commit("deps", {"pyproject.toml": '[project]\nname = "x"\ndependencies = ["a", "b"]\n'})
    (server.sim / "pip_fail_once").touch()
    r1 = server.run()
    assert r1.returncode != 0
    assert len(server.notices()) == 1 and "패키지 설치" in server.notices()[0]
    assert server.head() == v1 and server.runs(v1)  # 실패하면 받기 전 코드로 되돌림 (돌고 있는 봇과 디스크가 같게)
    # 바로 다음 1분 타이머는 쉬고(10분 간격으로 다시 시도), 10분 뒤엔 다시 시도해서 성공
    server.run()
    assert server.restarts() == 0
    server.age_markers(11)
    r3 = server.run()
    assert r3.returncode == 0, r3.stdout + r3.stderr
    assert server.head() == server.remote() and server.runs(server.remote())
    assert len(server.notices()) == 1  # 같은 실패 알림은 한 번만
    assert server.messages()[-1].startswith("✅") and server.remote()[:7] in server.messages()[-1]  # 해결됐다고 한 번
    server.run()
    assert len(server.messages()) == 2


def test_pulled_but_not_restarted_is_picked_up(server: Server) -> None:
    """서버에서 손으로 git pull 만 하고 재시작을 안 했어도 다음 실행 때 재시작한다."""
    v2 = server.commit("v2")
    server.git("pull", "-q", "--ff-only", "origin", "main")
    assert server.head() == v2 and not server.runs(v2)
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.runs(v2)


def test_missing_running_version_restarts_once(server: Server) -> None:
    """이 기능 이전 버전의 봇(running_version 을 안 씀)이 돌고 있으면 한 번 재시작해서 기록하게 한다."""
    (server.srv / "var" / "running_version").unlink()
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.runs(server.head())
    server.run()
    assert server.restarts() == 1


def test_unknown_running_version_restarts_only_once(server: Server) -> None:
    """봇이 자기 커밋을 못 읽으면('?') 재시작은 한 번만 (1분마다 재시작하지 않게)."""
    (server.srv / "var" / "running_version").write_text("?\n", encoding="utf-8")
    systemctl = server.sim / "bin" / "systemctl"
    systemctl.write_text(systemctl.read_text(encoding="utf-8").replace("git rev-parse --short HEAD > var", "echo '?' > var"), encoding="utf-8")
    assert server.run().returncode == 0
    assert server.run().returncode == 0
    assert server.restarts() == 1


def test_selftest_failure_rolls_back_and_keeps_old_bot(server: Server) -> None:
    v1 = server.head()
    server.commit("bad", {"BROKEN": "x"})
    r = server.run()
    assert r.returncode != 0
    assert server.head() == v1 and server.runs(v1)  # 디스크도 옛 코드로, 봇은 재시작 안 함
    assert server.restarts() == 0
    msgs = server.notices()
    assert len(msgs) == 1 and "점검" in msgs[0] and "short_name" in msgs[0]
    # 같은 커밋은 다시 받지 않음 (10분이 지나도) → 새 커밋이 오면 다시 시도
    server.age_markers(30)
    server.run()
    assert server.head() == v1 and server.log("calls.log").count("selftest.py") == 1
    v3 = server.commit("fixed", {"BROKEN": None})
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.runs(v3) and len(server.notices()) == 1


def test_crash_after_restart_rolls_back_to_the_running_version(server: Server) -> None:
    v1 = server.head()
    server.commit("crash", {"CRASH": "x"})
    r = server.run()
    assert r.returncode != 0
    assert server.head() == v1 and server.runs(v1)  # 옛 버전으로 다시 켬
    assert server.restarts() == 2
    msgs = server.notices()
    assert len(msgs) == 1 and "바로 멈췄어요" in msgs[0] and f"이전 버전({v1[:7]})으로 되돌려 켰어요" in msgs[0]


def test_crash_with_nothing_to_roll_back_to_says_so(server: Server) -> None:
    """돌고 있던 커밋이 디스크와 같으면(재시작만 필요했던 경우) '되돌렸다'고 하지 않는다."""
    (server.srv / "var" / "running_version").unlink()
    (server.srv / "CRASH").write_text("x", encoding="utf-8")  # 로컬에서만 생긴 문제
    assert server.run().returncode != 0
    msgs = server.notices()
    assert len(msgs) == 1 and "바로 멈췄어요" in msgs[0] and "되돌려" not in msgs[0]


def test_stopped_bot_is_not_started_without_a_new_commit(server: Server) -> None:
    """처음 설치 중(키 넣기 전)이거나 일부러 꺼 둔 봇은 버전 기록이 없다고 켜지 않는다."""
    (server.srv / "var" / "running_version").unlink()
    (server.sim / "state").write_text("inactive", encoding="utf-8")
    assert server.run().returncode == 0
    assert server.restarts() == 0


def test_restart_hiccup_is_retried(server: Server) -> None:
    server.commit("v2")
    (server.sim / "restart_fail_once").touch()
    assert server.run().returncode != 0
    assert len(server.notices()) == 1
    server.age_markers(11)
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.runs(server.remote())


def test_manual_run_retries_right_away(server: Server) -> None:
    """'지금 한 번' 실행은 10분 기다림을 건너뛰고 바로 다시 시도한다."""
    server.commit("v2")
    (server.sim / "restart_fail_once").touch()
    assert server.run().returncode != 0
    r = server.run("now")
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.runs(server.remote())


def test_pip_runs_only_when_pyproject_changes(server: Server) -> None:
    server.commit("v2")  # 템플릿·코드만 바뀜
    assert server.run().returncode == 0
    first = server.log("calls.log").count("pip install")
    server.commit("v3", {"pyproject.toml": '[project]\nname = "x"\ndependencies = ["a", "c"]\n'})
    assert server.run().returncode == 0
    server.commit("v4")
    assert server.run().returncode == 0
    # 처음 한 번(설치 기록이 없을 때) + 의존성이 바뀐 v3 에서만
    assert first <= 1
    assert server.log("calls.log").count("pip install") == first + 1
    assert server.runs(server.remote())


def test_publishing_in_progress_defers_the_update(server: Server) -> None:
    v1 = server.head()
    server.commit("v2")
    busy = server.srv / "var" / "busy"
    busy.mkdir(parents=True)
    (busy / "publish_1").write_text("publish", encoding="utf-8")
    r = server.run()
    assert r.returncode == 0 and server.head() == v1 and server.restarts() == 0  # 파일도 안 바꿈
    (busy / "publish_1").unlink()
    assert server.run().returncode == 0
    assert server.runs(server.remote())
    assert not (server.srv / "var" / "updating").exists()  # 발행 보류 표식은 끝나면 지움


def test_stale_busy_marker_does_not_block_forever(server: Server) -> None:
    server.commit("v2")
    busy = server.srv / "var" / "busy"
    busy.mkdir(parents=True)
    mark = busy / "publish_1"
    mark.write_text("publish", encoding="utf-8")
    old = mark.stat().st_mtime - 20 * 60  # 강제 종료로 남은 20분 전 표식
    os.utime(mark, (old, old))
    assert server.run().returncode == 0
    assert server.runs(server.remote())


def test_local_edit_blocks_pull_with_a_clear_message(server: Server) -> None:
    (server.srv / "config.yaml").write_text("app:\n  name: mine\n", encoding="utf-8")
    server.commit("cfg", {"config.yaml": "app:\n  name: upstream\n"})
    r = server.run()
    assert r.returncode != 0
    msgs = server.notices()
    assert len(msgs) == 1 and "config.yaml" in msgs[0] and "git checkout -- config.yaml" in msgs[0]
    assert (server.srv / "config.yaml").read_text(encoding="utf-8") == "app:\n  name: mine\n"  # 서버에서 고친 내용은 그대로
    # 고친 내용을 정리하면 (새 push 없이도) 다음 시도에 받아진다
    server.git("checkout", "--", "config.yaml")
    server.age_markers(11)
    r = server.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.runs(server.remote()) and len(server.notices()) == 1


def test_untracked_file_in_the_way_is_named(server: Server) -> None:
    (server.srv / "notes.txt").write_text("mine", encoding="utf-8")
    server.commit("notes", {"notes.txt": "upstream"})
    assert server.run().returncode != 0
    msgs = server.notices()
    assert len(msgs) == 1 and "notes.txt" in msgs[0] and "rm notes.txt" in msgs[0]


def test_diverged_server_gets_a_readable_reason(server: Server) -> None:
    (server.srv / "local.txt").write_text("x", encoding="utf-8")
    server.git("add", "local.txt")
    server.git("commit", "-q", "-m", "server-only commit")
    server.commit("v2")
    assert server.run().returncode != 0
    msgs = server.notices()
    assert len(msgs) == 1 and "서버에만 있는 커밋" in msgs[0] and "hint" not in msgs[0]


# ---- selftest.py (auto-update.sh 가 재시작 전에 돌리는 진짜 점검)


def _selftest(cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LIGHTSAIL / "selftest.py")], cwd=cwd, capture_output=True, text=True, timeout=120,
        env={**os.environ, **env},
    )


def _copy_config(tmp: Path) -> Path:
    shutil.copy2(ROOT / "config.yaml", tmp / "config.yaml")
    shutil.copytree(ROOT / "templates", tmp / "templates")
    return tmp


def test_selftest_passes_on_this_repo() -> None:
    """점검이 멀쩡한 코드를 막으면 모든 배포가 멈춘다 → 이 저장소 그대로는 통과해야 한다."""
    r = _selftest(ROOT)
    assert r.returncode == 0, r.stderr
    assert "점검 통과" in r.stdout


def test_selftest_catches_a_template_that_does_not_match_the_code(tmp_path: Path) -> None:
    t = _copy_config(tmp_path) / "templates" / "deal_threads.j2"
    t.write_text("{{ facts.no_such_field }}\n" + t.read_text(encoding="utf-8"), encoding="utf-8")
    r = _selftest(tmp_path)
    assert r.returncode == 1
    last = r.stderr.strip().splitlines()[-1]
    assert last.startswith("deal_threads.j2: UndefinedError") and "no_such_field" in last


def test_selftest_catches_a_broken_config_without_printing_values(tmp_path: Path) -> None:
    cfg = _copy_config(tmp_path) / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\napp:\n  scheduler_tick_seconds: notanint\n", encoding="utf-8")
    r = _selftest(tmp_path, TELEGRAM_ADMIN_CHAT_ID="private-value-123")
    assert r.returncode == 1
    assert r.stderr.strip().splitlines()[-1].startswith("설정 오류 — app.scheduler_tick_seconds")
    assert "private-value-123" not in r.stdout + r.stderr and "notanint" not in r.stderr


# ---- setup.sh


def test_setup_rerun_pulls_the_latest_code_and_restarts_on_it(server: Server) -> None:
    """'다시 실행하면 코드도 갱신'이 주석에만 있던 문제: 실제로 최신 코드를 받고 그 코드로 재시작한다."""
    v2 = server.commit("v2")
    (server.srv / "var" / "update_failed_1234567").touch()  # 자동 업데이트가 막혀 있던 흔적
    (server.srv / ".env").write_text(
        "".join(f"{k}=x\n" for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID", "COUPANG_ACCESS_KEY", "COUPANG_SECRET_KEY")),
        encoding="utf-8",
    )
    server.env["HOME"] = str(server.tmp / "home")
    r = server.run(script="setup.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.head() == v2 and server.runs(v2)
    assert v2[:7] in r.stdout  # 지금 코드 버전을 보여 줌
    assert not list((server.srv / "var").glob("update_failed_*"))
    assert (server.srv / "var" / "installed_pyproject").read_text(encoding="utf-8").strip()  # 자동 업데이트가 pip 를 또 돌리지 않게
    unit = (server.sim / "etc" / "dealbot.service").read_text(encoding="utf-8")
    assert "KillMode=mixed" in unit and "TimeoutStopSec=90" in unit  # 종료 때 하던 발행을 마칠 시간


def test_setup_continues_when_the_pull_is_blocked(server: Server) -> None:
    (server.srv / "config.yaml").write_text("app:\n  name: mine\n", encoding="utf-8")
    server.commit("cfg", {"config.yaml": "app:\n  name: upstream\n"})
    server.env["HOME"] = str(server.tmp / "home")
    r = server.run(script="setup.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "최신 코드를 못 받았어요" in r.stdout
    assert (server.srv / "config.yaml").read_text(encoding="utf-8") == "app:\n  name: mine\n"


# ---- https.sh

STOCK_CADDYFILE = """# The Caddyfile is an easy way to configure your Caddy web server.
:80 {
\t# Set this path to your site's directory.
\troot * /usr/share/caddy

\t# Enable the static file server.
\tfile_server

\t# Another common task is to set up a reverse proxy:
\t# reverse_proxy localhost:8080
}
"""


def _https(server: Server, env_lines: str = "", caddyfile: str | None = None, ip: str = "3.35.1.2") -> subprocess.CompletedProcess[str]:
    etc = server.sim / "etc"
    if caddyfile is not None:
        (etc / "Caddyfile").write_text(caddyfile, encoding="utf-8")
    (server.srv / ".env").write_text("TELEGRAM_BOT_TOKEN=fake\n" + env_lines, encoding="utf-8")
    server.env["CADDY_DIR"] = str(etc)
    server.env["FAKE_IP"] = ip
    return server.run(script="https.sh")


def test_https_drops_redirect_overrides_for_another_host(server: Server) -> None:
    """예전 Railway·예시 값의 THREADS_REDIRECT_URI 가 남아 있으면 sslip 주소를 등록해도 봇은 옛 주소를 보낸다 → 지운다."""
    r = _https(server, "THREADS_REDIRECT_URI=https://localhost/callback\n"
               "INSTAGRAM_REDIRECT_URI=https://old.up.railway.app/instagram/callback\n")
    assert r.returncode == 0, r.stdout + r.stderr
    env = (server.srv / ".env").read_text(encoding="utf-8")
    assert "PUBLIC_DOMAIN=3-35-1-2.sslip.io\n" in env
    assert "THREADS_REDIRECT_URI" not in env and "INSTAGRAM_REDIRECT_URI" not in env
    assert "TELEGRAM_BOT_TOKEN=fake" in env
    assert "THREADS_REDIRECT_URI" in r.stdout and "https://3-35-1-2.sslip.io/threads/callback" in r.stdout
    assert list((server.srv / "var" / "env_backups").glob("env_*"))  # 바꾸기 전 .env 백업


def test_https_keeps_redirect_overrides_for_this_host(server: Server) -> None:
    keep = "THREADS_REDIRECT_URI=https://3-35-1-2.sslip.io/threads/callback\n"
    assert _https(server, keep).returncode == 0
    assert keep in (server.srv / ".env").read_text(encoding="utf-8")


def test_https_warns_when_the_ip_changed_and_reminds_static_ip(server: Server) -> None:
    r = _https(server, "PUBLIC_DOMAIN=52-79-136-64.sslip.io\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "52-79-136-64.sslip.io" in r.stdout and "3-35-1-2.sslip.io" in r.stdout and "바뀌었" in r.stdout
    assert "고정 IP" in r.stdout
    assert (server.srv / ".env").read_text(encoding="utf-8").count("PUBLIC_DOMAIN=") == 1


@pytest.mark.parametrize("before", [None, STOCK_CADDYFILE, "1-2-3-4.sslip.io {\n\treverse_proxy 127.0.0.1:8080\n}\n"])
def test_https_replaces_a_stock_or_own_caddyfile(server: Server, before: str | None) -> None:
    assert _https(server, caddyfile=before).returncode == 0
    caddy = (server.sim / "etc" / "Caddyfile").read_text(encoding="utf-8")
    assert "3-35-1-2.sslip.io {" in caddy and "reverse_proxy 127.0.0.1:8080" in caddy
    assert "/usr/share/caddy" not in caddy and "1-2-3-4" not in caddy


def test_https_keeps_other_sites_in_the_caddyfile(server: Server) -> None:
    other = "blog.example.com {\n\treverse_proxy 127.0.0.1:3000\n}\n"
    r = _https(server, caddyfile=other)
    assert r.returncode == 0, r.stdout + r.stderr
    caddy = (server.sim / "etc" / "Caddyfile").read_text(encoding="utf-8")
    assert caddy.startswith(other)  # 다른 사이트는 그대로
    assert f"import {server.sim / 'etc'}/dealbot.caddy" in caddy
    site = (server.sim / "etc" / "dealbot.caddy").read_text(encoding="utf-8")
    assert "3-35-1-2.sslip.io {" in site and "reverse_proxy 127.0.0.1:8080" in site
    # 다시 돌려도 import 는 한 줄
    assert _https(server, caddyfile=None).returncode == 0
    assert (server.sim / "etc" / "Caddyfile").read_text(encoding="utf-8").count("import ") == 1


# ---- setenv.sh


def _setenv(tmp: Path, *args: str, value: str = "") -> subprocess.CompletedProcess[str]:
    script = tmp / "deploy" / "lightsail" / "setenv.sh"
    if not script.exists():
        script.parent.mkdir(parents=True)
        shutil.copy2(LIGHTSAIL / "setenv.sh", script)
        (tmp / ".env").write_text("TELEGRAM_BOT_TOKEN=123:abc\n", encoding="utf-8")
    return subprocess.run(["bash", str(script), *args], cwd=tmp, input=value + "\n", capture_output=True, text=True, timeout=30)


def _backups(tmp: Path) -> list[Path]:
    return sorted((tmp / "var" / "env_backups").glob("env_*"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ADPICK_AFFID", "abcㅋ123"),  # 한글 자판으로 눌린 글자 (예전엔 *_ID 에 안 걸려 그대로 저장)
        ("NTFY_TOPIC", "dealbot\u00a0alerts"),
        ("PUBLIC_DOMAIN", "https://52-79-136-64.sslip.io/"),
        ("PUBLIC_DOMAIN", "52-79-136-64.sslip.ioㅋ"),
        ("PORT", "80８0"),
        ("PORT", "8080a"),
        ("THREADS_REDIRECT_URI", "52-79-136-64.sslip.io/threads/callback"),
        ("TELEGRAM_ADMIN_CHAT_ID", "@me"),
    ],
)
def test_setenv_rejects_bad_values_without_leaving_a_backup(tmp_path: Path, key: str, value: str) -> None:
    r = _setenv(tmp_path, key, value=value)
    assert r.returncode != 0 and "저장하지 않았어요" in r.stdout
    assert key not in (tmp_path / ".env").read_text(encoding="utf-8")
    assert _backups(tmp_path) == []  # 틀린 값이면 비밀값 사본도 안 남김


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ADPICK_AFFID", "abc123"),
        ("PUBLIC_DOMAIN", "52-79-136-64.sslip.io"),
        ("PORT", "8080"),
        ("THREADS_REDIRECT_URI", "https://52-79-136-64.sslip.io/threads/callback"),
        ("TELEGRAM_ADMIN_CHAT_ID", "-1001234"),
        ("DEALBOT_DATA_DIR", "/home/ubuntu/딜봇"),  # 폴더 경로는 한글 가능
    ],
)
def test_setenv_saves_good_values_with_a_backup(tmp_path: Path, key: str, value: str) -> None:
    r = _setenv(tmp_path, key, value=value)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{key}={value}\n" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert value not in r.stdout  # 값은 화면에 안 보임
    assert len(_backups(tmp_path)) == 1


def test_setenv_keeps_only_recent_backups(tmp_path: Path) -> None:
    for i in range(23):
        assert _setenv(tmp_path, "PORT", value=str(8000 + i)).returncode == 0
    assert len(_backups(tmp_path)) == 20
    assert "PORT=8022\n" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_scripts_parse() -> None:
    for f in sorted(LIGHTSAIL.glob("*.sh")):
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, (f.name, r.stderr)
