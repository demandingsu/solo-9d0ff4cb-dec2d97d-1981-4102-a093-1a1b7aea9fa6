"""异议追踪 API 集成测试：TestClient + 本地离线样例（MockTransport）。

完整走“分页抓取 -> 详情解析 -> 时点过滤/引用校验/撤销判定 -> 存储”流程，
并验证分页读取失败时只返回已采集证据、不做已/未答复断言。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

SITE = config.SAMPLE_DIR
OBJECTION_CATALOG = (
    "http://testserver/sample/objections/catalog/page_1.html"
)


def _sample_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if not path.startswith("/sample/"):
        return httpx.Response(404, text="not found")
    rel = path[len("/sample/"):]
    f = (SITE / rel).resolve()
    if not str(f).startswith(str(SITE.resolve())) or not f.is_file():
        return httpx.Response(404, text="not found")
    return httpx.Response(
        200,
        content=f.read_bytes(),
        headers={"content-type": "text/html; charset=utf-8"},
    )


_HANDLER = {"fn": _sample_handler}


def _dispatch(request: httpx.Request) -> httpx.Response:
    return _HANDLER["fn"](request)


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    # 公告与异议各自独立存储文件
    monkeypatch.setattr(config, "STORE_PATH", tmp_path / "store.json")
    monkeypatch.setattr(
        config, "OBJECTION_STORE_PATH", tmp_path / "objections.json"
    )
    from app import storage, objection_storage
    monkeypatch.setattr(storage, "store", storage.Store(config.STORE_PATH))
    monkeypatch.setattr(
        objection_storage,
        "objection_store",
        objection_storage.ObjectionStore(config.OBJECTION_STORE_PATH),
    )
    from app import main
    monkeypatch.setattr(main, "store", storage.Store(config.STORE_PATH))
    monkeypatch.setattr(
        main,
        "objection_store",
        objection_storage.ObjectionStore(config.OBJECTION_STORE_PATH),
    )

    _HANDLER["fn"] = _sample_handler
    transport = httpx.MockTransport(_dispatch)
    real_async_client = httpx.AsyncClient

    class PatchedAsyncClient(real_async_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)
    return TestClient(app)


def _track(client, **overrides):
    payload = {
        "publication_no": "GS-2026-007",
        "checkpoint": "2026-11-12 00:00:00",
        "catalog_url": OBJECTION_CATALOG,
    }
    payload.update(overrides)
    return client.post("/api/objections/track", json=payload)


def test_full_tracking_flow_and_sample_access(client):
    resp = _track(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "tracked"
    result = body["result"]
    assert result["completed"] is True
    assert result["total_pages"] == 3
    assert result["publication_no"] == "GS-2026-007"
    assert result["counts"] == {
        "answered": 3,
        "unanswered": 2,
        "pending_verify": 2,
    }

    items = {it["record_id"]: it for it in result["items"]}
    # 一份答复覆盖多项
    assert items["O001"]["status"] == "answered"
    assert items["O002"]["valid_reply_ids"] == ["R001"]
    # 唯一答复被撤销 -> 未答复（催办对象）
    assert items["O003"]["status"] == "unanswered"
    assert items["O003"]["valid_reply_ids"] == []
    # 撤销后仍有其他有效答复 -> 继续算已答复
    assert items["O008"]["status"] == "answered"
    assert items["O008"]["valid_reply_ids"] == ["R009"]
    # 无任何答复
    assert items["O007"]["status"] == "unanswered"
    # 跨页同号冲突
    assert items["O004"]["status"] == "pending_verify"
    # 答复含缺失引用
    assert items["O005"]["status"] == "pending_verify"

    # 每条事项与证据都带来源链接
    for item in result["items"]:
        assert item["source_url"].startswith(
            "http://testserver/sample/objections/detail/"
        )
        for ev in item["evidence"]:
            assert ev["source_url"].startswith(
                "http://testserver/sample/objections/detail/"
            )

    # 离线样例可直接访问（目录、普通详情、冲突版本详情）
    assert client.get("/sample/objections/catalog/page_1.html").status_code == 200
    assert client.get("/sample/objections/detail/O001.html").status_code == 200
    conflict = client.get("/sample/objections/detail/O004-conflict.html")
    assert conflict.status_code == 200
    assert "冲突版本" in conflict.text


def test_tracking_result_persisted_and_queryable(client):
    assert client.get("/api/objections").status_code == 404
    _track(client)
    summary = client.get("/api/objections").json()
    assert summary["publication_no"] == "GS-2026-007"
    raw = client.get("/api/objections/raw").json()
    assert raw["summary"]["total_records"] == summary["total_records"]
    # 存储文件为合法 JSON
    data = json.loads(
        Path(config.OBJECTION_STORE_PATH).read_text(encoding="utf-8")
    )
    assert data["summary"]["counts"]["unanswered"] == 2


def test_re_track_replaces_previous(client):
    r1 = _track(client)
    assert r1.json()["replaced_previous"] is False
    r2 = _track(client)
    assert r2.status_code == 200
    assert r2.json()["replaced_previous"] is True


def test_checkpoint_filters_future_records(client):
    # 时点提前到 11-04 00:00：R002(11-04 11:00) 尚未发布，O003 无答复
    resp = _track(client, checkpoint="2026-11-04 00:00:00")
    items = {it["record_id"]: it for it in resp.json()["result"]["items"]}
    assert set(items) == {"O001", "O002", "O003"}
    assert items["O003"]["status"] == "unanswered"
    assert items["O001"]["status"] == "answered"


def test_pagination_failure_returns_evidence_without_assertions(client):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        if "page_3" in request.url.path:
            raise httpx.ConnectError("simulated network outage")
        return _sample_handler(request)

    _HANDLER["fn"] = failing_handler
    try:
        resp = _track(client)
    finally:
        _HANDLER["fn"] = _sample_handler

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "incomplete"
    result = body["result"]
    assert result["completed"] is False
    assert "page_3" in (result["incomplete_reason"] or "")
    # 已采集到前两页证据
    assert result["total_pages"] == 2
    assert result["total_records"] >= 1
    # 不把任何事项断言为已答复或未答复
    assert result["counts"]["answered"] == 0
    assert result["counts"]["unanswered"] == 0
    assert result["counts"]["pending_verify"] == len(result["items"])
    for item in result["items"]:
        assert item["status"] == "pending_verify"
        assert any("不做已答复/未答复断言" in i for i in item["issues"])


def test_detail_page_failure_also_incomplete(client):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/detail/X001.html"):
            return httpx.Response(500, text="boom")
        return _sample_handler(request)

    _HANDLER["fn"] = failing_handler
    try:
        resp = _track(client)
    finally:
        _HANDLER["fn"] = _sample_handler

    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["completed"] is False
    assert result["counts"]["answered"] == 0
    assert result["counts"]["unanswered"] == 0
