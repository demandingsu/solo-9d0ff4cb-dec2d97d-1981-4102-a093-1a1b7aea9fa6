"""异议追踪判定逻辑单元测试（直接构造记录，不依赖网络与样例文件）。"""
from __future__ import annotations

from app.objection_models import (
    ObjectionCollectResult,
    ObjectionRecord,
    ObjectionRecordType as T,
    ObjectionStatus as S,
)
from app.objection_tracker import build_tracking_summary, filter_by_checkpoint

URL = "http://test/objection/detail/{}.html"
CHECKPOINT = "2026-11-20 00:00:00"


def rec(rid, rtype, published, refutes=None, revokes=None, page=1,
        title=None, content="", publication="GS-T-1"):
    return ObjectionRecord(
        record_id=rid,
        type=rtype,
        title=title or f"{rid}-title",
        publication_no=publication,
        published_at=published,
        content=content,
        refutes=refutes or [],
        revokes_id=revokes,
        source_url=URL.format(rid),
        catalog_page=page,
    )


def collect(records, completed=True, reason=None, checkpoint=CHECKPOINT):
    return ObjectionCollectResult(
        catalog_url="http://test/objection/catalog/page_1.html",
        publication_no="GS-T-1",
        checkpoint=checkpoint,
        total_pages=2,
        completed=completed,
        incomplete_reason=reason,
        collected_at="2026-11-21T00:00:00+00:00",
        records=records,
    )


def by_id(summary):
    return {it.record_id: it for it in summary.items}


def test_checkpoint_excludes_later_records():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("R1", T.REPLY, "2026-11-19 23:59:59", refutes=["O1"]),
        rec("R2", T.REPLY, "2026-11-20 00:00:01", refutes=["O1"]),  # 晚于核查时点
    ]
    kept = filter_by_checkpoint(records, CHECKPOINT)
    assert {r.record_id for r in kept} == {"O1", "R1"}

    summary = build_tracking_summary(collect(records))
    item = by_id(summary)["O1"]
    # R2 被时点过滤，R1 仍在 -> answered，证据中不得出现 R2
    assert item.status == S.ANSWERED
    assert item.valid_reply_ids == ["R1"]
    assert all(e.record_id != "R2" for e in item.evidence)


def test_single_reply_covers_multiple_objections():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("O2", T.OBJECTION, "2026-11-10 10:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1", "O2"]),
    ]
    items = by_id(build_tracking_summary(collect(records)))
    assert items["O1"].status == S.ANSWERED
    assert items["O2"].status == S.ANSWERED
    assert items["O1"].valid_reply_ids == ["R1"]
    # 证据均带来源链接
    assert items["O1"].evidence[0].source_url == URL.format("R1")


def test_reply_only_covers_listed_objections():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("O2", T.OBJECTION, "2026-11-10 10:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"]),
    ]
    items = by_id(build_tracking_summary(collect(records)))
    assert items["O1"].status == S.ANSWERED
    assert items["O2"].status == S.UNANSWERED
    assert items["O2"].evidence == []


def test_revocation_with_other_valid_reply_still_answered():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"]),
        rec("X1", T.REVOCATION, "2026-11-12 09:00", revokes="R1"),
        rec("R2", T.REPLY, "2026-11-13 09:00", refutes=["O1"]),
    ]
    item = by_id(build_tracking_summary(collect(records)))["O1"]
    assert item.status == S.ANSWERED
    assert item.valid_reply_ids == ["R2"]
    r1_ev = next(e for e in item.evidence if e.record_id == "R1")
    assert r1_ev.valid is False and "X1" in (r1_ev.invalid_reason or "")
    # 撤销记录也在证据链中可溯源
    assert any(e.record_id == "X1" and e.type == T.REVOCATION for e in item.evidence)


def test_revocation_of_only_reply_marks_unanswered():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"]),
        rec("X1", T.REVOCATION, "2026-11-12 09:00", revokes="R1"),
    ]
    item = by_id(build_tracking_summary(collect(records)))["O1"]
    assert item.status == S.UNANSWERED
    assert item.valid_reply_ids == []
    assert any("撤销" in i for i in item.issues)


def test_cross_page_same_id_content_conflict_pending_verify():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00", page=1, content="版本甲"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"], page=1),
        rec("O1", T.OBJECTION, "2026-11-15 09:00", page=3,
            title="O1-title-冲突", content="版本乙"),
    ]
    summary = build_tracking_summary(collect(records))
    item = by_id(summary)["O1"]
    assert item.status == S.PENDING_VERIFY
    assert any("冲突" in i for i in item.issues)
    assert any("O1" in i and "冲突" in i for i in summary.data_issues)


def test_duplicate_same_id_identical_content_is_not_conflict():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00", page=1),
        rec("O1", T.OBJECTION, "2026-11-10 09:00", page=2),  # 完全重复
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"]),
    ]
    item = by_id(build_tracking_summary(collect(records)))["O1"]
    assert item.status == S.ANSWERED


def test_missing_reply_reference_pending_verify():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1", "O999"]),
    ]
    summary = build_tracking_summary(collect(records))
    # O1 因关联答复含缺失引用 -> 待核实；O999 本身无记录，仅在全局问题中体现
    assert by_id(summary)["O1"].status == S.PENDING_VERIFY
    assert any("O999" in i for i in summary.data_issues)


def test_revocation_target_missing_is_reported():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"]),
        rec("X1", T.REVOCATION, "2026-11-12 09:00", revokes="R404"),
    ]
    summary = build_tracking_summary(collect(records))
    # 撤销目标缺失被忽略，R1 仍有效，O1 已答复；全局问题记录该异常
    assert by_id(summary)["O1"].status == S.ANSWERED
    assert any("R404" in i and "撤销" in i for i in summary.data_issues)


def test_incomplete_crawl_never_asserts_answered_or_unanswered():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"]),
        rec("O2", T.OBJECTION, "2026-11-12 09:00"),
    ]
    summary = build_tracking_summary(
        collect(records, completed=False, reason="抓取失败 http://x/page_3.html")
    )
    assert summary.completed is False
    assert summary.counts[S.ANSWERED.value] == 0
    assert summary.counts[S.UNANSWERED.value] == 0
    assert summary.counts[S.PENDING_VERIFY.value] == 2
    for item in summary.items:
        assert item.status == S.PENDING_VERIFY
        assert any("不做已答复/未答复断言" in i for i in item.issues)
    # 已采集证据仍然完整列出
    o1 = by_id(summary)["O1"]
    assert any(e.record_id == "R1" for e in o1.evidence)


def test_counts_and_evidence_links_present():
    records = [
        rec("O1", T.OBJECTION, "2026-11-10 09:00"),
        rec("O2", T.OBJECTION, "2026-11-10 10:00"),
        rec("R1", T.REPLY, "2026-11-11 09:00", refutes=["O1"]),
    ]
    summary = build_tracking_summary(collect(records))
    assert summary.counts == {
        S.ANSWERED.value: 1,
        S.UNANSWERED.value: 1,
        S.PENDING_VERIFY.value: 0,
    }
    for item in summary.items:
        assert item.source_url.startswith("http://test/")
        for e in item.evidence:
            assert e.source_url.startswith("http://test/")
