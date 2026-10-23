# deploy/backup.sh：用假 docker 命令代替容器，在临时目录里跑一遍备份 + 恢复演练（数据均为虚构）
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

from app import config
from app.services import auth_store, branch_table

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "deploy" / "backup.sh"


def _fake_docker(bin_dir: Path, data: Path) -> None:
    """`docker compose exec -T backend python -c CODE` → 在本机后端目录里执行 CODE，数据指向临时目录。"""
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "shift 5  # compose exec -T backend python\n"
        f'cd "{_REPO / "backend"}"\n'
        f'BRANCH_INFO_PATH="{data}/branch_info.json" BRANCH_HISTORY_DIR="{data}/branch_history" '
        f'AUTH_DB_PATH="{data}/auth.sqlite" exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _make_data(tmp_path: Path, monkeypatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    # 原告信息表：保存两次，第一次的进历史版本
    entry = {
        "name": "虚构电梯有限公司江苏分公司",
        "credit_code": "",
        "address": "虚构地址",
        "representative": "某某",
        "phone": "",
    }
    for _ in range(2):
        branch_table.save_table(
            [entry],
            "虚构.xlsx",
            str(data / "branch_info.json"),
            str(data / "branch_history"),
        )
    monkeypatch.setattr(config, "AUTH_DB_PATH", str(data / "auth.sqlite"))
    auth_store.create_user("lawyer01", "虚构的足够长密码123")
    auth_store.create_session("lawyer01")
    (data / "checkpoints.sqlite").write_bytes(b"case data must not be backed up")
    return data


def _run(
    args: list[str], tmp_path: Path, env_extra: dict
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        **env_extra,
    }
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO,
    )


def test_backup_and_restore_drill(tmp_path, monkeypatch):
    data = _make_data(tmp_path, monkeypatch)
    _fake_docker(tmp_path / "bin", data)
    backups = tmp_path / "backups"
    old = backups / "legal-doc-20000101-000000.tar.gz"
    backups.mkdir()
    old.write_bytes(b"old")
    os.utime(old, (0, 0))  # 远超保留期

    res = _run(
        [], tmp_path, {"BACKUP_DIR": str(backups), "BACKUP_RETENTION_DAYS": "30"}
    )
    assert res.returncode == 0, res.stderr
    (archive,) = backups.glob("legal-doc-*.tar.gz")  # 旧备份已清理，没有残留的半截文件
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600

    with tarfile.open(archive) as tar:
        names = tar.getnames()
        tar.extractall(tmp_path / "x")
    assert "branch_info.json" in names and "auth.sqlite" in names
    assert not any(
        "checkpoints" in n or "uploads" in n for n in names
    )  # 案件数据不备份
    con = sqlite3.connect(tmp_path / "x" / "auth.sqlite")
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert (
        con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    )  # 登录会话不带走
    con.close()

    drill = _run(["verify", str(archive)], tmp_path, {})
    assert drill.returncode == 0, drill.stderr
    assert "原告信息表：可读，1 行" in drill.stdout
    assert "历史版本：1 份" in drill.stdout
    assert "1 个账号" in drill.stdout and "恢复演练通过" in drill.stdout
    assert (
        "虚构电梯" not in drill.stdout and "lawyer01" not in drill.stdout
    )  # 只打印数量


def test_verify_rejects_broken_backup(tmp_path):
    (tmp_path / "bin").mkdir()
    bad = tmp_path / "legal-doc-bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tmp_path / "branch_info.json"
        info.write_text(json.dumps({"entries": []}), encoding="utf-8")
        tar.add(info, arcname="branch_info.json")
    res = _run(["verify", str(bad)], tmp_path, {})
    assert res.returncode != 0
    assert "原告信息表是空的" in res.stderr and "缺少账号库" in res.stderr


def test_failed_backup_leaves_no_partial_file(tmp_path):
    # 容器里打包失败（如后端没在运行）：脚本报错退出，不留半截文件冒充备份
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(
        "#!/usr/bin/env bash\necho 'no such service' >&2\nexit 1\n"
    )
    (bin_dir / "docker").chmod(0o755)
    backups = tmp_path / "backups"
    res = _run([], tmp_path, {"BACKUP_DIR": str(backups)})
    assert res.returncode != 0
    assert list(backups.iterdir()) == []
