"""单个公司调研。用法：python research.py https://example.com --icp icp.json"""
import argparse
import json
import time
from pathlib import Path

import config
from extractor import analyze
from fetcher import _host, fetch_site, normalize_url
from schemas import ICP, ResearchResult


def research_company(url: str, icp: ICP, model: str | None = None) -> ResearchResult:
    t0 = time.time()
    url = normalize_url(url)
    result = ResearchResult(url=url)
    try:
        fetched = fetch_site(url, icp.product_keywords)
        result.pages_fetched = [p.url for p in fetched.pages]
        result.contact_emails = sorted(fetched.emails)
        result.fetch_errors = fetched.errors
        if not fetched.pages:
            result.error = "没有抓到任何页面"
        else:
            profile, check, llm = analyze(icp, fetched.pages, model)
            result.profile = profile
            result.evidence_check = check
            result.model = llm.model
            result.input_tokens = llm.input_tokens
            result.output_tokens = llm.output_tokens
            result.cost = config.estimate_cost(llm.input_tokens, llm.output_tokens)
    except Exception as e:  # 批量运行时单条失败不应中断整体
        result.error = f"{type(e).__name__}: {e}"
    result.latency_s = round(time.time() - t0, 2)
    return result


def load_icp(path: str) -> ICP:
    return ICP.model_validate_json(Path(path).read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--icp", default="icp.json")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    result = research_company(args.url, load_icp(args.icp), args.model)
    out = Path("results") / f"{_host(result.url)}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(result.model_dump_json(indent=2))
    print(f"\n已保存到 {out}")


if __name__ == "__main__":
    main()
