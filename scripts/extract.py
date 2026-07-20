"""L1 提取: 对已有文章执行 6 类型 Schema 知识提取.

使用方式:
  1. 直接 API 模式 (需要 config.yaml 配置 LLM):
     python scripts/extract.py --author "作者名"

  2. Agent 模式 (无 LLM 配置, 输出 prompt 到 stdout):
     python scripts/extract.py --author "作者名" --agent
     然后由 Claude Code agent 调用 LLM, 结果通过 --result 传入

  3. 单篇提取:
     python scripts/extract.py --article-id 1
"""

import argparse
import json
import sys
from pathlib import Path

import dao_article
import dao_atom
import dao_author
from database import get_connection
from llm_client import LLMClient


EXTRACT_SYSTEM_PROMPT = (Path(__file__).parent.parent / "prompts" / "extract_l1.txt") \
    .read_text(encoding="utf-8")

EXTRACT_SCHEMA = {
    "name": "knowledge_extraction",
    "schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "topic": {"type": "string"},
                        "source_date": {"type": "string"},
                        "verifiability": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["content", "topic"],
                },
            },
            "methods": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "topic": {"type": "string"},
                        "steps": {"type": "array", "items": {"type": "string"}},
                        "applicability": {"type": "string"},
                        "limitations": {"type": "string"},
                    },
                    "required": ["content", "topic"],
                },
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "topic": {"type": "string"},
                        "stance": {"type": "string", "enum": ["strong", "moderate", "speculative"]},
                        "counter_evidence": {"type": "string"},
                    },
                    "required": ["content", "topic"],
                },
            },
            "assumptions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "topic": {"type": "string"},
                        "supporting_clues": {"type": "string"},
                        "alternative_possible": {"type": "string", "enum": ["yes", "no"]},
                    },
                    "required": ["content", "topic"],
                },
            },
            "counters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "topic": {"type": "string"},
                        "what_is_countered": {"type": "string"},
                        "author_alternative": {"type": "string"},
                    },
                    "required": ["content", "topic"],
                },
            },
            "styles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sentence": {"type": "string"},
                        "pattern": {"type": "string"},
                        "usage": {"type": "string"},
                    },
                    "required": ["sentence", "pattern"],
                },
            },
        },
        "required": ["facts", "methods", "values", "assumptions", "counters", "styles"],
    },
}


def load_llm_config():
    """Load LLM config from astromind config.yaml."""
    config_path = Path.home() / ".astromind-praxis" / "config.yaml"
    if not config_path.exists():
        return {}, {}, False
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        llm = config.get("llm", {})
        has_config = bool(llm.get("api_key"))
        return llm, config, has_config
    except Exception:
        return {}, {}, False


def extract_article(article: dict, llm_client: LLMClient) -> dict | None:
    """Extract knowledge atoms from a single article."""
    published_at = article.get("published_at") or "未知"
    content = article.get("content_text", "")

    if not content.strip():
        print(f"  [!] 文章 {article['id']} 内容为空，跳过")
        return None

    # Truncate if too long (max ~8000 chars to stay within context)
    if len(content) > 12000:
        content = content[:12000] + "\n...(文章已截断)"

    user_prompt = f"""文章日期: {published_at}
文章标题: {article['title']}
文章内容:
{content}"""

    try:
        result = llm_client.chat(
            EXTRACT_SYSTEM_PROMPT,
            user_prompt,
            schema=EXTRACT_SCHEMA,
            temperature=0.3,
            max_tokens=4096,
        )
        return result
    except Exception as e:
        print(f"  [!] LLM 调用失败: {e}")
        return None


def store_atoms(article: dict, result: dict):
    """Store extracted atoms into knowledge_atoms table."""
    published_at = article.get("published_at")
    author_name = article["author_name"]
    article_id = article["id"]
    counts = {}

    for atom_type in ["facts", "methods", "values", "assumptions", "counters", "styles"]:
        items = result.get(atom_type, [])
        count = 0
        for item in items:
            if atom_type == "styles":
                content = item.get("sentence", "")
                evidence = json.dumps({
                    "pattern": item.get("pattern", ""),
                    "usage": item.get("usage", ""),
                })
            else:
                content = item.get("content", "")
                evidence = item.get("supporting_clues") or item.get("counter_evidence") or None

            topic = item.get("topic", "未分类")
            if not content.strip():
                continue

            dao_atom.insert_atom(
                article_id=article_id,
                author_name=author_name,
                atom_type=atom_type.rstrip("s"),  # "facts" → "fact"
                content=content,
                topic=topic,
                evidence=evidence,
                published_at=published_at,
            )
            count += 1

        counts[atom_type] = count

    return counts


