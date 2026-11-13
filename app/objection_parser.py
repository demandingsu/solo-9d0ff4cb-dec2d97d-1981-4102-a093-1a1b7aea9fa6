"""异议记录 HTML 解析器，针对与内置异议样例结构一致的页面。

目录页约定（与公告目录同构，便于复用分页习惯）：
  - 每条详情链接位于 ul.objection-catalog 的 li 内
  - 下一页：a[rel=next]，无该链接即末页
详情页约定：
  - table#objection-meta 内 th/td 键值对，字段为
    记录编号、公示编号、记录类型（异议事项/异议答复/答复撤销）、标题、
    发布时间、答复事项（答复必填，多个编号以中文顿号/逗号/分号/空白分隔）、
    撤销答复（撤销必填）
  - 正文摘要位于 div#objection-body
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .objection_models import ObjectionRecord, ObjectionRecordType

VALID_TYPES = {t.value for t in ObjectionRecordType}

# 答复事项字段中编号分隔符：顿号、逗号、分号、空白
_SPLIT_RE = re.compile(r"[、,，;；\s]+")
# 编号形如字母/数字/连字符；剥离单元格内可能附带的中文说明或括号文字
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_]*")


class ObjectionParseError(Exception):
    """页面结构不符合约定时抛出。"""


def _meta_table(soup: BeautifulSoup) -> dict[str, str]:
    table = soup.find("table", id="objection-meta")
    if table is None:
        raise ObjectionParseError("详情页缺少 table#objection-meta")
    meta: dict[str, str] = {}
    for tr in table.find_all("tr"):
        th, td = tr.find("th"), tr.find("td")
        if th is None or td is None:
            continue
        meta[th.get_text(strip=True)] = td.get_text(strip=True)
    return meta


def _split_ids(raw: str) -> list[str]:
    ids: list[str] = []
    for part in _SPLIT_RE.split(raw.strip()):
        ids.extend(_ID_RE.findall(part))
    # 去重并保序
    return list(dict.fromkeys(ids))


def parse_objection_detail(html_text: str, url: str, catalog_page: int) -> ObjectionRecord:
    soup = BeautifulSoup(html_text, "html.parser")
    meta = _meta_table(soup)

    required = ["记录编号", "公示编号", "记录类型", "标题", "发布时间"]
    missing = [k for k in required if not meta.get(k)]
    if missing:
        raise ObjectionParseError(f"详情页 {url} 缺少必填字段: {', '.join(missing)}")

    rtype = meta["记录类型"]
    if rtype not in VALID_TYPES:
        raise ObjectionParseError(f"详情页 {url} 记录类型无法识别: {rtype}")

    body_node = soup.find("div", id="objection-body")
    content = body_node.get_text(strip=True) if body_node is not None else ""

    refutes_raw = meta.get("答复事项") or ""
    revokes = meta.get("撤销答复") or None
    refutes = _split_ids(refutes_raw)

    if rtype == ObjectionRecordType.REPLY.value and not refutes:
        raise ObjectionParseError(f"异议答复 {url} 缺少答复事项")
    if rtype == ObjectionRecordType.REVOCATION.value and not revokes:
        raise ObjectionParseError(f"答复撤销 {url} 缺少撤销答复目标")

    return ObjectionRecord(
        record_id=meta["记录编号"],
        type=ObjectionRecordType(rtype),
        title=meta["标题"],
        publication_no=meta["公示编号"],
        published_at=meta["发布时间"],
        content=content,
        refutes=refutes,
        revokes_id=revokes,
        source_url=url,
        catalog_page=catalog_page,
    )


def parse_objection_catalog(
    html_text: str, url: str
) -> tuple[list[str], Optional[str]]:
    """返回 (详情页绝对 URL 列表, 下一页绝对 URL 或 None)。"""
    soup = BeautifulSoup(html_text, "html.parser")
    catalog = soup.find("ul", class_="objection-catalog")
    if catalog is None:
        raise ObjectionParseError(f"目录页 {url} 缺少 ul.objection-catalog")

    detail_urls: list[str] = []
    for a in catalog.find_all("a", href=True):
        detail_urls.append(urljoin(url, a["href"]))
    if not detail_urls:
        raise ObjectionParseError(f"目录页 {url} 未包含任何详情链接")

    next_page = None
    nxt = soup.find("a", rel=True, href=True)
    if nxt is not None and "next" in (nxt.get("rel") or []):
        next_page = urljoin(url, nxt["href"])
    return detail_urls, next_page
