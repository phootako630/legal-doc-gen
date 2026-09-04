# file_store 单测：字节暂存 put/get 往返、未知 id、容量淘汰。
from app.services import file_store


def test_put_get_roundtrip():
    fid = file_store.put(b"hello-pdf-bytes")
    assert isinstance(fid, str) and fid
    assert file_store.get(fid) == b"hello-pdf-bytes"


def test_get_unknown_or_none_returns_none():
    assert file_store.get(None) is None
    assert file_store.get("nonexistent-id") is None


def test_capacity_eviction_drops_oldest():
    file_store.clear()
    first = file_store.put(b"first")
    # 塞满超过上限，最早一份应被淘汰
    for i in range(70):
        file_store.put(f"f{i}".encode())
    assert file_store.get(first) is None
    file_store.clear()
