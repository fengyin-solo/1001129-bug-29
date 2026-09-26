"""作业结算接口：维护结算单，覆盖发起核对、确认结算、标记争议等动作。

写操作必须带操作账号（operator），归属与权限由 SettleService 按结算对象的
对账员名册校验；其他账号调用只会拿到带原因的拒绝结果，不会改动任何数据。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.settle import SettleService

router = APIRouter(prefix="/api/settle", tags=["作业结算"])

service = SettleService()

LIST_FIELDS = ["结算单号", "结算对象", "对账员", "结算周期", "作业量", "应收金额", "已收金额", "开票状态", "结算状态"]
STATUSES = ["待核对", "核对中", "已确认", "已收款", "有争议"]


@router.get("/accounts", response_model=dict)
def list_accounts() -> dict[str, Any]:
    """返回可切换账号与结算对象归属名册，供前端控制只读/可操作视角。"""
    return service.roster()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按结算单号检索"),
    status: str | None = Query(default=None, description="待核对、核对中、已确认、已收款、有争议"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按结算单号与状态过滤作业结算列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出作业结算清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "settle", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条结算单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"结算单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条结算单，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="结算单已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条结算单执行发起核对、确认结算、标记争议。

    归属不符、重复确认、非法金额都会被拦下并在 message 中说明原因，
    返回 ok=False 而不是静默成功；服务端保证拒绝时不改动任何字段。
    """
    action = str(payload.values.get("action") or "").strip()
    operator = str(payload.values.get("operator") or "").strip()
    extra = {
        key: value
        for key, value in payload.values.items()
        if key not in ("action", "operator")
    }
    entry, message = service.run_action(entry_id, action, operator, extra)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)

