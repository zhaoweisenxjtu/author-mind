"""L1→L2 归并: Topic 聚类 + 女娲三重验证 + LLM 归并.

流程:
  1. Topic 聚类: embedding 聚类 topic 近义词
  2. 三重验证: 跨域复现 + 预测力 + 排他性
  3. LLM 归并: 同 topic 原子合并为心智模型
  4. 写入 mental_models 表 + MD 快照

使用方式:
  python scripts/merge_to_l2.py --author "作者名"
  python scripts/merge_to_l2.py --author "作者名" --topic "领导力"  # 单 topic
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

import dao_atom
import dao_model
import dao_article
import dao_author
from llm_client import LLMClient

from vec_index import EmbeddingClient, cosine_similarity

# ── 常量 & 路径 ──
AUTHORS_DIR = Path.home() / ".astromind-praxis" / "authors"
TOPIC_SIMILARITY_THRESHOLD = 0.75  # topic 聚类阈值
MERGE_TRIGGER_COUNT = 3             # same_topic 原子数达到此值触发归并

# ── Prompt 模板 ──
MERGE_L2_SYSTEM = """你是知识整合专家。将分散的原子知识点聚合为一个结构化的心智模型。
心智模型是该话题下作者认知体系的结构化表达，包括核心方法、判断逻辑、边界条件和演变历史。"""

MERGE_L2_USER = """话题: {topic}

该话题涉及的原子知识点:
{atoms_text}

请整合为结构化 Markdown:

# 心智模型: {topic}

## 元数据
- 证据数: {evidence_count} · 涉及文章: {article_count} 篇
- 首次出现: {first_seen} · 最近更新: {last_seen}

## 核心方法/观点
[从多条原子聚合的核心内容，保留作者原文的关键表述]

## 关键判断标准
[作者隐式或显式的判断逻辑]

## 边界与反例
[不适用的情况/作者明确反对的]

## 来源文章
{article_refs}

以 JSON 格式输出。"""

MERGE_L2_SCHEMA = {
    "name": "mental_model",
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content_md": {"type": "string"},
        },
        "required": ["title", "content_md"],
    },
}


def load_llm():
    config_path = Path.home() / ".astromind-praxis" / "config.yaml"
    if not config_path.exists():
        return None, {}
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        llm_cfg = config.get("llm", {})
        emb_cfg = config.get("embedding", {})
        if llm_cfg.get("api_key"):
            return (
                LLMClient(llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"]),
                emb_cfg,
            )
    except Exception:
        pass
    return None, {}


def cluster_topics(author_name: str, emb_client: EmbeddingClient) -> dict[str, list[str]]:
    """Cluster similar topics using embedding similarity.

    Returns {canonical_topic: [variant_topic, ...]}
    """
    topics = dao_atom.get_all_topics(author_name)
    if len(topics) <= 1:
        return {t: [t] for t in topics}

    # Get embeddings for all topics
    emb_list = emb_client.embed(topics)
    if not emb_list:
        return {t: [t] for t in topics}

    embeddings = np.array(emb_list, dtype=np.float32)
    embeddings_norm = embeddings / (np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10)
    sim_matrix = np.dot(embeddings_norm, embeddings_norm.T)

    # Greedy clustering
    assigned = set()
    clusters = {}

    for i, topic in enumerate(topics):
        if i in assigned:
            continue
        cluster = [topic]
        assigned.add(i)
        for j in range(i + 1, len(topics)):
            if j not in assigned and sim_matrix[i, j] > TOPIC_SIMILARITY_THRESHOLD:
                cluster.append(topics[j])
                assigned.add(j)
        canonical = max(cluster, key=len)  # longest topic as canonical
        clusters[canonical] = cluster

    return clusters


def run_triple_validation(topic: str, atoms: list[dict], llm: LLMClient,
                          author_models: list[dict]) -> dict:
    """Run triple validation on a topic's atoms.

    Returns {cross_domain: bool, predictability: int, exclusivity: int, passed_all: bool}
    """
    results = {"cross_domain": False, "predictability": 0, "exclusivity": 0}

    # Validation 1: Cross-domain (SQL, no LLM)
    article_ids = set(a["article_id"] for a in atoms)
    if len(atoms) >= MERGE_TRIGGER_COUNT and len(article_ids) >= 2:
        results["cross_domain"] = True

    # Combine atom contents for validation
    combined = "\n".join(
        f"[{a['type']}] {a['content'][:300]}" for a in atoms[:10]
    )

    # Validation 2: Predictability (LLM)
    try:
        val_sys = (Path(__file__).parent.parent / "prompts" / "triple_validation.txt") \
            .read_text(encoding="utf-8")
        val_user = f"""观点:
{combined}