def extract_all(author_name: str, llm=None):
    """Extract atoms for all unprocessed articles of author."""
    print(f"\n{'='*60}")
    print(f"L1 提取: {author_name}")
    print(f"{'='*60}")

    # Load config for LLM
    if llm is None:
        llm_config, _, has_config = load_llm_config()
        if not has_config:
            print("[!] 未配置 LLM API key (检查 ~/.astromind-praxis/config.yaml)")
            print("[!] 请使用 --agent 模式或先配置 LLM")
            return
        llm = LLMClient(
            base_url=llm_config.get("base_url", ""),
            api_key=llm_config.get("api_key", ""),
            model=llm_config.get("model", ""),
        )

    # Get unprocessed articles
    articles = dao_article.list_articles_without_atoms(author_name)
    if not articles:
        print("所有文章已提取完毕")
        return

    print(f"待提取: {len(articles)} 篇\n")

    total_counts = {}
    for i, article in enumerate(articles):
        print(f"[{i+1}/{len(articles)}] {article['title'][:50]}...")
        result = extract_article(article, llm)

        if result is None:
            continue

        counts = store_atoms(article, result)
        print(f"  Facts:{counts.get('facts',0)} Methods:{counts.get('methods',0)} "
              f"Values:{counts.get('values',0)} Assumptions:{counts.get('assumptions',0)} "
              f"Counters:{counts.get('counters',0)} Styles:{counts.get('styles',0)}")

        for k, v in counts.items():
            total_counts[k] = total_counts.get(k, 0) + v

    # Update author profile
    dao_author.update_counts(
        author_name,
        atom_count=dao_atom.count_atoms(author_name),
    )

    print(f"\n提取完成。总计: {total_counts}")
    return total_counts


def extract_one_article(article_id: int, llm=None):
    """Extract atoms for a single article by ID."""
    article = dao_article.get_article(article_id)
    if not article:
        print(f"文章 {article_id} 不存在")
        return

    if llm is None:
        llm_config, _, has_config = load_llm_config()
        if not has_config:
            print("[!] 未配置 LLM API key")
            return
        llm = LLMClient(
            base_url=llm_config.get("base_url", ""),
            api_key=llm_config.get("api_key", ""),
            model=llm_config.get("model", ""),
        )

    print(f"提取: {article['title']}")
    result = extract_article(article, llm)
    if result:
        counts = store_atoms(article, result)
        print(f"Facts:{counts.get('facts',0)} Methods:{counts.get('methods',0)} "
              f"Values:{counts.get('values',0)} Assumptions:{counts.get('assumptions',0)} "
              f"Counters:{counts.get('counters',0)} Styles:{counts.get('styles',0)}")

        dao_author.update_counts(
            article["author_name"],
            atom_count=dao_atom.count_atoms(article["author_name"]),
        )


def agent_mode(author_name: str):
    """Output prompt data for Claude Code agent to process.

    The agent reads the prompt from stdout, makes LLM calls,
    and passes results back via --result JSON.
    """
    articles = dao_article.list_articles_without_atoms(author_name)
    if not articles:
        print("[DONE] 所有文章已提取完毕")
        return

    prompts = []
    for article in articles:
        published_at = article.get("published_at") or "未知"
        content = article.get("content_text", "")
        if len(content) > 12000:
            content = content[:12000] + "\n...(截断)"

        prompts.append({
            "article_id": article["id"],
            "title": article["title"],
            "author_name": article["author_name"],
            "published_at": published_at,
            "system": EXTRACT_SYSTEM_PROMPT,
            "user": f"文章日期: {published_at}\n文章标题: {article['title']}\n文章内容:\n{content}",
            "schema": EXTRACT_SCHEMA,
        })

    # Output as JSON Lines — one line per article
    for p in prompts:
        print(json.dumps(p, ensure_ascii=False))


def apply_results(results_json: str):
    """Apply LLM results (from agent) to the database."""
    results = json.loads(results_json)
    if not isinstance(results, list):
        results = [results]

    for item in results:
        article_id = item["article_id"]
        article = dao_article.get_article(article_id)
        if not article:
            print(f"[!] 文章 {article_id} 不存在")
            continue

        result = item.get("result", item)
        counts = store_atoms(article, result)
        print(f"[OK] {article['title'][:40]}... "
              f"F:{counts.get('facts',0)} M:{counts.get('methods',0)} "
              f"V:{counts.get('values',0)} A:{counts.get('assumptions',0)} "
              f"C:{counts.get('counters',0)} S:{counts.get('styles',0)}")

    # Update counts
    if results:
        author_name = results[0].get("author_name") or (
            dao_article.get_article(results[0]["article_id"]) or {}).get("author_name", "")
        if author_name:
            dao_author.update_counts(
                author_name,
                atom_count=dao_atom.count_atoms(author_name),
            )


def main():
    parser = argparse.ArgumentParser(description="L1 知识提取")
    parser.add_argument("--author", help="作者名")
    parser.add_argument("--article-id", type=int, help="单篇文章 ID")
    parser.add_argument("--agent", action="store_true",
                        help="Agent 模式: 输出 prompt JSON Lines 到 stdout")
    parser.add_argument("--result", help="Agent 模式: 将 LLM 结果写入 DB (JSON)")
    args = parser.parse_args()

    if args.result:
        apply_results(args.result)
    elif args.agent:
        if not args.author:
            print("--agent 模式需要指定 --author")
            sys.exit(1)
        agent_mode(args.author)
    elif args.article_id:
        extract_one_article(args.article_id)
    elif args.author:
        extract_all(args.author)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
