# eval 的录制 / 回放缓存：按请求内容哈希保存 LLM 与 OCR 的返回，改规则后可零费用重跑。
#
# 拦截两处（都是 agent 实际调用外部服务的唯一入口）：
#   - app.agent.nodes.chat：DeepSeek 调用，键 = 模型 + 消息 + json_mode；
#   - app.services.ocr_engine._ocr_image_b64：单页 OCR，键 = OCR 模型 + 页面图片。
# 页面图片由 PyMuPDF 确定性渲染，同一份 PDF 同一页每次哈希相同。
# 缓存里是真实案卷的识别结果，只放在服务器的案卷目录下，不进仓库。
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from app import config
from app.agent import nodes
from app.services import ocr_engine

MODES = ("replay", "live")


class CacheMiss(RuntimeError):
    """严格回放时缓存未命中：说明这次运行要读新的页或发新的提示词。"""


@dataclass
class CacheStats:
    """单个案件的调用统计，写进报告（看 OCR 页数、是否触网）。"""

    llm_hits: int = 0
    llm_calls: int = 0  # 真实调用（未命中或 live 模式）
    ocr_hits: int = 0
    ocr_calls: int = 0
    misses: list[str] = field(default_factory=list)  # 严格回放时未命中的请求类型

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_hits": self.llm_hits,
            "llm_calls": self.llm_calls,
            "ocr_hits": self.ocr_hits,
            "ocr_calls": self.ocr_calls,
            "misses": list(self.misses),
        }


def _digest(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def llm_key(messages: list[dict], json_mode: bool) -> str:
    return _digest({"model": config.DEEPSEEK_MODEL, "messages": messages, "json": json_mode})


def ocr_key(img_b64: str) -> str:
    img = hashlib.sha256(img_b64.encode("ascii")).hexdigest()
    return _digest({"model": config.QWEN_OCR_MODEL, "image": img})


class ReplayCache:
    """
    一个案件一份缓存目录（<案件>/cache/llm/*.json、cache/ocr/*.txt）。

    mode="replay"：先查缓存；未命中时 strict=True 抛 CacheMiss，否则真实调用并录下。
    mode="live"：一律真实调用并覆盖缓存（换模型、改提示词后重建基线用）。
    """

    def __init__(self, root: str, mode: str = "replay", strict: bool = False) -> None:
        if mode not in MODES:
            raise ValueError(f"未知模式：{mode}（可选 {'/'.join(MODES)}）")
        self.root = root
        self.mode = mode
        self.strict = strict
        self.stats = CacheStats()
        self._lock = threading.Lock()  # OCR 在线程池里并发调用
        self.ocr_available = True  # 是否配置了 DashScope key（patched 进入时设置）

    def _path(self, kind: str, key: str, ext: str) -> str:
        return os.path.join(self.root, kind, f"{key}.{ext}")

    def _read(self, path: str) -> str | None:
        if self.mode == "live" or not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _write(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def _miss(self, kind: str) -> None:
        with self._lock:
            self.stats.misses.append(kind)
        if self.strict:
            raise CacheMiss(f"{kind} 缓存未命中（严格回放不调用外部服务）")

    def wrap_chat(self, real: Callable[..., Any]) -> Callable[..., Any]:
        async def chat(messages: list[dict], json_mode: bool = False) -> Any:
            path = self._path("llm", llm_key(messages, json_mode), "json")
            cached = self._read(path)
            if cached is not None:
                self.stats.llm_hits += 1
                return json.loads(cached)["result"]
            if self.mode == "replay":
                self._miss("LLM")
            result = await real(messages, json_mode=json_mode)
            self.stats.llm_calls += 1
            self._write(path, json.dumps({"result": result}, ensure_ascii=False))
            return result

        return chat

    def wrap_ocr(self, real: Callable[[str], str]) -> Callable[[str], str]:
        def ocr(img_b64: str) -> str:
            path = self._path("ocr", ocr_key(img_b64), "txt")
            cached = self._read(path)
            if cached is not None:
                with self._lock:
                    self.stats.ocr_hits += 1
                return cached
            if self.mode == "replay":
                self._miss("OCR")
            if not self.ocr_available:
                raise RuntimeError("OCR 缓存里没有这一页，且未配置 DASHSCOPE_API_KEY")
            text = real(img_b64)
            with self._lock:
                self.stats.ocr_calls += 1
            # 空串多半是接口异常（_ocr_image_b64 出错时返回空串），不录，下次重试
            if text:
                self._write(path, text)
            return text

        return ocr


class patched:
    """在 with 块内把 agent 的 LLM / OCR 入口换成带缓存的版本，退出时还原。"""

    def __init__(self, cache: ReplayCache) -> None:
        self.cache = cache
        self._saved: list[tuple[Any, str, Any]] = []

    def _set(self, obj: Any, name: str, value: Any) -> None:
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def __enter__(self) -> ReplayCache:
        self._set(nodes, "chat", self.cache.wrap_chat(nodes.chat))
        self._set(ocr_engine, "_ocr_image_b64", self.cache.wrap_ocr(ocr_engine._ocr_image_b64))
        # ocr_page 一开始就检查 DashScope key；纯回放不需要 key，给一个占位让它走到缓存
        self.cache.ocr_available = bool(ocr_engine._keys())
        if self.cache.mode == "replay" and not self.cache.ocr_available:
            self._set(ocr_engine, "_keys", lambda: ["(replay)"])
        return self.cache

    def __exit__(self, *exc: object) -> None:
        for obj, name, value in reversed(self._saved):
            setattr(obj, name, value)
        self._saved.clear()
