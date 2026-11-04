"""模块 A 第一步：根据 icp.json 自动生成搜索词 → 搜索 → 按域名去重 → 过滤 → 输出候选公司列表。
用法：python discover.py --icp icp.json"""
import argparse
import sys
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel

import config
import llm
import search as search_mod
from research import load_icp, research_company
from schemas import ICP

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
    "rocketreach.co", "tracxn.com", "leadiq.com", "zoominfo.com", "cbinsights.com", "mapquest.com",
    "bbb.org", "pricespy.co.uk", "pricerunner.com", "finance.yahoo.com", "cnbc.com",
    "businessinsider.com", "which.co.uk",
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


def run_search(icp: dict, ts: str) -> dict:
    """第一步：生成搜索词、搜索、去重、过滤，写 queries/candidates 文件。"""
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
    return {"rows": rows, "search_calls": search_calls, "query_in": in_tok, "query_out": out_tok}



# ---------------- 第二步：初筛 + 深度调研 ----------------
PRESCREEN_LABELS = ("likely_customer", "excluded", "irrelevant", "unsure")
PASS_LABELS = ("likely_customer", "unsure")  # 进入深度调研


class PrescreenItem(BaseModel):
    domain: str
    label: str   # likely_customer / excluded / irrelevant / unsure
    reason: str  # 一句中文理由


class PrescreenBatch(BaseModel):
    items: list[PrescreenItem]


def load_candidates(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("domain")]
    for r in rows:
        r["hit_count"] = int(r.get("hit_count") or 0)
    return rows


def _prescreen_batch(icp: dict, batch: list[dict]) -> tuple[list[dict], int, int]:
    system = "你是外贸获客研究员，负责根据搜索结果的域名、标题和摘要，快速判断一个网站是否值得深入调研。"
    listing = "\n".join(
        f"{i}. 域名: {c['domain']}\n   标题: {c['title']}\n   摘要: {c['snippet'][:300]}"
        for i, c in enumerate(batch, 1))
    user = (
        f"客户画像（JSON）：\n{json.dumps(icp, ensure_ascii=False, indent=2)}\n\n"
        "下面是搜索得到的候选网站。只根据域名、标题、摘要判断，给每个网站一个分类：\n"
        "- likely_customer：看起来是目标市场的户外/露营品牌商、零售商、批发商或进口商\n"
        "- excluded：明显命中画像的排除条件（制造商/工厂、亚洲出口商、租赁公司、B2B 平台等）\n"
        "- irrelevant：不是公司官网（新闻、博客、评测、比价、企业信息库、黄页等）\n"
        "- unsure：信息不足，无法判断\n"
        "每个网站给一句简短中文理由。domain 必须与输入完全一致，每个网站都要返回一项。\n\n" + listing)
    last_err = None
    for _ in range(2):  # 解析失败重试一次
        try:
            r = llm.call_structured(system, user, "submit_prescreen", "提交初筛结果",
                                    PrescreenBatch.model_json_schema(), max_tokens=4000)
            items = PrescreenBatch.model_validate(r.data).items
            return [i.model_dump() for i in items], r.input_tokens, r.output_tokens
        except Exception as e:
            last_err = e
    print(f"初筛批次失败：{last_err}")
    return [], 0, 0


def prescreen(icp: dict, cands: list[dict], batch_size: int, workers: int) -> tuple[int, int]:
    """原地给 cands 加 prescreen_label / prescreen_reason，返回 (input_tokens, output_tokens)。"""
    batches = [cands[i:i + batch_size] for i in range(0, len(cands), batch_size)]
    with ThreadPoolExecutor(workers) as ex:
        outs = list(ex.map(lambda b: _prescreen_batch(icp, b), batches))
    in_tok = out_tok = 0
    for batch, (items, i_t, o_t) in zip(batches, outs):
        in_tok += i_t
        out_tok += o_t
        by_domain = {it["domain"].lower(): it for it in items}
        for c in batch:
            it = by_domain.get(c["domain"].lower())
            if it and it["label"] in PRESCREEN_LABELS:
                c["prescreen_label"], c["prescreen_reason"] = it["label"], it["reason"]
            else:  # 模型漏掉或标签非法：保守地交给深度调研
                c["prescreen_label"], c["prescreen_reason"] = "unsure", "初筛未返回有效结果"
    return in_tok, out_tok


def classify_result(res) -> tuple[str, str]:
    """返回 (status, fail_reason)。没有 profile 就算失败：没抓到页面是 fetch_failed，其余是 llm_failed。"""
    if res.profile:
        return "researched", ""
    reason = res.error or "未知错误"
    if res.fetch_errors:
        reason += "；抓取错误：" + " / ".join(res.fetch_errors[:3])
    if not res.pages_fetched:
        return "fetch_failed", reason + "（建议人工查看官网）"
    return "llm_failed", reason


def research_with_backfill(icp: ICP, queue: list[dict], target: int, max_attempts: int, workers: int) -> int:
    """按顺序调研，直到成功数达到 target 或尝试数达到 max_attempts。每批只试"还差几家"那么多，失败的自动由下一批补位。
    结果写进每个候选的 result/status/fail_reason，返回尝试总数。"""
    ok = attempted = 0
    while ok < target and attempted < max_attempts and attempted < len(queue):
        n = min(target - ok, max_attempts - attempted)
        batch = queue[attempted:attempted + n]
        with ThreadPoolExecutor(workers) as ex:
            results = list(ex.map(lambda c: research_company(c["homepage"], icp), batch))
        for c, res in zip(batch, results):
            c["result"] = res
            c["status"], c["fail_reason"] = classify_result(res)
            ok += c["status"] == "researched"
        attempted += len(batch)
    return attempted


