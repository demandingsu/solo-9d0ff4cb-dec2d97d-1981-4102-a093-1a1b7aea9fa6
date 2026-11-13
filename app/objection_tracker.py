"""异议事项答复状态判定。

输入一次采集得到的原始记录（异议事项/异议答复/答复撤销），按下列规则输出
逐事项状态与证据：

1. 时点过滤：仅保留发布时间不晚于核查时点（含）的记录；晚于时点的记录
   一律不参与判定，也不作为证据。
2. 跨页同号冲突：同一记录编号在不同目录页出现且内容（标题/正文/引用/
   撤销目标/发布时间）不一致时，受影响事项标记 pending_verify。
3. 引用缺失：答复列明的异议编号、撤销指定的答复编号在采集记录中不存在时，
   相关事项标记 pending_verify，不猜测引用意图。
4. 答复仅覆盖列明的事项；一份答复可覆盖多项。
5. 撤销指定答复后该答复失效；事项仍有其他未撤销答复时继续算已答复。
6. 分页读取失败（completed=False）时只列出已采集证据，所有事项一律
   pending_verify，绝不断言已答复或未答复。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .objection_models import (
    ObjectionCollectResult,
    ObjectionItemView,
    ObjectionRecord,
    ObjectionRecordType as T,
    ObjectionStatus as S,
    ObjectionTrackSummary,
    RecordEvidence,
)

# 样例与接口统一使用的时间格式；解析失败则回退 ISO 8601
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_published(value: str) -> datetime:
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value)


def _record_fingerprint(r: ObjectionRecord) -> tuple:
    """用于跨页同号内容冲突比对的内容指纹。"""
    return (
        r.title,
        r.content,
        r.published_at,
        tuple(r.refutes),
        r.revokes_id,
        r.type,
        r.publication_no,
    )


def filter_by_checkpoint(
    records: list[ObjectionRecord], checkpoint: str
) -> list[ObjectionRecord]:
    cutoff = parse_published(checkpoint)
    return [r for r in records if parse_published(r.published_at) <= cutoff]


def build_tracking_summary(result: ObjectionCollectResult) -> ObjectionTrackSummary:
    records = filter_by_checkpoint(result.records, result.checkpoint)

    # ---- 1) 跨页同号冲突检测 ----
    by_id: dict[str, ObjectionRecord] = {}
    conflict_ids: set[str] = set()
    data_issues: list[str] = []

    for r in records:
        prev = by_id.get(r.record_id)
        if prev is None:
            by_id[r.record_id] = r
            continue
        if _record_fingerprint(prev) != _record_fingerprint(r):
            conflict_ids.add(r.record_id)
            data_issues.append(
                f"记录编号 {r.record_id} 在第 {prev.catalog_page}、{r.catalog_page} 页"
                "出现且内容冲突，相关事项需人工核实"
            )

    objections = [
        r for r in by_id.values() if r.type == T.OBJECTION
    ]
    replies = [r for r in by_id.values() if r.type == T.REPLY]
    revocations = [r for r in by_id.values() if r.type == T.REVOCATION]

    # ---- 2) 撤销关系：答复是否被任一（时点内的）撤销记录指定 ----
    revoked_reply_ids: set[str] = set()
    for rev in revocations:
        target = rev.revokes_id
        if target not in by_id or by_id[target].type != T.REPLY:
            data_issues.append(
                f"撤销记录 {rev.record_id} 指定的答复 {target} 不存在或不是异议答复，"
                "引用缺失，已忽略该撤销"
            )
            continue
        revoked_reply_ids.add(target)

    # ---- 3) 答复 -> 事项覆盖关系，并收集引用缺失 ----
    # 每个事项收集与其相关的全部证据
    evidence_by_objection: dict[str, list[RecordEvidence]] = defaultdict(list)
    # 事项编号 -> 该事项关联的、存在引用缺失的答复（答复本身可信度存疑）
    tainted_replies_by_objection: dict[str, set[str]] = defaultdict(set)
    pending_via_missing_ref: set[str] = set()

    for rep in replies:
        rep_valid = rep.record_id not in revoked_reply_ids
        invalid_reason = (
            f"答复已被撤销记录撤销：{_revokers_of(revocations, rep.record_id)}"
            if not rep_valid
            else None
        )
        if not rep.refutes:
            # 解析阶段已拦截，双保险
            data_issues.append(f"答复 {rep.record_id} 未列明任何答复事项，已忽略")
            continue
        reply_has_missing_ref = False
        for obj_id in rep.refutes:
            if obj_id not in by_id or by_id[obj_id].type != T.OBJECTION:
                data_issues.append(
                    f"答复 {rep.record_id} 引用的异议事项 {obj_id} 不存在，引用缺失"
                )
                pending_via_missing_ref.add(obj_id)
                reply_has_missing_ref = True
                continue
            evidence_by_objection[obj_id].append(
                RecordEvidence(
                    record_id=rep.record_id,
                    type=T.REPLY,
                    published_at=rep.published_at,
                    source_url=rep.source_url,
                    valid=rep_valid,
                    invalid_reason=invalid_reason,
                    refutes=list(rep.refutes),
                )
            )
        # 该答复存在缺失引用：其覆盖的所有现存事项都标为待核实（引用关系可信度存疑）
        if reply_has_missing_ref:
            for obj_id in rep.refutes:
                if obj_id in by_id and by_id[obj_id].type == T.OBJECTION:
                    tainted_replies_by_objection[obj_id].add(rep.record_id)

    # 撤销记录本身也作为证据挂到它所撤销答复覆盖的事项上，便于溯源
    for rev in revocations:
        target = rev.revokes_id
        if target not in by_id or by_id[target].type != T.REPLY:
            continue
        for obj_id in by_id[target].refutes:
            if obj_id in by_id and by_id[obj_id].type == T.OBJECTION:
                evidence_by_objection[obj_id].append(
                    RecordEvidence(
                        record_id=rev.record_id,
                        type=T.REVOCATION,
                        published_at=rev.published_at,
                        source_url=rev.source_url,
                        revokes_id=target,
                    )
                )

    # 与每个事项存在引用关系的答复/撤销编号，用于判断关联记录是否跨页冲突
    reply_ids_by_objection: dict[str, set[str]] = defaultdict(set)
    revocation_ids_by_objection: dict[str, set[str]] = defaultdict(set)
    for rep in replies:
        for obj_id in rep.refutes:
            reply_ids_by_objection[obj_id].add(rep.record_id)
    for rev in revocations:
        target = rev.revokes_id
        if target in by_id and by_id[target].type == T.REPLY:
            for obj_id in by_id[target].refutes:
                revocation_ids_by_objection[obj_id].add(rev.record_id)

    # ---- 4) 逐事项判定 ----
    items: list[ObjectionItemView] = []
    counts = {s.value: 0 for s in S}

    for obj in sorted(objections, key=lambda r: (r.published_at, r.record_id)):
        evidence = sorted(
            evidence_by_objection.get(obj.record_id, []),
            key=lambda e: (e.published_at, e.record_id),
        )
        valid_replies = [
            e.record_id
            for e in evidence
            if e.type == T.REPLY and e.valid
        ]
        issues: list[str] = []

        related_conflict = bool(
            reply_ids_by_objection[obj.record_id] & conflict_ids
            or revocation_ids_by_objection[obj.record_id] & conflict_ids
        )
        tainted = tainted_replies_by_objection.get(obj.record_id, set())

        if not result.completed:
            # 分页不完整：只列证据，不断言已/未答复
            status = S.PENDING_VERIFY
            reason = result.incomplete_reason or "分页读取未完成"
            issues.append(
                f"目录分页读取失败（{reason}），证据不完整，不做已答复/未答复断言"
            )
        elif obj.record_id in conflict_ids:
            status = S.PENDING_VERIFY
            issues.append("同一记录编号跨页内容冲突，需人工核实")
        elif related_conflict:
            status = S.PENDING_VERIFY
            issues.append("与本事项相关的答复或撤销记录存在跨页内容冲突，需人工核实")
        elif obj.record_id in pending_via_missing_ref or tainted:
            status = S.PENDING_VERIFY
            if tainted:
                issues.append(
                    "答复 "
                    + "、".join(sorted(tainted))
                    + " 同时引用了不存在的异议编号，引用缺失，需人工核实"
                )
            if obj.record_id in pending_via_missing_ref:
                issues.append("存在指向本事项编号但引用无法对应的数据，需人工核实")
        elif valid_replies:
            status = S.ANSWERED
        else:
            status = S.UNANSWERED
            if evidence:
                issues.append("已有答复记录但均被撤销，当前无有效答复")

        items.append(
            ObjectionItemView(
                record_id=obj.record_id,
                title=obj.title,
                published_at=obj.published_at,
                status=status,
                source_url=obj.source_url,
                valid_reply_ids=valid_replies,
                evidence=evidence,
                issues=issues,
            )
        )
        counts[status.value] += 1

    # 引用缺失指向的异议在本批记录中不存在：无法逐事项展示，仅保留全局问题
    for missing in sorted(pending_via_missing_ref):
        if missing not in by_id:
            data_issues.append(
                f"异议事项 {missing} 被答复引用但在采集记录中缺失，无法判定其状态"
            )

    return ObjectionTrackSummary(
        publication_no=result.publication_no,
        checkpoint=result.checkpoint,
        catalog_url=result.catalog_url,
        completed=result.completed,
        incomplete_reason=result.incomplete_reason,
        total_pages=result.total_pages,
        total_records=len(records),
        counts=counts,
        items=items,
        data_issues=data_issues,
    )


def _revokers_of(revocations: list[ObjectionRecord], reply_id: str) -> str:
    return "、".join(
        rev.record_id for rev in revocations if rev.revokes_id == reply_id
    )
