# eval 链路追踪（Langfuse，自托管）：每个案件一条 trace，案件里每次 LLM / OCR / 裁判调用各一条记录，
# 逐字段结论写成 score。只用于开发与 eval（eval_cases.py --langfuse），线上系统不导入本模块。
#
# 为什么挂在 ReplayCache 上：缓存的三个包装（wrap_chat / wrap_ocr / wrap_judge）是 eval 调用外部服务的
# 唯一出口，命中缓存的回放也记一条（metadata.cached=True），回放时同样能在 Langfuse 里看提示词与返回。
#
# 数据：trace 里有真实案卷内容（提示词、抽取结果、标准值），只许发到自托管的 Langfuse；
# 发往 Langfuse Cloud（*.langfuse.com）默认拒绝，只有虚构案卷可加 --langfuse-allow-cloud。
# OCR 只记页面图片的哈希，不上传图片。
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

CLOUD_SUFFIX = "langfuse.com"
# 允许发往 Langfuse Cloud 的唯一案卷目录：仓库里的虚构案卷集（按解析软链接后的真实路径比较）
FIXTURES_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "eval_fixtures"))


class _NoopObs:
    def update(self, **_: Any) -> None:
        pass


class NoopTracer:
    """不追踪（默认）：接口与 CaseTracer 相同，什么都不做。"""

    @contextmanager
    def observe(self, name: str, as_type: str = "generation", **_: Any) -> Iterator[_NoopObs]:
        yield _NoopObs()

    def finish(self, result: dict[str, Any]) -> None:
        pass


class CaseTracer:
    """一个案件的 trace：子记录用显式的 trace_context 挂到根 span 下（OCR 在线程池里跑，不依赖上下文传递）。"""

    def __init__(self, client: Any, root: Any) -> None:
        self.client = client
        self.root = root
        self.trace_context = {"trace_id": root.trace_id, "parent_span_id": root.id}

    @contextmanager
    def observe(self, name: str, as_type: str = "generation", **kwargs: Any) -> Iterator[Any]:
        obs = self.client.start_observation(trace_context=self.trace_context, name=name, as_type=as_type, **kwargs)
        try:
            yield obs
        except Exception as exc:
            obs.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:500])
            raise
        finally:
            obs.end()

    def finish(self, result: dict[str, Any]) -> None:
        """
        案件跑完：根 span 记结论汇总（出错时标 ERROR）；逐字段结论 → CATEGORICAL score，
        裁判结论另记一条，案件准确率 → NUMERIC score。
        """
        if result.get("error"):
            self.root.update(level="ERROR", status_message=result["error"][:500])
        self.root.update(input={"materials": result.get("materials")},
                         output={k: result.get(k) for k in ("summary", "quality", "causes", "readiness", "error")})
        tid = self.root.trace_id
        summary = result.get("summary") or {}
        for r in result.get("results") or []:
            comment = f"标准值：{r.get('expected')}\n系统值：{r.get('actual')}"
            self.client.create_score(trace_id=tid, name=r["key"], value=r["verdict"], data_type="CATEGORICAL",
                                     comment=comment)
            if r.get("match") == "judge":
                self.client.create_score(trace_id=tid, name=f"裁判/{r['key']}", value=r.get("judge_verdict") or "未裁判",
                                         data_type="CATEGORICAL", comment=r.get("judge_reason") or None)
        scored = sum(summary.get(k, 0) for k in ("正确", "错误", "缺失"))
        if scored:
            self.client.create_score(trace_id=tid, name="准确率", value=summary.get("正确", 0) / scored,
                                     data_type="NUMERIC")


class Tracing:
    """一次 eval 运行的追踪：持有 Langfuse 客户端，按案件开 trace。"""

    def __init__(self, client: Any, run: str, tags: list[str]) -> None:
        self.client = client
        self.run = run
        self.tags = tags

    @contextmanager
    def case(self, name: str, metadata: dict[str, Any] | None = None) -> Iterator[CaseTracer]:
        from langfuse import propagate_attributes

        with propagate_attributes(session_id=self.run, tags=self.tags, trace_name=f"eval {name}"):
            with self.client.start_as_current_observation(name=f"eval {name}", as_type="span",
                                                          metadata=metadata or {}) as root:
                yield CaseTracer(self.client, root)

    def flush(self) -> None:
        self.client.flush()

    def url(self) -> str:
        return os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or ""


def check_host(host: str, allow_cloud: bool, root: str | None = None) -> None:
    """
    真实案卷只许发到自托管的 Langfuse：Cloud 地址默认拒绝；--langfuse-allow-cloud 也只放行
    仓库里的虚构案卷集 eval_fixtures/（#156 评审：只靠文档约束，真实案卷目录加上这个参数照样会上云）。
    """
    netloc = urlparse(host).hostname or ""
    if not netloc:
        raise SystemExit("--langfuse 需要设置 LANGFUSE_HOST（自托管 Langfuse 的地址，如 http://localhost:3000）")
    if not (netloc == CLOUD_SUFFIX or netloc.endswith("." + CLOUD_SUFFIX)):
        return
    if not allow_cloud:
        raise SystemExit(
            f"LANGFUSE_HOST 指向 Langfuse Cloud（{netloc}）：trace 里有案卷内容，只许发到自托管的 Langfuse。"
            "只跑虚构案卷集 eval_fixtures/ 时可加 --langfuse-allow-cloud"
        )
    if root is None or os.path.realpath(root) != FIXTURES_DIR:
        raise SystemExit(
            f"--langfuse-allow-cloud 只用于仓库里的虚构案卷集（{FIXTURES_DIR}），"
            f"这次的案卷目录是 {root}：不许发往 Langfuse Cloud"
        )


def make_tracing(run: str, tags: list[str], allow_cloud: bool = False, root: str | None = None) -> Tracing:
    """按环境变量 LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 连接；连不上直接报错退出。"""
    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or ""
    check_host(host, allow_cloud, root)
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        raise SystemExit("--langfuse 需要设置 LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY（Langfuse 项目设置页生成）")
    from langfuse import Langfuse

    client = Langfuse(host=host)
    try:
        ok = client.auth_check()
    except Exception as exc:  # noqa: BLE001 — 密钥错（401）、连不上等都在这里报成一句话
        raise SystemExit(f"连不上 Langfuse（{host}）或密钥不对：{type(exc).__name__}") from None
    if not ok:
        raise SystemExit(f"连不上 Langfuse（{host}）或密钥不对")
    return Tracing(client, run, tags)


def image_digest(img_b64: str) -> str:
    return hashlib.sha256(img_b64.encode("ascii")).hexdigest()[:16]
