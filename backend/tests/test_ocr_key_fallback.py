# DashScope 付费备用 key：主 key（免费额度）被拒（401/403）时改用备用 key，限流（429）不切换
import pytest
import requests

from app.services import ocr_engine


class _Resp:
    def __init__(self, status: int):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def json(self):
        return {"output": {"choices": [{"message": {"content": [{"text": "识别文本"}]}}]}}


@pytest.fixture
def calls(monkeypatch):
    """记录每次请求用的 key；按 key 返回预设状态码。"""
    used: list[str] = []
    status_by_key: dict[str, int] = {}

    def fake_post(_url, headers, json, timeout):
        key = headers["Authorization"].removeprefix("Bearer ")
        used.append(key)
        return _Resp(status_by_key.get(key, 200))

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(ocr_engine, "DASHSCOPE_API_KEY", "free-key")
    monkeypatch.setattr(ocr_engine, "DASHSCOPE_API_KEY_BACKUP", "paid-key")
    monkeypatch.setattr(ocr_engine, "_use_backup", False)
    return used, status_by_key


def test_primary_key_used_when_ok(calls):
    used, _ = calls
    assert ocr_engine._ocr_image_b64("img") == "识别文本"
    assert used == ["free-key"]


def test_switch_to_backup_on_403_and_stay(calls):
    used, status = calls
    status["free-key"] = 403  # 免费额度用完
    assert ocr_engine._ocr_image_b64("img") == "识别文本"
    assert ocr_engine._ocr_image_b64("img") == "识别文本"
    # 第一次撞主 key 后切到备用 key；第二次直接用备用 key
    assert used == ["free-key", "paid-key", "paid-key"]


def test_rate_limit_does_not_switch(calls):
    used, status = calls
    status["free-key"] = 429
    with pytest.raises(requests.HTTPError):
        ocr_engine._ocr_image_b64("img")
    assert used == ["free-key"]


def test_no_backup_configured_raises(calls, monkeypatch):
    used, status = calls
    monkeypatch.setattr(ocr_engine, "DASHSCOPE_API_KEY_BACKUP", None)
    status["free-key"] = 403
    with pytest.raises(requests.HTTPError):
        ocr_engine._ocr_image_b64("img")
    assert used == ["free-key"]


def test_backup_only(calls, monkeypatch):
    used, _ = calls
    monkeypatch.setattr(ocr_engine, "DASHSCOPE_API_KEY", None)
    assert ocr_engine._ocr_image_b64("img") == "识别文本"
    assert used == ["paid-key"]
