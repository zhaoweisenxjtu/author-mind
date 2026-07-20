"""查看作者知识提炼进度.

使用方式:
  python scripts/status.py "作者名"
  python scripts/status.py --author "作者名"
"""

import argparse
import sys
from pathlib import Path

import dao_article
import dao_atom
import dao_model
import dao_author
from database import get_connection

AUTHORS_DIR = Path.home() / ".astromind-praxis" / "authors"


def status(author_name: str):
    """Display author pipeline progress."""
    profile = dao_author.get_profile(author_name)

    article_count = dao_article.count_articles(author_name)
    atom_count = dao_atom.count_atoms(author_name)
    model_count = dao_model.count_models(author_name)

    # Check file existence
    author_dir = AUTHORS_DIR / author_name
    persona_exists = (author_dir / "persona.md").exists()
    mirror_exists = (author_dir / "writing-mirror.md").exists()

    # Model dir
    models_dir = author_dir / "models"
    model_files = list(models_dir.glob("*.md")) if models_dir.exists() else []

    # Type breakdown
    type_breakdown = {}
    atoms = dao_atom.list_atoms_by_author(author_name)
    for a in atoms:
        t = a.get("type", "?")
        type_breakdown[t] = type_breakdown.get(t, 0) + 1

    # Unmerged atoms
    unmerged = sum(1 for a in atoms if not a.get("merged_to"))

    # Topics
    topics = dao_atom.get_all_topics(author_name)

    # Articles with/without atoms
    articles_with = dao_article.count_articles_with_atoms(author_name)
    articles_pending = article_count - articles_with

    print(f"\n{'='*50}")
    print(f"  {author_name} · 知识提炼进度")
    print(f"{'='*50}")

    # Progress bar
    stages = [
        ("L0 文章摄入", article_count > 0),
        ("L1 原子提取", atom_count > 0),
        ("L2 模型归并", model_count > 0),
        ("L3 认知画像", persona_exists),
        ("L4 写作镜像", mirror_exists),
    ]

    max_label = max(len(s[0]) for s in stages)
    completed = sum(1 for _, done in stages if done)

    print(f"\n  整体进度: {completed}/{len(stages)}")
    for label, done in stages:
        bar = "▓" if done else "░"
        print(f"  [{bar}] {label:<{max_label}}")

    print(f"\n── 数据统计 ──")
    print(f"  L0 文章: {article_count} 篇 (已提取: {articles_with}, 待提取: {articles_pending})")
    print(f"  L1 原子: {atom_count} 条")
    if type_breakdown:
        print(f"    类型分布: {type_breakdown}")
    print(f"    未归并: {unmerged} 条")
    print(f"  L2 模型: {model_count} 个 · MD 快照: {len(model_files)} 个")
    print(f"  L3 persona: {'已生成' if persona_exists else '未生成'}")
    print(f"  L4 mirror:  {'已生成' if mirror_exists else '未生成'}")

    if topics:
        print(f"\n── Topic 覆盖 ({len(topics)} 个) ──")
        topic_counts = dao_atom.count_atoms_by_topic(author_name)
        for tc in topic_counts[:10]:
            has_model = dao_model.model_exists(author_name, tc["topic"])
            flag = " [L2]" if has_model else ""
            print(f"  {tc['topic']}: {tc['cnt']} atoms{flag}")
        if len(topic_counts) > 10:
            print(f"  ... 还有 {len(topic_counts) - 10} 个 topic")

    # Profile timestamps
    if profile:
        if profile.get("last_distilled_at"):
            print(f"\n  最近蒸馏: {profile['last_distilled_at']}")
        if profile.get("created_at"):
            print(f"  首次入库: {profile['created_at']}")

    # Next steps
    print(f"\n── 下一步 ──")
    if article_count == 0:
        print("  → 获取文章: python scripts/fetch_articles.py <合集URL>")
    elif atom_count == 0:
        print(f"  → L1 提取: python scripts/extract.py --author \"{author_name}\"")
    elif model_count == 0 and unmerged >= 3:
        print(f"  → L2 归并: python scripts/merge_to_l2.py --author \"{author_name}\"")
    elif not persona_exists and model_count >= 3:
        print(f"  → L3 提炼: python scripts/extract_l3.py --author \"{author_name}\"")
    elif not mirror_exists and persona_exists:
        print(f"  → L4 镜像: python scripts/extract_l4.py --author \"{author_name}\"")
    else:
        print("  所有阶段已完成")

    print()


def main():
    parser = argparse.ArgumentParser(description="查看作者知识提炼进度")
    parser.add_argument("author", nargs="?", help="作者名")
    parser.add_argument("--author", dest="author_name", help="作者名 (具名参数)")
    args = parser.parse_args()

    name = args.author or args.author_name
    if not name:
        parser.print_help()
        sys.exit(1)

    status(name)


if __name__ == "__main__":
    main()
