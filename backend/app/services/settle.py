"""作业结算业务规则：状态流转、归属校验、金额锁定与争议留痕都收在这里。

口径约定（延续既有对账习惯，状态序列不变）：
待核对 → 核对中 → 已确认 → 已收款，任何环节都可以「标记争议」。
- 只有结算单归属的对账员能提交动作，其他人只能查看；
- 确认收款由服务端锁定已收金额，不接受前端改写；
- 同一条结算单重复确认只保留第一次的结果，幂等返回；
- 争议标记记录操作人，重复标记不覆盖第一位操作人。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from app.store import store

MODULE = "settle"
REQUIRED_FIELDS = ["结算单号", "结算对象", "结算周期"]
# 登记结算单时允许一并落库的可选业务字段
OPTIONAL_FIELDS = ["对账员", "作业量", "应收金额", "已收金额", "开票状态", "结算状态"]
STATUS_ORDER = ["待核对", "核对中", "已确认", "已收款", "有争议"]
ACTION_RULES = {"发起核对": "核对中", "确认结算": "已收款", "标记争议": "有争议"}
DISPUTE_STATUS = "有争议"
SETTLED_STATUS = "已收款"

# 结算对象 → 归属对账员：登记时没显式指定对账员就按这张表归属
OBJECT_OWNERS: dict[str, str] = {
    "远东船务有限公司": "王敏",
    "海华货主集团": "李洁",
    "甬港集装箱物流": "王敏",
}


def _now() -> str:
    """收口时间生成，测试时可替换成固定时钟。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SettleService:
    clock: Callable[[], str] = staticmethod(_now)

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

    def owner_of(self, entry: dict[str, Any]) -> str:
        """结算单的归属对账员：优先取单据上的对账员，其次按结算对象查表。"""
        owner = str(entry.get("对账员") or "").strip()
        if owner:
            return owner
        return OBJECT_OWNERS.get(str(entry.get("结算对象") or "").strip(), "")

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if field in values and str(values.get(field) or "") != "":
                entry[field] = values.get(field)
        if not str(entry.get("对账员") or "").strip():
            entry["对账员"] = OBJECT_OWNERS.get(str(entry["结算对象"]).strip(), "")
        self._set_status(entry, STATUS_ORDER[0])
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(
        self,
        entry_id: int,
        action: str,
        operator: str = "",
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"结算单 {entry_id} 不存在或已归档"
        operator = str(operator or "").strip()
        if not operator:
            return None, "未识别到对账员身份，无法提交；当前账号只有查看权限"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于作业结算可执行范围"

        # 归属约束：只有结算对象对应的对账员本人能改动，其他人一律拒绝并说明原因。
        owner = self.owner_of(entry)
        if not owner:
            return None, "该结算单尚未指派归属对账员，暂不能提交动作，请先补登对账员"
        if operator != owner:
            return None, (
                f"结算对象「{entry.get('结算对象')}」归属对账员 {owner}，"
                f"{operator} 无权提交「{action}」，当前账号可查看但不能改动"
            )

        if action == "发起核对":
            return self._start_check(entry)
        if action == "确认结算":
            return self._confirm_receipt(entry, operator, values or {})
        return self._mark_dispute(entry, operator)

    def _set_status(self, entry: dict[str, Any], status: str) -> None:
        # 内部状态与列表展示的「结算状态」列必须同步，避免详情和列表对不上。
        entry["status"] = status
        entry["结算状态"] = status

    def _start_check(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        if entry.get("status") == DISPUTE_STATUS:
            return None, "结算单处于争议状态，需先处理争议后再发起核对"
        if entry.get("status") != "待核对":
            return entry, f"结算单已在「{entry.get('status')}」阶段，无需重复发起核对"
        self._set_status(entry, "核对中")
        entry["pending"] = True
        return entry, "结算单已发起核对"

    def _confirm_receipt(
        self, entry: dict[str, Any], operator: str, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        # 幂等：同一条结算单重复确认只保留第一次的结果，不产生新记录、不改金额。
        if entry.get("status") == SETTLED_STATUS:
            confirmer = entry.get("收款确认人") or "原对账员"
            return entry, (
                f"该结算单已于 {entry.get('收款确认时间', '—')} 由 {confirmer} 确认收款，"
                "请勿重复确认，已沿用原确认结果"
            )
        if entry.get("status") == DISPUTE_STATUS:
            return None, "结算单处于争议状态，争议处理完毕前不能确认收款"
        if entry.get("status") not in ("核对中", "已确认"):
            return None, f"结算单当前为「{entry.get('status')}」，请先发起核对再确认收款"

        # 金额由服务端按应收金额锁定，忽略请求里任何对已收金额的改写，防止越权篡改。
        submitted = values.get("已收金额")
        receivable = entry.get("应收金额")
        entry["已收金额"] = receivable
        self._set_status(entry, SETTLED_STATUS)
        entry["pending"] = False
        entry["abnormal"] = False
        entry["收款确认人"] = operator
        entry["收款确认时间"] = self.clock()
        message = f"收款已确认，已收金额锁定为 {receivable}"
        if submitted is not None and str(submitted) != str(receivable):
            message += "；提交金额与应收金额不一致，已按应收金额入账，未采纳提交值"
        return entry, message

    def _mark_dispute(self, entry: dict[str, Any], operator: str) -> tuple[dict[str, Any], str]:
        # 争议留痕：第一次标记记录操作人；重复标记保留首位操作人，避免争议记录被反复改写。
        if entry.get("争议标记人") and entry.get("status") == DISPUTE_STATUS:
            return entry, (
                f"该结算单已由 {entry['争议标记人']} 于 "
                f"{entry.get('争议标记时间', '—')} 标记争议，沿用原标记"
            )
        entry["争议标记人"] = operator
        entry["争议标记时间"] = self.clock()
        self._set_status(entry, DISPUTE_STATUS)
        entry["pending"] = True
        entry["abnormal"] = True
        return entry, f"结算单已标记争议，操作人 {entry['争议标记人']} 已留痕"
