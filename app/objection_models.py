"""异议追踪领域模型。

三类记录（均带记录编号、发布时间、来源链接）：
  - 异议事项 ObjectionRecord      记录编号 O...
  - 异议答复 ReplyRecord          记录编号 R...，refutes 列明其答复的异议编号（可多项）
  - 答复撤销 RevocationRecord     记录编号 X...，revokes_id 指定被撤销的答复编号

事项判定状态：
  answered          至少一份未被撤销的有效答复列明该事项
  unanswered        不存在任何有效答复
  pending_verify    跨页同号内容冲突、或引用缺失等数据问题，无法可靠判定
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ObjectionRecordType(str, Enum):
    OBJECTION = "异议事项"
    REPLY = "异议答复"
    REVOCATION = "答复撤销"


class ObjectionStatus(str, Enum):
    ANSWERED = "answered"          # 已有有效答复
    UNANSWERED = "unanswered"      # 尚无有效答复，需要催办
    PENDING_VERIFY = "pending_verify"  # 数据存疑，需人工核实，不做已/未答复断言


# ---- 抓取/解析后的原始记录（保留引用关系与来源，不做判定） ----

class ObjectionRecord(BaseModel):
    record_id: str
    type: ObjectionRecordType
    title: str
    publication_no: str                 # 所属结果公示编号
    published_at: str                   # 发布时间（页面原文，比较时按约定格式解析）
    content: str = ""                   # 正文摘要，用于跨页同号冲突比对
    refutes: list[str] = Field(default_factory=list)   # 答复所覆盖的异议记录编号
    revokes_id: Optional[str] = None    # 撤销所针对的答复记录编号
    source_url: str
    catalog_page: int = 1               # 记录出现在第几页目录（冲突定位用）


class ObjectionCollectResult(BaseModel):
    """一次分页采集的完整结果。completed=False 表示分页读取失败，
    此时 records 仅含已成功采集的证据，incomplete_reason 说明失败点。"""

    catalog_url: str
    publication_no: str
    checkpoint: str
    total_pages: int
    completed: bool
    incomplete_reason: Optional[str] = None
    collected_at: str
    records: list[ObjectionRecord]


# ---- 判定侧证据与视图模型 ----

class RecordEvidence(BaseModel):
    """一条与事项相关的记录证据（答复或撤销）。"""

    record_id: str
    type: ObjectionRecordType
    published_at: str
    source_url: str
    valid: bool = True                  # 答复是否未被撤销
    invalid_reason: Optional[str] = None
    refutes: list[str] = Field(default_factory=list)
    revokes_id: Optional[str] = None


class ObjectionItemView(BaseModel):
    """逐事项判定结果。"""

    record_id: str
    title: str
    published_at: str
    status: ObjectionStatus
    source_url: str
    valid_reply_ids: list[str] = Field(default_factory=list)
    evidence: list[RecordEvidence] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)  # 待核实原因（pending_verify 时非空）


class ObjectionTrackSummary(BaseModel):
    publication_no: str
    checkpoint: str
    catalog_url: str
    completed: bool                                  # 分页是否完整读取
    incomplete_reason: Optional[str] = None
    total_pages: int
    total_records: int
    counts: dict[str, int]                           # answered/unanswered/pending_verify
    items: list[ObjectionItemView]
    data_issues: list[str] = Field(default_factory=list)  # 全局数据问题（引用缺失等）


class ObjectionTrackRequest(BaseModel):
    publication_no: str = Field(..., description="结果公示编号，如 GS-2026-007")
    checkpoint: str = Field(
        ...,
        description="核查时点（含），格式 YYYY-MM-DD HH:MM:SS；仅统计发布时间不晚于该时点的记录",
    )
    catalog_url: HttpUrl = Field(..., description="异议记录目录第一页 URL")

    @field_validator("checkpoint")
    @classmethod
    def _check_checkpoint(cls, v: str) -> str:
        # 惰性导入，避免与 objection_tracker 形成循环导入
        from .objection_tracker import parse_published

        try:
            parse_published(v)
        except ValueError as exc:
            raise ValueError(
                "核查时点格式无法识别，请使用 YYYY-MM-DD HH:MM:SS"
            ) from exc
        return v

    @field_validator("publication_no")
    @classmethod
    def _check_publication_no(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("公示编号不能为空")
        return v.strip()


class ObjectionTrackResponse(BaseModel):
    status: str                         # tracked | incomplete
    result: ObjectionTrackSummary
    collected_at: str
    replaced_previous: bool
