"""Real sandbox runs. Needs Docker and the image: `scripts/sandbox.ps1 build`."""

import shlex
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from app.sandbox.client import CONTAINER_PREFIX, SandboxResult, run_code_sync


def _sandbox_ready() -> bool:
    s = get_settings()
    try:
        cmd = [*shlex.split(s.sandbox_docker), "image", "inspect", s.sandbox_image]
        return subprocess.run(cmd, capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(
    not _sandbox_ready(), reason="Docker or the sandbox image is not available"
)

SESSION = uuid.uuid4()


@pytest.fixture
def s(tmp_path: Path) -> Settings:
    settings = get_settings().model_copy(
        update={"data_dir": tmp_path / "data", "sandbox_timeout_seconds": 20.0}
    )
    session_dir = settings.data_dir / str(SESSION)
    session_dir.mkdir(parents=True)
    (session_dir / "sales.csv").write_text("region,revenue\nnorth,100\nsouth,250\n")
    # Another session's file and a host secret, both outside this session's folder.
    (settings.data_dir / "other-session").mkdir()
    (settings.data_dir / "other-session" / "private.csv").write_text("x\n1\n")
    (settings.data_dir / "host-secret.env").write_text("KEY=1\n")
    return settings


def run(s: Settings, code: str) -> SandboxResult:
    return run_code_sync(s, SESSION, code)


def leftover_containers(s: Settings) -> list[str]:
    cmd = [*shlex.split(s.sandbox_docker), "ps", "-aq", "--filter", f"name={CONTAINER_PREFIX}"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.split()


def test_table_chart_and_stdout(s: Settings) -> None:
    r = run(
        s,
        "df = pd.read_csv(f'{DATA_DIR}/sales.csv')\n"
        "print('rows', len(df))\n"
        "df.plot.bar(x='region', y='revenue')\n"
        "df.groupby('region')['revenue'].sum()\n",
    )

    assert r.status == "ok", r.error
    assert r.stdout == "rows 2\n"
    [table] = r.tables
    assert table.columns == ["region", "revenue"]
    assert table.rows == [["north", 100], ["south", 250]]
    assert r.charts == ["chart_1.png"]
    assert (s.data_dir / str(SESSION) / "runs" / r.run_id / "chart_1.png").is_file()
    assert leftover_containers(s) == []


def test_error_returns_traceback_of_the_code(s: Settings) -> None:
    r = run(s, "df = pd.read_csv(f'{DATA_DIR}/sales.csv')\ndf['nope']\n")

    assert r.status == "error"
    assert r.error is not None
    assert "KeyError: 'nope'" in r.error
    assert 'File "<code>", line 2' in r.error
    assert "df['nope']" in r.error  # source line shown, so the model can fix it
    assert "run.py" not in r.error  # runner frames hidden


def test_syntax_error(s: Settings) -> None:
    r = run(s, "def (:\n")
    assert r.status == "error"
    assert "SyntaxError" in (r.error or "")


def test_no_network(s: Settings) -> None:
    r = run(
        s,
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('connected')\n"
        "except OSError as e:\n"
        "    print('blocked', e)\n"
        "print(sorted(socket.if_nameindex()))\n",
    )
    assert r.status == "ok", r.error
    assert "blocked" in r.stdout
    assert "(1, 'lo')]" in r.stdout  # loopback is the only interface


def test_timeout_kills_infinite_loop(s: Settings) -> None:
    s = s.model_copy(update={"sandbox_timeout_seconds": 3.0})
    r = run(s, "while True:\n    pass\n")
    assert r.status == "timeout"
    assert leftover_containers(s) == []


def test_memory_limit(s: Settings) -> None:
    s = s.model_copy(update={"sandbox_memory_mb": 128})
    r = run(s, "x = b'x' * (1024 ** 3)\n")
    assert r.status == "oom" or "MemoryError" in (r.error or ""), r
    assert leftover_containers(s) == []


def test_cgroup_limits_and_identity(s: Settings) -> None:
    r = run(
        s,
        "import os\n"
        "print(os.getuid(), os.getgid())\n"
        "print(open('/sys/fs/cgroup/memory.max').read().strip())\n"
        "print(open('/sys/fs/cgroup/cpu.max').read().strip())\n"
        "print(open('/sys/fs/cgroup/pids.max').read().strip())\n"
        "print(open('/proc/self/status').read().split('CapEff:')[1].split()[0])\n",
    )
    assert r.status == "ok", r.error
    uid, mem, cpu, pids, caps = r.stdout.splitlines()
    assert uid == "10001 10001"
    assert mem == str(512 * 1024 * 1024)
    assert cpu == "100000 100000"  # 1 CPU
    assert pids == "64"
    assert int(caps, 16) == 0  # no capabilities


def test_only_the_session_folder_is_visible(s: Settings) -> None:
    r = run(
        s,
        "import os\n"
        "print(sorted(os.listdir('/data')))\n"
        "hits = [os.path.join(d, f) for d, _, fs in os.walk('/')\n"
        "        if not d.startswith(('/proc', '/sys'))\n"
        "        for f in fs if f in ('private.csv', 'host-secret.env', '.env')]\n"
        "print(hits)\n"
        "print(os.path.exists('/mnt/e'), os.path.exists('/var/run/docker.sock'))\n",
    )
    assert r.status == "ok", r.error
    listing, hits, host = r.stdout.splitlines()
    assert listing == "['runs', 'sales.csv']"
    assert hits == "[]"
    assert host == "False False"


def test_filesystem_is_read_only_except_out_and_tmp(s: Settings) -> None:
    r = run(
        s,
        "for p in ['/data/new.csv', '/usr/x', '/runner/run.py', '/out/ok.txt', '/tmp/ok']:\n"
        "    try:\n"
        "        open(p, 'w').write('x')\n"
        "        print(p, 'writable')\n"
        "    except OSError:\n"
        "        print(p, 'blocked')\n",
    )
    assert r.status == "ok", r.error
    assert r.stdout.splitlines() == [
        "/data/new.csv blocked",
        "/usr/x blocked",
        "/runner/run.py blocked",
        "/out/ok.txt writable",
        "/tmp/ok writable",
    ]


def test_runaway_output_is_capped(s: Settings) -> None:
    r = run(s, "for i in range(10**6):\n    print('spam spam spam')\n")
    assert r.status == "ok", r.error
    assert len(r.stdout) == s.sandbox_max_output_chars
    assert any("cut" in n for n in r.notes)


def test_code_that_kills_the_runner_is_reported(s: Settings) -> None:
    r = run(s, "import os\nos._exit(3)\n")
    assert r.status == "crashed"
    assert "exit code 3" in (r.error or "")


@pytest.mark.parametrize("target", ["/etc/hostname", "/mnt/e/ai-agent-portfolio/datasage/.env"])
def test_symlinks_planted_by_the_code_are_not_followed(s: Settings, target: str) -> None:
    r = run(s, f"import os\nos.symlink({target!r}, '/out/result.json')\nos._exit(0)\n")
    assert r.status == "crashed"
    assert "KEY" not in r.model_dump_json() and "DATABASE" not in r.model_dump_json()


def test_symlinked_chart_is_dropped(s: Settings) -> None:
    r = run(
        s,
        "import os\n"
        "plt.plot([1, 2])\n"
        "os.symlink('/data/sales.csv', '/out/chart_9.png')\n"
        "show(pd.DataFrame({'a': [1]}))\n",
    )
    assert r.status == "ok", r.error
    assert r.charts == ["chart_1.png"]
