#!/usr/bin/env python3
"""生成可离线访问的分页 HTML 异议记录样例站点。

目录结构（全部为相对链接，无任何外部资源）：
  sample_site/objections/index.html          -> catalog/page_1.html 的副本入口
  sample_site/objections/catalog/page_{1..3}.html
  sample_site/objections/detail/<记录ID>.html

详情页统一结构：
  <table id="objection-meta"> 中以 <th>字段名</th><td>值</td> 给出：
    记录编号 / 公示编号 / 记录类型（异议事项/异议答复/答复撤销）/ 标题 /
    发布时间 / 答复事项（答复必填，多编号以顿号分隔）/ 撤销答复（撤销必填）
  正文摘要位于 <div id="objection-body">
解析器 app/objection_parser.py 依赖上述结构。

样例覆盖场景（公示编号 GS-2026-007）：
  O001 一份答复覆盖两项（O001+O002），其中 O001 另有答复被撤销但仍有有效答复 -> answered
  O002 与 O001 共享多事项答复 -> answered
  O003 唯一答复被撤销 -> unanswered（催办对象）
  O004 跨页同号内容冲突（page_1 与 page_3 各一份 O004，内容不同）-> pending_verify
  O005 答复引用了不存在的异议 O999；同时 O006 被答复引用但自身记录缺失
  O007 完全无答复 -> unanswered（催办对象）
  O008 答复 R008 被撤销后，仍有第二份有效答复 R009 -> answered
"""
from __future__ import annotations

import html
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "sample_site", "objections")
CATALOG_DIR = os.path.join(SITE, "catalog")
DETAIL_DIR = os.path.join(SITE, "detail")

PUBLICATION_NO = "GS-2026-007"

TYPE_OBJECTION = "异议事项"
TYPE_REPLY = "异议答复"
TYPE_REVOCATION = "答复撤销"


@dataclass
class Rec:
    rid: str
    rtype: str
    title: str
    published: str
    body: str
    refutes: list[str] = field(default_factory=list)   # 答复覆盖的异议编号
    revokes: str | None = None                          # 撤销针对的答复编号
    # catalog_page: 该条目出现在第几页目录（用于构造跨页同号冲突）
    catalog_page: int = 1
    # 详情文件名；默认与记录编号同名，跨页同号冲突副本使用独立文件
    detail_file: str | None = None

    @property
    def file_stem(self) -> str:
        return self.detail_file or self.rid


# 记录按目录页排列；page_1 放 6 条，page_2 放 6 条，page_3 放其余 + 冲突副本
RECORDS: list[Rec] = [
    # ---- 第 1 页 ----
    Rec("O001", TYPE_OBJECTION, "关于评标办法评分细则的异议",
        "2026-11-02 09:00", "异议人认为技术评分细则中类似业绩认定标准不明确。",
        catalog_page=1),
    Rec("O002", TYPE_OBJECTION, "关于开标程序的异议",
        "2026-11-02 10:30", "异议人认为开标记录唱标环节遗漏一家投标人报价。",
        catalog_page=1),
    Rec("R001", TYPE_REPLY, "关于 O001、O002 两项异议的合并答复",
        "2026-11-03 15:00", "经核查，评分细则已补正说明并重新公示；唱标遗漏系记录笔误，已更正存档。",
        refutes=["O001", "O002"], catalog_page=1),
    Rec("O003", TYPE_OBJECTION, "关于中标候选人业绩材料的异议",
        "2026-11-03 09:00", "异议人称第一中标候选人某项业绩合同金额存疑。",
        catalog_page=1),
    Rec("R002", TYPE_REPLY, "关于 O003 异议的答复（后被撤销）",
        "2026-11-04 11:00", "初步答复称业绩材料齐全。",
        refutes=["O003"], catalog_page=1),
    Rec("X001", TYPE_REVOCATION, "撤销对 O003 的答复 R002",
        "2026-11-05 09:30", "因答复依据材料待补充核验，撤销 R002 答复，待重新核查后答复。",
        revokes="R002", catalog_page=1),

    # ---- 第 2 页 ----
    Rec("O004", TYPE_OBJECTION, "关于评标委员会组成的异议",
        "2026-11-04 09:00", "异议人称某评标专家与投标人存在关联关系（原始版本）。",
        catalog_page=2),
    Rec("R003", TYPE_REPLY, "关于 O004 异议的答复",
        "2026-11-05 14:00", "经调取专家回避声明，未发现应回避未回避情形。",
        refutes=["O004"], catalog_page=2),
    Rec("O005", TYPE_OBJECTION, "关于招标文件澄清期限的异议",
        "2026-11-05 09:00", "异议人称澄清答复发出距投标截止不足法定时间。",
        catalog_page=2),
    Rec("R004", TYPE_REPLY, "关于 O005 及 O999 的答复（含缺失引用）",
        "2026-11-06 10:00", "澄清期限经核满足要求；另对编号 O999 的材料一并说明。",
        refutes=["O005", "O999"], catalog_page=2),
    Rec("R005", TYPE_REPLY, "针对缺失异议 O006 的答复（O006 记录本身缺失）",
        "2026-11-06 15:00", "答复称 O006 所反映问题不成立，但目录中不存在 O006 事项记录。",
        refutes=["O006"], catalog_page=2),
    Rec("O007", TYPE_OBJECTION, "关于投标保证金退还的异议",
        "2026-11-06 09:30", "异议人称未中标人保证金逾期未退还。",
        catalog_page=2),

    # ---- 第 3 页 ----
    Rec("O008", TYPE_OBJECTION, "关于工程量清单偏差的异议",
        "2026-11-07 09:00", "异议人称分部分项工程量清单两处计量单位与图纸不符。",
        catalog_page=3),
    Rec("R008", TYPE_REPLY, "关于 O008 异议的第一份答复（后被撤销）",
        "2026-11-08 09:00", "初次答复认为不影响报价。",
        refutes=["O008"], catalog_page=3),
    Rec("X002", TYPE_REVOCATION, "撤销对 O008 的答复 R008",
        "2026-11-08 15:00", "因清单复核未完成，撤销 R008。",
        revokes="R008", catalog_page=3),
    Rec("R009", TYPE_REPLY, "关于 O008 异议的重新答复",
        "2026-11-09 10:00", "复核后确有单位偏差，已发布补遗并顺延投标截止时间。",
        refutes=["O008"], catalog_page=3),
    # 跨页同号冲突：O004 在第 3 页再次出现，正文、标题与发布时间均不同，
    # 且目录指向独立详情文件 O004-conflict.html（记录编号同为 O004）
    Rec("O004", TYPE_OBJECTION, "关于评标委员会组成的异议（冲突版本）",
        "2026-11-10 09:00", "异议人称评标委员会人数不符合招标文件约定（与第 2 页版本内容冲突）。",
        catalog_page=3, detail_file="O004-conflict"),
]

