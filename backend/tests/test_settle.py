"""作业结算：归属校验、金额锁定、幂等确认与争议留痕的回归测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import SEED_ROWS
from app.services import settle as settle_service
from app.store import store

client = TestClient(app)


def _reset_rows() -> None:
    """每个用例回到种子数据，避免内存存储互相串状态。"""
    table = store.rows("settle")
    table.clear()
    table.extend(dict(row) for row in SEED_ROWS["settle"])


def _action(entry_id: int, action: str, operator: str, **extra: object) -> dict:
    # 对账员姓名随 JSON 提交，避免 HTTP 头只能走 Latin-1 的编码限制。
    resp = client.post(
        f"/api/settle/{entry_id}/actions",
        json={"values": {"action": action, "operator": operator, **extra}},
    )
    assert resp.status_code == 200
    return resp.json()


def setup_function(_func: object) -> None:
    _reset_rows()
    settle_service.SettleService.clock = staticmethod(lambda: "2026-09-26 09:00:00")


def test_non_owner_cannot_confirm_and_reason_is_explained() -> None:
    # SETT-0002 归属李洁，王敏不是该结算对象的对账员。
    result = _action(2, "确认结算", "王敏")
    assert result["ok"] is False
    assert "归属对账员 李洁" in result["message"]
    assert "无权提交" in result["message"]

    detail = client.get("/api/settle/2").json()
    assert detail["status"] == "核对中"
    assert "收款确认人" not in detail


def test_anonymous_operator_is_rejected() -> None:
    resp = client.post(
        "/api/settle/2/actions",
        json={"values": {"action": "确认结算"}},
    )
    body = resp.json()
    assert body["ok"] is False
    assert "对账员身份" in body["message"]


def test_owner_confirm_locks_amount_and_ignores_submitted_value() -> None:
    # 请求里试图把已收金额改成 1 元，服务端必须按应收金额入账。
    result = _action(2, "确认结算", "李洁", **{"已收金额": 1})
    assert result["ok"] is True
    assert "142800" in result["message"]

    detail = client.get("/api/settle/2").json()
    assert detail["status"] == "已收款"
    assert detail["结算状态"] == "已收款"
    assert detail["已收金额"] == 142800.0
    assert detail["应收金额"] == 142800.0
    assert detail["收款确认人"] == "李洁"


def test_duplicate_confirm_keeps_single_result() -> None:
    _action(2, "确认结算", "李洁")
    first = client.get("/api/settle/2").json()

    # 用固定时钟区分第一次与第二次；再次确认不应改时间、不应改金额、不应产生新记录。
    settle_service.SettleService.clock = staticmethod(lambda: "2026-10-01 08:00:00")
    repeat = _action(2, "确认结算", "李洁")
    assert repeat["ok"] is True
    assert "请勿重复确认" in repeat["message"]

    second = client.get("/api/settle/2").json()
    assert second["已收金额"] == first["已收金额"] == 142800.0
    assert second["收款确认时间"] == "2026-09-26 09:00:00"
    assert len(store.rows("settle")) == len(SEED_ROWS["settle"])


def test_dispute_records_operator_and_keeps_first_marker() -> None:
    result = _action(2, "标记争议", "李洁")
    assert result["ok"] is True
    assert result["entry"]["争议标记人"] == "李洁"

    settle_service.SettleService.clock = staticmethod(lambda: "2026-10-02 08:00:00")
    repeat = _action(2, "标记争议", "李洁")
    assert repeat["ok"] is True
    assert "沿用原标记" in repeat["message"]

    detail = client.get("/api/settle/2").json()
    assert detail["争议标记人"] == "李洁"
    assert detail["争议标记时间"] == "2026-09-26 09:00:00"
    assert detail["abnormal"] is True


def test_confirm_blocked_while_in_dispute() -> None:
    result = _action(2, "标记争议", "李洁")
    assert result["ok"] is True
    again = _action(2, "确认结算", "李洁")
    assert again["ok"] is False
    assert "争议" in again["message"]


def test_start_check_then_confirm_full_flow() -> None:
    blocked = _action(1, "确认结算", "王敏")
    assert blocked["ok"] is False
    assert "先发起核对" in blocked["message"]

    started = _action(1, "发起核对", "王敏")
    assert started["ok"] is True
    assert started["entry"]["status"] == "核对中"

    confirmed = _action(1, "确认结算", "王敏")
    assert confirmed["ok"] is True
    assert confirmed["entry"]["status"] == "已收款"
    assert confirmed["entry"]["已收金额"] == 186500.0


def test_list_and_detail_share_same_amount_after_refresh() -> None:
    _action(2, "确认结算", "李洁")
    # 模拟刷新：列表与详情都重新从服务端读取，金额口径必须一致且不回退。
    listed = next(item for item in client.get("/api/settle").json()["items"] if item["id"] == 2)
    detail = client.get("/api/settle/2").json()
    assert listed["已收金额"] == detail["已收金额"] == 142800.0
    assert listed["status"] == detail["status"] == "已收款"


def test_readonly_account_sees_no_change_after_action() -> None:
    # 值班管理员对所有结算单都只有查看权限。
    for entry_id in (1, 2):
        result = _action(entry_id, "发起核对", "值班管理员")
        assert result["ok"] is False
        assert "可查看但不能改动" in result["message"]
    assert client.get("/api/settle/1").json()["status"] == "待核对"
