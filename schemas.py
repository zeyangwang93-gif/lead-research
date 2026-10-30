from typing import Literal
from pydantic import BaseModel, Field

BusinessType = Literal[
    "importer", "distributor", "wholesaler", "retailer", "brand",
    "manufacturer", "service", "other", "unknown",
]


class ICP(BaseModel):
    """理想客户画像：由使用者（外贸企业）填写。"""
    our_products: str
    target_markets: list[str]
    target_customer_types: list[str]
    exclude: list[str] = []
    product_keywords: list[str] = []


class Evidence(BaseModel):
    claim: str = Field(description="这条证据支持的判断，简体中文")
    source_url: str = Field(description="原文所在页面的 URL，必须是提供的页面之一")
    quote: str = Field(description="网页原文片段，逐字摘录，不超过 30 个词")


class CompanyProfile(BaseModel):
    """LLM 输出的结构。联系邮箱不让模型生成，由正则从网页提取，避免编造。"""
    company_name: str | None
    country: str | None
    business_type: BusinessType
    main_products: list[str]
    imports_from_abroad: bool | None = Field(description="有直接证据才填 true/false，否则 null")
    match_score: int = Field(ge=0, le=100)
    match_reasons: list[str]
    risks: list[str] = Field(description="不确定因素或不匹配的信号")
    evidence: list[Evidence]
    confidence: Literal["high", "medium", "low"]


class EvidenceCheck(BaseModel):
    total: int = 0
    bad_url: int = 0          # 引用了未抓取的页面，已删除
    quote_not_found: int = 0  # 引文在原网页中找不到，保留但计数


class ResearchResult(BaseModel):
    url: str
    profile: CompanyProfile | None = None
    contact_emails: list[str] = []
    pages_fetched: list[str] = []
    fetch_errors: list[str] = []
    evidence_check: EvidenceCheck | None = None
    error: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_s: float = 0.0