虚构一个新场景: 请评估该观点在面对新的话题/场景时能否产生一致判断？

作者的其他已知观点 (用于校准):
{chr(10).join(f'- {m["title"]}: {m["content_md"][:200]}' for m in author_models[:5])}"""

        val_schema = {
            "name": "validation",
            "schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "predictable": {"type": "boolean"},
                    "reasoning": {"type": "string"},
                },
                "required": ["score", "predictable"],
            },
        }
        result = llm.chat(val_sys, val_user, val_schema, temperature=0.3)
        results["predictability"] = result.get("score", 0)
    except Exception:
        results["predictability"] = 0

    # Validation 3: Exclusivity (LLM, internal consistency check)
    if author_models:
        try:
            exc_sys = "你是观点独特性评估器。判断一个候选观点是否具有作者特异认知特征。"
            exc_user = f"""候选观点:
{combined}

该作者已确认的心智模型 (用于判断内部一致性):
{chr(10).join(f'- {m["title"]}' for m in author_models[:5])}

评估维度:
1. 具体性: 是否包含具体的判断标准/方法步骤？
2. 内部一致性: 是否与作者其他心智模型在逻辑上一致？
3. 可证伪性: 是否可能被证据推翻？

评分 1-5。"""

            exc_schema = {
                "name": "exclusivity",
                "schema": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "integer", "minimum": 1, "maximum": 5},
                        "specific": {"type": "boolean"},
                        "consistent": {"type": "boolean"},
                        "falsifiable": {"type": "boolean"},
                    },
                    "required": ["score"],
                },
            }
            result = llm.chat(exc_sys, exc_user, exc_schema, temperature=0.3)
            results["exclusivity"] = result.get("score", 0)
        except Exception:
            results["exclusivity"] = 0
    else:
        results["exclusivity"] = 3  # No existing models to compare, default pass

    # Overall pass: cross_domain=True AND predictability>=4 AND exclusivity>=4
    results["passed_all"] = (
        results["cross_domain"]
        and results["predictability"] >= 4
        and results["exclusivity"] >= 4
    )
    return results


def merge_topic_to_l2(author_name: str, topic: str, atom_ids: list[int],
                      llm: LLMClient, article_data: list[dict]) -> int | None:
    """Merge atoms of one topic into an L2 mental model. Returns model_id or None."""
    atoms = [dao_atom.get_atom(aid) for aid in atom_ids]
    atoms = [a for a in atoms if a is not None]
    if not atoms:
        return None

    # Sort by content type priority
    type_order = {"method": 0, "value": 1, "counter": 2, "fact": 3, "assumption": 4}
    atoms.sort(key=lambda a: type_order.get(a["type"], 5))

    # Build atom text
    atoms_text = "\n\n".join(
        f"[{a['type'].upper()}] {a['content']} (文章{a['article_id']}, {a.get('published_at','?')})"
        for a in atoms
    )

    published_dates = [a.get("published_at") for a in atoms if a.get("published_at")]
    first_seen = min(published_dates) if published_dates else "?"
    last_seen = max(published_dates) if published_dates else "?"

    article_refs = "\n".join(
        f"- [{d.get('title','?')}]({d.get('url','#')}) · {d.get('published_at','?')}"
        for d in article_data if d["id"] in {a["article_id"] for a in atoms}
    )

    user_prompt = MERGE_L2_USER.format(
        topic=topic,
        atoms_text=atoms_text[:8000],
        evidence_count=len(atoms),
        article_count=len(set(a["article_id"] for a in atoms)),
        first_seen=first_seen,
        last_seen=last_seen,
        article_refs=article_refs[:2000],
    )

    try:
        result = llm.chat(MERGE_L2_SYSTEM, user_prompt, MERGE_L2_SCHEMA, temperature=0.5, max_tokens=4096)
    except Exception as e:
        print(f"  [!] LLM 归并失败: {e}")
        return None

    title = result.get("title", topic)
    content_md = result.get("content_md", "")

    # Save MD snapshot
    author_dir = AUTHORS_DIR / author_name / "models"
    author_dir.mkdir(parents=True, exist_ok=True)
    safe_topic = "".join(c if c.isalnum() or c in "._- " else "_" for c in topic)
    md_path = author_dir / f"{safe_topic}.md"
    md_path.write_text(content_md, encoding="utf-8")

    # Insert into DB
    model_id = dao_model.insert_model(
        author_name=author_name,
        topic=topic,
        title=title,
        content_md=content_md,
        md_path=str(md_path),
        evidence_count=len(atoms),
        article_count=len(set(a["article_id"] for a in atoms)),
        first_seen_at=first_seen,
    )

    # Mark atoms as merged
    dao_atom.set_merged(atom_ids, model_id)

    return model_id


def run_merge(author_name: str, topic_filter: str = None):
    """Full L1→L2 merge pipeline."""
    print(f"\n{'='*60}")
    print(f"L1→L2 归并: {author_name}")
    print(f"{'='*60}")

    llm, emb_cfg = load_llm()
    if not llm:
        print("[!] 未配置 LLM，无法执行归并")
        return

    emb_client = EmbeddingClient(
        api_base=emb_cfg.get("api_base", ""),
        api_key=emb_cfg.get("api_key", ""),
        model=emb_cfg.get("model", "text-embedding-3-small"),
    )

    # Ensure author_dir exists
    (AUTHORS_DIR / author_name / "models").mkdir(parents=True, exist_ok=True)

    # Get existing models for validation
    existing_models = dao_model.list_models(author_name)

    # Get all articles for reference
    articles = dao_article.list_articles(author_name)
    article_map = {a["id"]: a for a in articles}

    # Step 1: Topic clustering
    print("\n[1/3] Topic 聚类...")
    topic_clusters = cluster_topics(author_name, emb_client)
    print(f"  原始 topic 数: {len(dao_atom.get_all_topics(author_name))}")
    print(f"  聚类后 topic 数: {len(topic_clusters)}")

    # Show clusters with >1 variant
    for canonical, variants in topic_clusters.items():
        if len(variants) > 1:
            print(f"    {canonical} <- {[v for v in variants if v != canonical]}")

    # Update atom topics to canonical
    for canonical, variants in topic_clusters.items():
        for variant in variants:
            if variant != canonical:
                atoms = dao_atom.list_atoms_by_topic(author_name, variant)
                for a in atoms:
                    dao_atom.update_topic(a["id"], canonical)

    # Step 2: Triple validation + trigger check
    print("\n[2/3] 三重验证 + 归并触发检查...")
    topic_counts = dao_atom.count_atoms_by_topic(author_name)

    merge_candidates = []
    skipped = []
    for tc in topic_counts:
        topic = tc["topic"]
        if topic_filter and topic != topic_filter:
            continue

        if tc["cnt"] < MERGE_TRIGGER_COUNT:
            skipped.append((topic, tc["cnt"], "原子数不足"))
            continue

        # Check if already has a model
        if dao_model.model_exists(author_name, topic):
            skipped.append((topic, tc["cnt"], "已有模型"))
            continue

        atoms = dao_atom.list_atoms_by_topic(author_name, topic)
        validation = run_triple_validation(topic, atoms, llm, existing_models)

        print(f"  {topic}: atoms={tc['cnt']} articles={tc['article_cnt']} "
              f"cross={validation['cross_domain']} pred={validation['predictability']} "
              f"excl={validation['exclusivity']} → {'PASS' if validation['passed_all'] else 'FAIL'}")

        if validation["passed_all"]:
            merge_candidates.append((topic, [a["id"] for a in atoms], validation))
        else:
            skipped.append((topic, tc["cnt"], "三重验证未通过"))

    if skipped:
        print(f"\n  跳过: {len(skipped)} 个 topic")
        for t, cnt, reason in skipped[:10]:
            print(f"    {t} ({cnt} atoms): {reason}")

    # Step 3: Merge
    print(f"\n[3/3] 归并 {len(merge_candidates)} 个 topic...")
    if not merge_candidates:
        print("  无符合条件的 topic 需要归并")
        return

    new_models = 0
    for topic, atom_ids, validation in merge_candidates:
        # Get article data for references
        atom_article_ids = set()
        for aid in atom_ids:
            a = dao_atom.get_atom(aid)
            if a:
                atom_article_ids.add(a["article_id"])
        relevant_articles = [article_map[aid] for aid in atom_article_ids if aid in article_map]

        print(f"\n  归并: {topic} ({len(atom_ids)} atoms)...")
        model_id = merge_topic_to_l2(author_name, topic, atom_ids, llm, relevant_articles)

        if model_id:
            # Update validation results
            dao_model.update_model(model_id, triple_check=validation)
            new_models += 1
            print(f"  [OK] model_id={model_id}")
        else:
            print(f"  [FAIL]")

    # Update profile
    dao_author.update_counts(
        author_name,
        l2_model_count=dao_model.count_models(author_name),
        atom_count=dao_atom.count_atoms(author_name),
    )
    dao_author.set_distilled(author_name)

    print(f"\n归并完成: 新增 {new_models} 个心智模型")
    print(f"L2 总数: {dao_model.count_models(author_name)}")


def main():
    parser = argparse.ArgumentParser(description="L1→L2 归并")
    parser.add_argument("--author", required=True, help="作者名")
    parser.add_argument("--topic", help="仅归并指定 topic")
    args = parser.parse_args()
    run_merge(args.author, args.topic)


if __name__ == "__main__":
    main()
