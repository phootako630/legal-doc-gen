# eval 基线门槛（#82）：变差 / 缓存未命中 / 运行出错 / 基线缺案件失败，变好只提示
from scripts.case_eval import baseline
from scripts.case_eval.scoring import CORRECT, MISSING, PENDING, WRONG


def _r(key, verdict, source_ok=None, silent=False):
    return {"key": key, "verdict": verdict, "source_ok": source_ok, "silent": silent,
            "expected": "标准", "actual": "系统"}


def _case(name, results, error=None, misses=()):
    return {"case": name, "results": results, "error": error, "stats": {"misses": list(misses)}}


BASE = {"cases": {"甲": {
    "a": {"verdict": CORRECT, "source_ok": True, "silent": False},
    "b": {"verdict": MISSING, "source_ok": None, "silent": False},
    "c": {"verdict": WRONG, "source_ok": None, "silent": True},
    "d": {"verdict": PENDING, "source_ok": None, "silent": False},
}}}


def test_same_as_baseline_passes():
    gate = baseline.compare(BASE, [_case("甲", [_r("a", CORRECT, True), _r("b", MISSING), _r("c", WRONG, silent=True)])], "fx")
    assert not gate.failed and not gate.improvements


def test_regressions_fail_and_name_case_and_field():
    gate = baseline.compare(BASE, [_case("甲", [
        _r("a", CORRECT, False),            # 来源由对变错
        _r("b", WRONG, silent=True),        # 缺失 → 错误（更危险）
        _r("c", WRONG, silent=True),
    ])], "fx")
    assert gate.failed
    assert len(gate.regressions) == 2
    assert gate.regressions[0].startswith("甲 / a：正确 → 正确，来源错误")
    assert gate.regressions[1].startswith("甲 / b：缺失 → 错误，静默错误")


def test_improvements_do_not_fail():
    gate = baseline.compare(BASE, [_case("甲", [_r("a", CORRECT, True), _r("b", CORRECT), _r("c", MISSING)])], "fx")
    assert not gate.failed
    assert len(gate.improvements) == 2  # 缺失 → 正确；错误 → 缺失
    assert "--update-baseline" in baseline.render(gate, "fx/baseline.json")


def test_wrong_to_missing_is_better_and_pending_ignored():
    gate = baseline.compare(BASE, [_case("甲", [_r("c", MISSING), _r("d", WRONG)])], "fx")
    assert not gate.failed and len(gate.improvements) == 1


def test_cache_miss_fails_with_rerecord_command():
    # OCR 未命中在线程里被当作单页失败跳过，案件本身没出错：看调用统计
    gate = baseline.compare(BASE, [_case("甲", [_r("a", CORRECT, True)], misses=["OCR"])], "eval_fixtures")
    assert gate.failed
    assert "--case 甲 --mode live" in gate.errors[0] and "rm -rf eval_fixtures/甲/cache" in gate.errors[0]


def test_run_error_and_unknown_case_fail():
    gate = baseline.compare(BASE, [_case("甲", [], error="ValueError: 坏了"), _case("乙", [_r("a", CORRECT)])], "fx")
    assert gate.failed
    assert gate.errors[0] == "甲：运行出错 ValueError: 坏了"
    assert "基线里没有这个案件" in gate.errors[1]


def test_snapshot_roundtrip(tmp_path):
    cases = [_case("甲", [_r("a", CORRECT, True)]), _case("乙", [], error="x")]
    path = tmp_path / "baseline.json"
    baseline.save(str(path), cases)
    data = baseline.load(str(path))
    assert data == {"cases": {"甲": {"a": {"verdict": CORRECT, "source_ok": True, "silent": False}}}}
    assert "系统" not in path.read_text(encoding="utf-8")  # 只存结论，不存取值
