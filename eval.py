"""在标注集上评估。用法：python eval.py --data data/eval_set.csv --tag v1"""
import argparse
import csv
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from extractor import PROMPT_VERSION
from research import load_icp, research_company


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/eval_set.csv")
    ap.add_argument("--icp", default="icp.json")
    ap.add_argument("--threshold", type=int, default=60, help="评分 >= 阈值视为潜在客户")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--model", default=None)
    ap.add_argument("--tag", default=PROMPT_VERSION, help="本次运行的标签，便于对比不同版本")
    args = ap.parse_args()

    icp = load_icp(args.icp)
    with open(args.data, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("url")]

    with ThreadPoolExecutor(args.workers) as ex:
        results = list(ex.map(lambda r: research_company(r["url"], icp, args.model), rows))

    tp = fp = fn = tn = failed = 0
    ev_total = ev_bad = 0
    out_rows = []
    for row, res in zip(rows, results):
        label = int(row["label"])
        p = res.profile
        pred = None if p is None else int(p.match_score >= args.threshold)
        if pred is None:
            failed += 1
        elif pred and label:
            tp += 1
        elif pred and not label:
            fp += 1
        elif not pred and label:
            fn += 1
        else:
            tn += 1
        if res.evidence_check:
            ev_total += res.evidence_check.total
            ev_bad += res.evidence_check.bad_url + res.evidence_check.quote_not_found
        out_rows.append({
            "url": res.url, "label": label,
            "score": p.match_score if p else "", "pred": "" if pred is None else pred,
            "correct": "" if pred is None else int(pred == label),
            "business_type": p.business_type if p else "",
            "confidence": p.confidence if p else "",
            "reasons": " | ".join(p.match_reasons) if p else "",
            "risks": " | ".join(p.risks) if p else "",
            "pages": len(res.pages_fetched), "emails": " ".join(res.contact_emails),
            "error": res.error or "", "notes": row.get("notes", ""),
            "tokens_in": res.input_tokens, "tokens_out": res.output_tokens,
            "cost": res.cost, "latency_s": res.latency_s,
        })

    done = len(rows) - failed
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    accuracy = (tp + tn) / done if done else 0.0
    ok = [r for r in results if r.profile]
    avg = lambda xs: sum(xs) / len(xs) if xs else 0.0

    print(f"\n=== 评估结果 [{args.tag}] 阈值={args.threshold} ===")
    print(f"样本 {len(rows)}，成功 {done}，失败 {failed}")
    print(f"准确率 {accuracy:.1%}  精确率 {precision:.1%}  召回率 {recall:.1%}")
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"引用可核验率 {1 - ev_bad / ev_total:.1%}" if ev_total else "引用可核验率 N/A")
    print(f"平均 tokens 输入 {avg([r.input_tokens for r in ok]):.0f} / 输出 {avg([r.output_tokens for r in ok]):.0f}")
    print(f"平均成本 {avg([r.cost for r in ok]):.4f}  平均耗时 {avg([r.latency_s for r in results]):.1f}s")

    out = Path("results") / f"eval_{args.tag}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig 让 Excel 正常显示中文
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)
    print(f"明细已保存到 {out}（重点看 correct=0 的行）")


if __name__ == "__main__":
    main()
