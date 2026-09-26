"""作业结算业务规则：状态流转、字段校验、归属权限与操作留痕都收在这里。

收款确认的口径：
- 只有结算对象在对账员名册里对应的对账员能提交写操作，其他账号只读；
- 确认收款时实收金额落库，之后列表与详情读的是同一份数据，刷新不会倒回；
- 同一结算单只保留一次收款确认结果，重复/越权提交都会说明原因后拒绝；
- 争议标记必须留下登记人与登记时间。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "settle"
REQUIRED_FIELDS = ["结算单号", "结算对象", "结算周期"]
STATUS_ORDER = ["待核对", "核对中", "已确认", "已收款", "有争议"]

# 结算对象 → 负责对账员：归属名册，收款确认只认这份映射
RECONCILER_ROSTER: dict[str, str] = {
    "远洋海运有限公司": "林对账",
    "东港货代公司": "周对账",
    "临港冷链物流": "陈对账",
    "华景船务代理": "林对账",
}

# 结算单上可执行的动作，沿用既有对账习惯
ACTION_RULES = {"发起核对": "核对中", "确认结算": "已收款", "标记争议": "有争议"}
# 金额、数量等业务字段（登记时透传保留）
AMOUNT_FIELDS = ["作业量", "应收金额", "已收金额"]


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_amount(raw: Any) -> float | None:
    """把提交值解析成非负金额；无法解析时返回 None 交给调用方说明原因。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    return amount


class SettleService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("结算单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def roster(self) -> dict[str, Any]:
        """给前端的账号视角：哪些账号是对账员、每个结算对象归谁、其他账号只读。"""
        accounts = sorted(set(RECONCILER_ROSTER.values()))
        return {
            "accounts": accounts,
            "roster": dict(RECONCILER_ROSTER),
            "read_only_hint": "非结算对象对应对账员的账号仅可查看，不能提交确认",
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in AMOUNT_FIELDS + ["开票状态"]:
            if values.get(field) is not None:
                entry[field] = values.get(field)
        # 归属以名册为准，登记时直接带出负责对账员
        entry["对账员"] = RECONCILER_ROSTER.get(str(values.get("结算对象") or ""), "")
        entry["status"] = STATUS_ORDER[0]
        entry["结算状态"] = entry["status"]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        operator: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"结算单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于作业结算可执行范围"

        # 先验身份与归属，任何越权提交都在改动数据之前拒绝
        denied = self._deny_reason(entry, operator)
        if denied:
            return None, denied

        settle_no = str(entry.get("结算单号") or entry_id)
        if action == "发起核对":
            return self._start_check(entry, settle_no, operator)
        if action == "确认结算":
            return self._confirm_receipt(entry, settle_no, operator, values)
        return self._mark_dispute(entry, settle_no, operator, values)

    def _deny_reason(self, entry: dict[str, Any], operator: str) -> str:
        """归属校验：不是本结算对象对应对账员的账号只能查看。"""
        if not operator:
            return "未识别到操作账号，无法提交结算操作；请切换到对账员账号后再试"
        target = str(entry.get("结算对象") or "")
        owner = RECONCILER_ROSTER.get(target)
        if owner is None:
            return (
                f"结算对象「{target}」尚未在对账员名册中分配归属，"
                "收款确认已暂停，请联系结算主管补充分配"
            )
        if operator != owner:
            return (
                f"当前账号「{operator}」不是结算对象「{target}」的负责对账员"
                f"（负责对账员：{owner}），仅可查看，不能提交确认"
            )
        return ""

    def _start_check(self, entry: dict[str, Any], settle_no: str, operator: str) -> tuple[dict[str, Any] | None, str]:
        status = str(entry.get("status") or "")
        if status == "核对中":
            return None, f"结算单 {settle_no} 已在核对流程中（{entry.get('核对发起人', '—')} 发起），无需重复发起"
        if status != "待核对":
            return None, f"结算单 {settle_no} 当前为「{status}」，不能再发起核对"
        entry["status"] = "核对中"
        entry["结算状态"] = "核对中"
        entry["pending"] = True
        entry["abnormal"] = False
        entry["核对发起人"] = operator
        entry["核对发起时间"] = _now_text()
        return entry, f"结算单 {settle_no} 已发起核对"

    def _confirm_receipt(
        self,
        entry: dict[str, Any],
        settle_no: str,
        operator: str,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        status = str(entry.get("status") or "")
        # 重复确认只保留第一次结果，第二次直接拒绝且不再改任何字段
        if status == "已收款":
            return None, (
                f"结算单 {settle_no} 已于 {entry.get('收款确认时间', '—')} "
                f"由对账员 {entry.get('收款确认人', '—')} 确认收款，请勿重复确认"
            )
        if status not in ("核对中", "已确认", "有争议"):
            return None, f"结算单 {settle_no} 当前为「{status}」，需先完成核对才能确认收款"

        received = _as_amount(values.get("收款金额"))
        if values.get("收款金额") not in (None, "") and received is None:
            return None, f"收款金额「{values.get('收款金额')}」不是合法金额，请重新填写"
        if received is not None and received < 0:
            return None, "收款金额不能为负数，请重新填写"
        if received is None:
            received = _as_amount(entry.get("已收金额"))
        if received is None:
            received = _as_amount(entry.get("应收金额"))
        if received is None:
            return None, "未填写收款金额，且结算单没有可沿用的应收金额，无法确认收款"

        # 归属与状态都通过后才落库：金额、操作人、时间一并写死，刷新不倒回
        entry["已收金额"] = received
        entry["status"] = "已收款"
        entry["结算状态"] = "已收款"
        entry["pending"] = False
        entry["abnormal"] = False
        entry["收款确认人"] = operator
        entry["收款确认时间"] = _now_text()
        if str(entry.get("开票状态") or "") in ("", "未开票"):
            entry["开票状态"] = "未开票"

        receivable = _as_amount(entry.get("应收金额"))
        if receivable is not None and abs(receivable - received) > 0.005:
            return entry, (
                f"结算单 {settle_no} 收款已确认：实收 {received:.2f}，"
                f"与应收 {receivable:.2f} 存在差额，请跟进核销"
            )
        return entry, f"结算单 {settle_no} 收款已确认，实收 {received:.2f}"

    def _mark_dispute(
        self,
        entry: dict[str, Any],
        settle_no: str,
        operator: str,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        status = str(entry.get("status") or "")
        # 争议同样只登记一次，重复标记不另生记录
        if status == "有争议":
            return None, (
                f"结算单 {settle_no} 的争议已由 {entry.get('争议登记人', '—')} "
                f"于 {entry.get('争议登记时间', '—')} 登记，处理中请勿重复标记"
            )
        entry["status"] = "有争议"
        entry["结算状态"] = "有争议"
        entry["pending"] = True
        entry["abnormal"] = True
        entry["争议登记人"] = operator
        entry["争议登记时间"] = _now_text()
        reason = str(values.get("争议原因") or "").strip()
        if reason:
            entry["争议原因"] = reason
        return entry, f"结算单 {settle_no} 已标记争议，登记人：{operator}"
