# file_store 单测：落盘 put/get 往返、未知/非法 id、路径穿越防护、过期清理。
import os
import time

from app.services import file_store


def test_put_get_roundtrip():
    fid = file_store.put(b"hello-pdf-bytes")
    assert isinstance(fid, str) and fid
    assert file_store.get(fid) == b"hello-pdf-bytes"


def test_get_unknown_or_none_returns_none():
    assert file_store.get(None) is None
    assert file_store.get("nonexistent-id") is None
    assert file_store.get("0" * 32) is None  # 格式合法但不存在


def test_get_rejects_path_traversal(tmp_path):
    # file_id 来自客户端请求：即使目录外有这个文件，也不能被读到
    (tmp_path / "secret").write_bytes(b"secret")
    assert file_store.get("../secret") is None
    assert file_store.get(str(tmp_path / "secret")) is None


def test_purge_expired_removes_only_old_files():
    old = file_store.put(b"old")
    fresh = file_store.put(b"fresh")
    two_days_ago = time.time() - 2 * 86400
    path = os.path.join(file_store.UPLOAD_DIR, old)
    os.utime(path, (two_days_ago, two_days_ago))

    assert file_store.purge_expired(retention_days=1) == 1
    assert file_store.get(old) is None
    assert file_store.get(fresh) == b"fresh"


def test_purge_on_missing_dir_is_noop():
    assert file_store.purge_expired(retention_days=1) == 0
