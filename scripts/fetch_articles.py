"""从公众号合集链接批量获取文章列表.

使用方式:
  python scripts/fetch_articles.py "https://mp.weixin.qq.com/mp/appmsgalbum?..."
  python scripts/fetch_articles.py --url "合集URL" --max-pages 20
  python scripts/fetch_articles.py --url "合集URL" --author "作者名"

流程:
  1. 从合集 URL 提取 __biz 和 album_id
  2. 循环分页获取文章列表 (begin=0,10,20...)
  3. url_hash 去重，断点续抓
  4. 写入 articles 表
"""

import argparse
import json
import re
import sys
import time
import hashlib
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import dao_article
import dao_author
from database import get_connection
from llm_client import LLMClient

WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.29"
)

REQUEST_INTERVAL = 3        # seconds between API calls
MAX_RETRIES = 3
MAX_PAGES_DEFAULT = 20      # 200 articles max per run
ALBUM_API = "https://mp.weixin.qq.com/mp/appmsgalbum"


def parse_album_url(url: str) -> tuple[str, str, str]:
    """Extract __biz, album_id and optional author_name from album URL.

    Returns (__biz, album_id, author_name_or_empty)
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    biz = params.get("__biz", [""])[0]
    album_id = params.get("album_id", [""])[0]
    action = params.get("action", ["getalbum"])[0]

    if not biz or not album_id:
        # Try to extract from path or fragment
        for part in url.split("?"):
            if "__biz=" in part:
                m = re.search(r"__biz=([^&]+)", part)
                if m:
                    biz = m.group(1)
            if "album_id=" in part:
                m = re.search(r"album_id=([^&]+)", part)
                if m:
                    album_id = m.group(1)

    return biz, album_id, action


def fetch_album_page(biz: str, album_id: str, begin: int = 0,
                     count: int = 10) -> dict | None:
    """Fetch one page of article list from WeChat album API."""
    import httpx

    url = (
        f"{ALBUM_API}?__biz={biz}&action=getalbum"
        f"&album_id={album_id}&count={count}&begin={begin}&f=json"
    )

    headers = {
        "User-Agent": WECHAT_UA,
        "Accept": "application/json",
    }

    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data
        except httpx.TimeoutException:
            wait = 10 * (2 ** attempt)
            print(f"    超时，{wait}s 后重试 ({attempt + 1}/{MAX_RETRIES})...")
            time.sleep(wait)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                print(f"    限流，等待 60s...")
                time.sleep(60)
                continue
            print(f"    HTTP {e.response.status_code}，跳过")
            return None
        except Exception as e:
            print(f"    请求失败: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(10 * (2 ** attempt))
            else:
                return None
    return None


def fetch_article_content(url: str, llm_client: LLMClient = None) -> str | None:
    """Fetch article full content via WebFetch.

    Returns article text, or None if failed.
    """
    import httpx

    headers = {
        "User-Agent": WECHAT_UA,
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text

        # Simple extraction: get <title> and article body
        title_match = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        # Try to find the article content div
        content = ""
        for div_id in ["js_content", "img-content", "js_article"]:
            m = re.search(
                rf'<div[^>]*id="{div_id}"[^>]*>(.*?)</div>\s*<script',
                html, re.DOTALL | re.IGNORECASE,
            )
            if m:
                content = m.group(1)
                break

        if not content:
            # Fallback: extract all text from body
            body = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL)
            if body:
                content = body.group(1)

        # Strip HTML tags
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"&nbsp;", " ", content)
        content = re.sub(r"&lt;", "<", content)
        content = re.sub(r"&gt;", ">", content)
        content = re.sub(r"&amp;", "&", content)
        content = re.sub(r"&quot;", '"', content)
        # Collapse whitespace
        content = re.sub(r"\s+", " ", content).strip()

        return content if len(content) > 100 else None
    except Exception as e:
        print(f"    抓取文章内容失败: {e}")
        return None


def fetch_album(author_name: str, biz: str, album_id: str,
                max_pages: int = MAX_PAGES_DEFAULT,
                fetch_content: bool = False,
                llm_client: LLMClient = None) -> dict:
    """Fetch all articles from a WeChat album.

    Returns {total: int, new: int, skipped: int, errors: int}
    """
    print(f"\n{'='*60}")
    print(f"获取合集文章: {author_name}")
    print(f"{'='*60}")

    # Ensure author profile exists
    author_dir = Path.home() / ".astromind-praxis" / "authors" / author_name
    author_dir.mkdir(parents=True, exist_ok=True)

    profile = dao_author.get_profile(author_name)
    if not profile:
        dao_author.insert_profile(author_name, str(author_dir))

    total_fetched = 0
    new_articles = 0
    skipped = 0
    errors = 0

    for page in range(max_pages):
        begin = page * 10
        print(f"\n  第 {page + 1} 页 (begin={begin})...")
        data = fetch_album_page(biz, album_id, begin)

        if not data:
            print("    获取失败，停止分页")
            break

        # Parse article list
        article_list = data.get("getalbum_resp", {}).get("article_list", [])
        if not article_list:
            article_list = data.get("article_list", [])

        if not article_list:
            print("    无更多文章，停止分页")
            break

        for item in article_list:
            # Different response formats
            if isinstance(item, dict):
                title = item.get("title", "")
                url = item.get("url", item.get("content_url", ""))
                create_time = item.get("create_time", "")
            else:
                continue

            if not url:
                continue

            # Resolve relative URLs
            if url.startswith("/"):
                url = f"https://mp.weixin.qq.com{url}"

            # Check if already exists
            if dao_article.article_exists(url):
                skipped += 1
                continue

            # Fetch content if requested
            content_text = ""
            if fetch_content:
                print(f"    抓取: {title[:40]}...")
                content_text = fetch_article_content(url, llm_client) or ""
                time.sleep(REQUEST_INTERVAL)

            # Insert article
            article_id = dao_article.insert_article(
                author_name=author_name,
                title=title,
                url=url,
                content_text=content_text,
                published_at=create_time,
                source_type="wechat",
            )

            if article_id:
                new_articles += 1
            else:
                skipped += 1

            total_fetched += 1

        # Respect rate limiting between pages
        if page < max_pages - 1:
            time.sleep(REQUEST_INTERVAL)

    # Update author counts
    dao_author.update_counts(
        author_name,
        article_count=dao_article.count_articles(author_name),
        last_article_at=datetime_now_str(),
    )

    result = {
        "author_name": author_name,
        "total_fetched": total_fetched,
        "new": new_articles,
        "skipped": skipped,
        "errors": errors,
    }

    print(f"\n获取完成: 新增 {new_articles} | 跳过 {skipped} | 错误 {errors}")
    return result


def datetime_now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    global REQUEST_INTERVAL
    parser = argparse.ArgumentParser(description="获取公众号合集文章列表")
    parser.add_argument("url", nargs="?", help="公众号合集 URL")
    parser.add_argument("--url", dest="album_url", help="公众号合集 URL (具名参数)")
    parser.add_argument("--author", required=True, help="作者名")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT,
                        help=f"最大分页数 (默认 {MAX_PAGES_DEFAULT}, 每页 10 篇)")
    parser.add_argument("--fetch-content", action="store_true",
                        help="同时抓取文章全文 (默认仅获取标题+URL)")
    parser.add_argument("--interval", type=int, default=REQUEST_INTERVAL,
                        help=f"请求间隔秒数 (默认 {REQUEST_INTERVAL})")
    args = parser.parse_args()

    album_url = args.url or args.album_url
    if not album_url:
        parser.print_help()
        print("\n[!] 请提供公众号合集 URL")
        sys.exit(1)

    # Override interval
    REQUEST_INTERVAL = args.interval

    biz, album_id, _ = parse_album_url(album_url)
    if not biz or not album_id:
        print(f"[!] 无法从 URL 中提取 __biz 和 album_id: {album_url}")
        print("    请确认链接格式: https://mp.weixin.qq.com/mp/appmsgalbum?__biz=...&action=getalbum&album_id=...")
        sys.exit(1)

    print(f"解析: __biz={biz[:20]}... album_id={album_id}")

    # Load LLM client if content fetching is needed
    llm_client = None
    if args.fetch_content:
        config_path = Path.home() / ".astromind-praxis" / "config.yaml"
        if config_path.exists():
            try:
                import yaml
                with open(config_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                llm_cfg = config.get("llm", {})
                if llm_cfg.get("api_key"):
                    llm_client = LLMClient(
                        llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"],
                    )
            except Exception:
                pass

    result = fetch_album(
        author_name=args.author,
        biz=biz,
        album_id=album_id,
        max_pages=args.max_pages,
        fetch_content=args.fetch_content,
        llm_client=llm_client,
    )

    print(f"\n数据库文章总数: {dao_article.count_articles(args.author)}")


if __name__ == "__main__":
    main()
