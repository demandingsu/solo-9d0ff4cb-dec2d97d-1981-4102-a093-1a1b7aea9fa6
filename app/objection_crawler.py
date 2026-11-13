"""异议记录采集器：沿目录分页抓取详情并解析。

与公告采集器的关键区别：任一目录页或详情页读取失败时**不丢弃已采集证据**，
返回 completed=False 的部分结果（含已成功页面与记录、失败原因与失败发生页），
由上层保证“不把任何事项断言为已答复或未答复”。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from . import config
from .objection_models import ObjectionCollectResult, ObjectionRecord
from .objection_parser import (
    ObjectionParseError,
    parse_objection_catalog,
    parse_objection_detail,
)


class ObjectionCrawler:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ObjectionParseError(f"抓取失败 {url}: {exc}") from exc
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise ObjectionParseError(
                f"响应不是 HTML {url} (content-type={ctype})"
            )
        return resp.text

    async def _fetch_detail(
        self, client: httpx.AsyncClient, url: str, catalog_page: int
    ) -> ObjectionRecord:
        async with self._sem:
            html_text = await self._get(client, url)
        return parse_objection_detail(html_text, url, catalog_page)

    async def collect(
        self,
        catalog_url: str,
        publication_no: str,
        checkpoint: str,
    ) -> ObjectionCollectResult:
        """沿分页抓取全部异议记录。

        成功 -> completed=True；任一页面失败 -> completed=False，
        records 保留此前已成功解析的全部记录，incomplete_reason 注明失败点。
        """
        records: list[ObjectionRecord] = []
        incomplete_reason: str | None = None
        pages = 0
        seen_pages: set[str] = set()
        page_url: str | None = catalog_url

        async with httpx.AsyncClient(
            timeout=config.REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": config.USER_AGENT},
        ) as client:
            while page_url is not None and incomplete_reason is None:
                if page_url in seen_pages:
                    incomplete_reason = f"目录分页出现循环: {page_url}"
                    break
                seen_pages.add(page_url)
                if pages >= config.MAX_PAGES:
                    incomplete_reason = (
                        f"目录页数达到上限 {config.MAX_PAGES}，疑似分页异常"
                    )
                    break

                try:
                    html_text = await self._get(client, page_url)
                    detail_urls, next_url = parse_objection_catalog(
                        html_text, page_url
                    )
                except ObjectionParseError as exc:
                    incomplete_reason = str(exc)
                    break
                # 仅成功读取并解析的目录页计入页数（失败页不计为已采集证据）
                pages += 1

                # 详情页并发抓取；任一失败即停止翻页，已完成的记录仍保留
                tasks = [
                    asyncio.create_task(
                        self._fetch_detail(client, u, pages)
                    )
                    for u in detail_urls
                ]
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_EXCEPTION
                )
                page_failed = False
                for fut in done:
                    exc = fut.exception()
                    if exc is not None:
                        incomplete_reason = str(exc)
                        page_failed = True
                    else:
                        records.append(fut.result())
                if page_failed:
                    # 未完成的任务不再等待：本页证据不完整，整体按未完成处理
                    for fut in pending:
                        fut.cancel()
                    break
                page_url = next_url

        return ObjectionCollectResult(
            catalog_url=catalog_url,
            publication_no=publication_no,
            checkpoint=checkpoint,
            total_pages=pages,
            completed=incomplete_reason is None,
            incomplete_reason=incomplete_reason,
            collected_at=self.now_iso(),
            records=records,
        )

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