def lead_row(c: dict) -> dict:
    res, p = c.get("result"), None
    p = res.profile if res else None
    return {
        "domain": c["domain"], "homepage": c["homepage"],
        "prescreen_label": c["prescreen_label"], "prescreen_reason": c["prescreen_reason"],
        "status": c["status"], "fail_reason": c.get("fail_reason", ""),
        "score": p.match_score if p else "",
        "business_type": p.business_type if p else "",
        "confidence": p.confidence if p else "",
        "country": (p.country or "") if p else "",
        "reasons": " | ".join(p.match_reasons) if p else "",
        "emails": " ".join(res.contact_emails) if res else "",
        "error": (res.error or "") if res else "",
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 gbk，打印德语/波兰语会报错
    ap = argparse.ArgumentParser()
    ap.add_argument("--icp", default="icp.json")
    ap.add_argument("--candidates", help="已有的 candidates_*.csv；给了就跳过搜索")
    ap.add_argument("--max-research", type=int, default=30, help="最多深度调研多少家（按 hit_count 从高到低）")
    ap.add_argument("--max-attempts", type=int, default=None,
                    help="最多尝试调研多少家（含失败的），默认是 --max-research 的 2 倍")
    ap.add_argument("--threshold", type=int, default=60, help="评分 >= 阈值视为潜在客户")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=20, help="初筛每批处理的候选数")
    args = ap.parse_args()
    icp_dict = json.loads(Path(args.icp).read_text(encoding="utf-8"))
    icp = load_icp(args.icp)
    max_attempts = args.max_attempts or args.max_research * 2
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    search_calls = q_in = q_out = 0
    if args.candidates:
        cands = load_candidates(args.candidates)
        before = len(cands)
        cands = [c for c in cands if not is_blocked(c["domain"])]  # 套用最新的过滤名单
        print(f"读取候选 {before} 个，按当前过滤名单去掉 {before - len(cands)} 个，剩 {len(cands)} 个")
    else:
        out = run_search(icp_dict, ts)
        cands, search_calls, q_in, q_out = out["rows"], out["search_calls"], out["query_in"], out["query_out"]
        for c in cands:
            c["hit_count"] = int(c["hit_count"])

    # 初筛
    p_in, p_out = prescreen(icp_dict, cands, args.batch_size, args.workers)
    counts = {l: sum(c["prescreen_label"] == l for c in cands) for l in PRESCREEN_LABELS}
    print("\n=== 初筛 ===")
    for l in PRESCREEN_LABELS:
        print(f"{l}: {counts[l]}")

    # 深度调研（hit_count 从高到低，sorted 稳定，同分保持原顺序）
    passed = sorted([c for c in cands if c["prescreen_label"] in PASS_LABELS],
                    key=lambda c: -c["hit_count"])
    print(f"\n通过初筛 {len(passed)} 个，目标成功调研 {args.max_research} 家（最多尝试 {max_attempts} 家）...")
    attempted = research_with_backfill(icp, passed, args.max_research, max_attempts, args.workers)
    for c in cands:
        if "status" not in c:
            c["status"] = "not_researched" if c["prescreen_label"] in PASS_LABELS else "screened_out"

    tried = [c for c in cands if "result" in c]
    ok = [c for c in tried if c["status"] == "researched"]
    n_fetch = sum(c["status"] == "fetch_failed" for c in tried)
    n_llm = sum(c["status"] == "llm_failed" for c in tried)
    hits = [c for c in ok if c["result"].profile.match_score >= args.threshold]
    r_in = sum(c["result"].input_tokens for c in tried)
    r_out = sum(c["result"].output_tokens for c in tried)
    if len(ok) < args.max_research:
        reason = "已达尝试上限" if attempted >= max_attempts else "候选已用完"
        print(f"注意：成功调研只有 {len(ok)} 家，未达目标 {args.max_research}（{reason}）")

    # 输出顺序：researched 按分数从高到低 → 失败 → not_researched → screened_out
    order = {"researched": 0, "fetch_failed": 1, "llm_failed": 1, "not_researched": 2, "screened_out": 3}
    cands.sort(key=lambda c: (order[c["status"]],
                              -c["result"].profile.match_score if c["status"] == "researched" else 0))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"leads_{ts}.csv"
    write_csv(path, ["domain", "homepage", "prescreen_label", "prescreen_reason", "status", "fail_reason",
                     "score", "business_type", "confidence", "country", "reasons", "emails", "error"],
              [lead_row(c) for c in cands])

    total_tok = q_in + q_out + p_in + p_out + r_in + r_out
    print("\n=== 结果 ===")
    print(f"成功调研 {len(ok)} 家；尝试 {attempted} 家；补位 {max(0, attempted - args.max_research)} 家；"
          f"失败：抓取失败 {n_fetch}，模型输出失败 {n_llm}")
    print(f"{args.threshold} 分以上：{len(hits)} 家" + (f"（命中率 {len(hits) / len(ok):.0%}）" if ok else ""))
    print(f"成本：搜索调用 {search_calls} 次；搜索词 tokens in={q_in}/out={q_out}；"
          f"初筛 tokens in={p_in}/out={p_out}；调研 tokens in={r_in}/out={r_out}；总计 {total_tok}")
    if hits:
        print(f"平均每个 {args.threshold}+ 客户用 {total_tok // len(hits)} tokens")
    print(f"输出：{path}")


if __name__ == "__main__":
    main()
