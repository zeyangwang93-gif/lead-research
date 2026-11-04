"""模块 A 第一步：根据 icp.json 自动生成搜索词 → 搜索 → 按域名去重 → 过滤 → 输出候选公司列表。
用法：python discover.py --icp icp.json"""
import argparse
import sys
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

import config
import llm
import search as search_mod

OUT_DIR = Path("data/discovered")
EVAL_SET = Path("data/eval_set.csv")

# 明显不是候选公司的域名（匹配该域名本身及其子域名）。要增减直接改这里。
BLOCKED_DOMAINS = [
    "amazon.com", "amazon.de", "amazon.co.uk", "amazon.nl", "amazon.pl", "ebay.com", "ebay.de",
    "ebay.co.uk", "ebay.nl", "aliexpress.com", "alibaba.com", "1688.com", "made-in-china.com",
    "globalsources.com", "dhgate.com", "temu.com", "wish.com", "etsy.com",
    "wikipedia.org", "youtube.com", "facebook.com", "instagram.com", "linkedin.com",
    "pinterest.com", "reddit.com", "trustpilot.com", "twitter.com", "x.com", "tiktok.com",
    "quora.com", "medium.com", "google.com", "bing.com", "yelp.com", "tripadvisor.com",
    "allegro.pl", "ceneo.pl", "bol.com", "idealo.de", "kelkoo.com", "geizhals.de",
    "europages.com", "kompass.com", "indiamart.com", "tradekey.com", "ec21.com",
]


class Query(BaseModel):
    query: str
    language: str  # German / English / Dutch / Polish
    angle: str     # 客户类型 + 叫法，例如 "importer / tent"


class QueryList(BaseModel):
    queries: list[Query]


def generate_queries(icp: dict, n: int) -> tuple[list[Query], int, int]:
    system = "你是外贸获客研究员，擅长为海外买家设计搜索引擎关键词。"
    user = (
        f"客户画像（JSON）：\n{json.dumps(icp, ensure_ascii=False, indent=2)}\n\n"
        f"请生成 {n} 个搜索词，用来在搜索引擎里找到符合画像的海外买家公司（进口商、批发商、品牌商、零售商）官网。\n"
        "要求：\n"
        "1. 覆盖目标市场的各国语言：德语、英语、荷兰语、波兰语，各语言数量大致均衡。\n"
        "2. 覆盖不同客户类型：进口商、批发商、品牌商、零售商。\n"
        "3. 覆盖不同叫法：帐篷（tent/Zelt/tent/namiot）、露营装备、户外装备等；可带国家或城市名。\n"
        "4. 搜索词用对应语言书写，像当地买家公司网站上会出现的说法；不要出现“制造商/factory/supplier China”之类会引向竞争对手的词。\n"
        "5. language 填 German/English/Dutch/Polish；angle 填客户类型和叫法，如 \"importer / tent\"。"
    )
    r = llm.call_structured(system, user, "submit_queries", "提交搜索词列表",
                            QueryList.model_json_schema(), max_tokens=3000)
    qs = QueryList.model_validate(r.data).queries
    return qs[:n], r.input_tokens, r.output_tokens


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_blocked(domain: str) -> bool:
    return any(domain == b or domain.endswith("." + b) for b in BLOCKED_DOMAINS)


def load_eval_labels() -> dict[str, str]:
    if not EVAL_SET.exists():
        return {}
    with EVAL_SET.open(encoding="utf-8-sig", newline="") as f:
        return {domain_of(row["url"]): row["label"] for row in csv.DictReader(f) if row.get("url")}


def write_csv(path: Path, fields: list[str], rows: list[dict]):
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig 方便 Excel 打开
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 gbk，打印德语/波兰语会报错
    ap = argparse.ArgumentParser()
    ap.add_argument("--icp", default="icp.json")
    args = ap.parse_args()
    icp = json.loads(Path(args.icp).read_text(encoding="utf-8"))

    queries, in_tok, out_tok = generate_queries(icp, config.DISCOVER_NUM_QUERIES)
    print(f"生成搜索词 {len(queries)} 个（tokens: in={in_tok}, out={out_tok}）")

    domains: dict[str, dict] = {}   # 每个域名：首个命中的结果 + 命中它的搜索词
    seen_domains: set[str] = set()
    query_rows = []
    raw_total = search_calls = 0
    for i, q in enumerate(queries, 1):
        try:
            results = search_mod.search(q.query, config.SEARCH_MAX_RESULTS)
        except Exception as e:
            print(f"[{i}/{len(queries)}] 搜索失败：{q.query} -> {e}")
            results = []
        search_calls += 1
        raw_total += len(results)
        new_domains = 0
        q_domains = set()
        for r in results:
            d = domain_of(r["url"])
            if not d or d in q_domains:
                continue
            q_domains.add(d)
            if is_blocked(d):
                seen_domains.add(d)
                continue
            if d not in domains:
                domains[d] = {"title": r["title"], "snippet": r["snippet"], "queries": []}
                if d not in seen_domains:
                    new_domains += 1
            domains[d]["queries"].append(q.query)
            seen_domains.add(d)
        query_rows.append({"query": q.query, "language": q.language, "angle": q.angle,
                           "results": len(results), "new_domains_after_filter": new_domains})
        print(f"[{i}/{len(queries)}] {q.language:8} {q.query} -> {len(results)} 条，新增候选 {new_domains}")
        time.sleep(0.2)

    labels = load_eval_labels()
    rows = []
    for d, info in sorted(domains.items(), key=lambda kv: -len(kv[1]["queries"])):
        rows.append({
            "domain": d, "homepage": f"https://{d}/", "title": info["title"],
            "snippet": info["snippet"].replace("\n", " "), "hit_count": len(info["queries"]),
            "source_queries": " | ".join(info["queries"]), "in_eval_set": labels.get(d, ""),
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    write_csv(OUT_DIR / f"queries_{ts}.csv",
              ["query", "language", "angle", "results", "new_domains_after_filter"], query_rows)
    write_csv(OUT_DIR / f"candidates_{ts}.csv",
              ["domain", "homepage", "title", "snippet", "hit_count", "source_queries", "in_eval_set"], rows)

    blocked = len([d for d in seen_domains if is_blocked(d)])
    print(f"\n搜索调用 {search_calls} 次；原始结果 {raw_total} 条；去重后域名 {len(seen_domains)} 个；"
          f"过滤 {blocked} 个；候选 {len(rows)} 个")
    hit = [r["domain"] for r in rows if r["in_eval_set"] != ""]
    print(f"命中标注集 {len(hit)} 个：{', '.join(hit)}")
    print(f"输出：{OUT_DIR}/queries_{ts}.csv, candidates_{ts}.csv")


if __name__ == "__main__":
    main()