PAGE_SIZE = 6

CSS = """
body { font-family: "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; margin: 24px; color: #222; }
h1 { font-size: 20px; } table { border-collapse: collapse; width: 760px; margin-top: 12px; }
th, td { border: 1px solid #999; padding: 6px 10px; text-align: left; font-size: 14px; }
th { background: #f0f3f7; width: 130px; }
ul.objection-catalog li { margin: 6px 0; } .pager a { margin-right: 12px; }
.meta-id { color: #666; font-size: 13px; }
#objection-body { width: 760px; margin-top: 14px; line-height: 1.7; }
"""


def detail_html(r: Rec) -> str:
    rows = [
        ("记录编号", r.rid),
        ("公示编号", PUBLICATION_NO),
        ("记录类型", r.rtype),
        ("标题", r.title),
        ("发布时间", r.published),
    ]
    if r.refutes:
        links = "、".join(
            f'<a href="{i}.html">{html.escape(i)}</a>' for i in r.refutes
        )
        rows.append(("答复事项", links))
    if r.revokes is not None:
        rows.append(("撤销答复", f'<a href="{r.revokes}.html">{html.escape(r.revokes)}</a>'))
    body = "".join(
        f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in rows
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(r.title)}</title>
<style>{CSS}</style>
</head>
<body>
<h1>{html.escape(r.title)}</h1>
<p class="meta-id">记录编号：{html.escape(r.rid)} ｜ 公示编号：{PUBLICATION_NO}</p>
<table id="objection-meta">
{body}
</table>
<div id="objection-body">{html.escape(r.body)}</div>
<p>本页面为离线样例数据，用于测试异议事项答复追踪逻辑。</p>
<p><a href="../catalog/page_1.html">返回异议记录目录</a></p>
</body>
</html>
"""


def catalog_html(page: int, total_pages: int, depth: int = 1) -> str:
    """depth=1 页面位于 catalog/ 下（链接带 ../）；depth=0 位于站点根。"""
    prefix = "../" if depth else ""
    start = (page - 1) * PAGE_SIZE
    items = RECORDS[start:start + PAGE_SIZE]
    lis = []
    for r in items:
        lis.append(
            '<li><span class="meta-id">'
            f'[{html.escape(r.rid)}] {html.escape(r.published)}｜{html.escape(r.rtype)}</span> '
            f'<a href="{prefix}detail/{r.file_stem}.html">{html.escape(r.title)}</a></li>'
        )
    pager = []
    for p in range(1, total_pages + 1):
        if p == page:
            pager.append(f"<strong>第 {p} 页</strong>")
        else:
            pager.append(f'<a href="{prefix}catalog/page_{p}.html">第 {p} 页</a>')
    if page < total_pages:
        pager.append(
            f'<a rel="next" href="{prefix}catalog/page_{page + 1}.html">下一页 &raquo;</a>'
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>结果公示异议记录目录（{PUBLICATION_NO}） - 第 {page} 页</title>
<style>{CSS}</style>
</head>
<body>
<h1>结果公示 {PUBLICATION_NO} 异议记录目录（第 {page} / {total_pages} 页）</h1>
<ul class="objection-catalog">
{''.join(lis)}
</ul>
<p class="pager">{''.join(pager)}</p>
</body>
</html>
"""


def main() -> None:
    os.makedirs(CATALOG_DIR, exist_ok=True)
    os.makedirs(DETAIL_DIR, exist_ok=True)
    total_pages = (len(RECORDS) + PAGE_SIZE - 1) // PAGE_SIZE

    # 详情页按详情文件名去重输出；O004 冲突副本写入独立文件 O004-conflict.html
    seen: set[str] = set()
    for r in RECORDS:
        if r.file_stem in seen:
            continue
        seen.add(r.file_stem)
        with open(
            os.path.join(DETAIL_DIR, f"{r.file_stem}.html"), "w", encoding="utf-8"
        ) as f:
            f.write(detail_html(r))

    for p in range(1, total_pages + 1):
        with open(os.path.join(CATALOG_DIR, f"page_{p}.html"), "w", encoding="utf-8") as f:
            f.write(catalog_html(p, total_pages, depth=1))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(catalog_html(1, total_pages, depth=0))
    print(
        f"generated {len(seen)} detail pages, {total_pages} catalog pages "
        f"({len(RECORDS)} catalog entries) under {SITE}"
    )


if __name__ == "__main__":
    main()
