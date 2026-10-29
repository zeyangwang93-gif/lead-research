"""抓取公司官网：首页 + 按类别挑选的关键子页面（关于我们、产品、联系、贸易合作）。"""
import re
from dataclasses import dataclass, field
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

import config

# 每个类别挑一个最可能的页面，避免全被产品详情页占满
LINK_GROUPS = {
    "about": ["about", "company", "who-we-are", "our-story", "über-uns", "uber-uns", "ueber-uns",
              "unternehmen", "quienes-somos", "empresa", "chi-siamo", "azienda", "a-propos", "qui-sommes"],
    "products": ["product", "produkt", "producto", "prodotti", "produit", "catalog", "brands", "range", "sortiment"],
    "trade": ["import", "distribut", "wholesale", "sourcing", "partner", "dealer", "händler", "b2b"],
    "contact": ["contact", "kontakt", "contacto", "contatti", "impressum", "imprint"],
}
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".mp4",
            ".doc", ".docx", ".xls", ".xlsx")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BAD_EMAIL_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".js", ".css")


@dataclass
class Page:
    url: str
    title: str
    text: str


@dataclass
class FetchResult:
    pages: list[Page] = field(default_factory=list)
    emails: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return urldefrag(url)[0]


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _load_robots(base: str, client: httpx.Client) -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    try:
        r = client.get(urljoin(base, "/robots.txt"))
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        rp.parse([])  # 读不到 robots.txt 视为允许
    return rp


def _get(client, robots, url: str, res: FetchResult) -> tuple[str, str] | None:
    if not robots.can_fetch(config.ROBOTS_AGENT, url):
        res.errors.append(f"robots.txt 禁止抓取: {url}")
        return None
    try:
        r = client.get(url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        res.errors.append(f"{url}: {type(e).__name__}")
        return None
    if "text/html" not in r.headers.get("content-type", ""):
        res.errors.append(f"非 HTML 页面: {url}")
        return None
    html = r.text
    for e in EMAIL_RE.findall(html):
        e = e.lower().rstrip(".")
        if not e.endswith(BAD_EMAIL_SUFFIX):
            res.emails.add(e)
    return str(r.url), html


def _to_page(url: str, html: str) -> Page:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return Page(url=url, title=title, text=text[: config.MAX_CHARS_PER_PAGE])


def _pick_links(html: str, base_url: str, limit: int) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    candidates: dict[str, set[str]] = {}
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(base_url, a["href"]))[0]
        if not href.startswith("http") or _host(href) != _host(base_url):
            continue
        if href.lower().endswith(SKIP_EXT) or href.rstrip("/") == base_url.rstrip("/"):
            continue
        key = (urlparse(href).path + " " + a.get_text(" ", strip=True)).lower().replace(" ", "-")
        groups = {g for g, kws in LINK_GROUPS.items() if any(k in key for k in kws)}
        if groups:
            candidates.setdefault(href, set()).update(groups)

    def depth(h: str) -> int:
        return len(urlparse(h).path)

    picked: list[str] = []
    for g in LINK_GROUPS:  # 每类取路径最短的（更可能是栏目页而不是详情页）
        matches = [h for h, gs in candidates.items() if g in gs and h not in picked]
        if matches:
            picked.append(min(matches, key=depth))
    rest = sorted((h for h in candidates if h not in picked), key=depth)
    return (picked + rest)[:limit]


def fetch_site(url: str) -> FetchResult:
    url = normalize_url(url)
    res = FetchResult()
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "en;q=0.9,*;q=0.5"}
    with httpx.Client(headers=headers, timeout=config.REQUEST_TIMEOUT, follow_redirects=True) as client:
        robots = _load_robots(url, client)
        home = _get(client, robots, url, res)
        if home is None:
            return res
        final_url, html = home
        res.pages.append(_to_page(final_url, html))
        for link in _pick_links(html, final_url, config.MAX_PAGES - 1):
            got = _get(client, robots, link, res)
            if got:
                res.pages.append(_to_page(*got))
    return res
