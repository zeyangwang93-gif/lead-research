# Lead Research：外贸获客 Agent · 第 1 周

输入一个公司官网，输出这家公司是否是潜在买家的结构化分析，每条判断附原文证据。附带评估脚本，用标注集统计准确率、成本和耗时。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env              # 填 API key 和模型
cp icp.example.json icp.json      # 改成你的产品和目标客户
python research.py https://某公司官网
```

## 流程

```
官网 URL → fetcher（首页 + 关于/产品/贸易/联系 各选一页，遵守 robots.txt）
        → extractor（LLM 强制 tool calling，按 JSON Schema 输出）
        → verify（删除引用未抓取页面的证据，统计找不到原文的引文）
        → ResearchResult（含 tokens、成本、耗时）
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `fetcher.py` | 抓取网页、挑选子页面、正则提取邮箱 |
| `llm.py` | 统一的结构化输出调用，支持 Anthropic 和 OpenAI 兼容接口 |
| `extractor.py` | Prompt、调用模型、事后核验 |
| `schemas.py` | 输入输出的数据结构 |
| `research.py` | 单个公司调研的命令行入口 |
| `eval.py` | 在标注集上批量评估 |

## 设计决策（面试可讲）

- **结构化输出用强制 tool calling**：比让模型"返回 JSON"更稳定，再用 pydantic 校验。
- **邮箱不让模型生成**：由正则从网页提取，从根源上避免编造联系方式。
- **证据可核验**：要求模型逐字摘录原文并给出 URL，程序检查 URL 是否真实抓取过、引文是否出现在原文中。
- **防提示注入**：网页是不可信数据，system prompt 明确不执行网页中的指令。
- **模型可替换**：改 `.env` 即可切换到 DeepSeek、Kimi、通义千问等，方便对比效果和成本。
- **子页面按类别挑选**：每类选路径最短的链接，避免名额被产品详情页占满。

## 本周任务

1. 配好 `.env`，用 3–5 个网址跑通 `research.py`，看输出是否合理。
2. 选一个真实产品写 `icp.json`，越具体越好。
3. 建标注集 `data/eval_set.csv`（格式见 `data/eval_set.example.csv`），30–50 家：
   - 正例：展会参展商名单、搜索 "产品 + importer + 国家" 找到并人工确认的买家。
   - 负例要包含难例：同行制造商、中国贸易公司、B2B 平台、相关但品类不同的公司。
4. 跑基线：`python eval.py --tag v1`，记录准确率、精确率、召回率、引用可核验率、平均成本。
5. 打开结果 CSV 看 `correct=0` 的行，分析原因，改 prompt 后用新 tag 重跑对比。
6. 每个问题和解决方法记到 `docs/notes.md`，这是面试"踩过的坑"的素材。

## 已知局限

- JavaScript 渲染的网站只能拿到很少文本（后续可接 Playwright）。
- 有 Cloudflare 等反爬保护的网站会抓取失败。
- 只抓同域名页面，每页截断到 4000 字符，信息可能不全。
- 引文核验是精确子串匹配，模型稍作改写就会计为"找不到"，所以该指标偏保守。
- 部分 OpenAI 兼容服务不支持强制指定工具（`tool_choice`），切换时需要测试。
