"""L1 去重: 向量预筛 + LLM 精判.

流程:
  1. 加载作者所有 L1 原子
  2. 对每条新原子 → vec_index 搜索 top-5 相似已有原子
  3. 相似度 > 0.85 → LLM 判断是否同一知识点
  4. 是 → 更新 evidence count, 不新增
  5. 否 → 新增入库

使用方式:
  python scripts/dedup.py --author "作者名"
"""

import argparse
import json
import sys
from pathlib import Path

import dao_atom
import dao_author
from database import get_connection
from llm_client import LLMClient

from vec_index import AtomVectorIndex, EmbeddingClient

DEDUP_SYSTEM = """你是知识去重判断器。判断两条原子知识点是否表达相同信息。

判定标准:
- 相同: 核心事实/观点/方法一致，只是表述方式不同
- 不同: 核心内容不同，或者同一主题下的不同角度/不同细节

回复格式: {"same": true/false, "reason": "一句话说明理由"}"""

DEDUP_SCHEMA = {
    "name": "dedup_judge",
    "schema": {
        "type": "object",
        "properties": {
            "same": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["same", "reason"],
    },
}

SIMILARITY_THRESHOLD = 0.85


def load_llm():
    config_path = Path.home() / ".astromind-praxis" / "config.yaml"
    if not config_path.exists():
        return None
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        llm_cfg = config.get("llm", {})
        emb_cfg = config.get("embedding", {})
        if llm_cfg.get("api_key"):
            return LLMClient(
                base_url=llm_cfg.get("base_url", ""),
                api_key=llm_cfg.get("api_key", ""),
                model=llm_cfg.get("model", ""),
            ), emb_cfg
    except Exception:
        pass
    return None, {}


def run_dedup(author_name: str):
    """Run deduplication on all unindexed atoms for an author."""
    print(f"\n{'='*60}")
    print(f"L1 去重: {author_name}")
    print(f"{'='*60}")

    llm, emb_cfg = load_llm()
    emb_client = EmbeddingClient(
        api_base=emb_cfg.get("api_base", ""),
        api_key=emb_cfg.get("api_key", ""),
        model=emb_cfg.get("model", "text-embedding-3-small"),
    )

    if not emb_client.is_configured():
        print("[!] embedding API 未配置，无法进行向量去重")
        print("    在 ~/.astromind-praxis/config.yaml 中配置 embedding 段")
        return

    vec_idx = AtomVectorIndex(author_name, emb_client)
    existing_ids = vec_idx.get_all_atom_ids()

    # Get all atoms, filter unindexed
    all_atoms = dao_atom.list_atoms_by_author(author_name)
    new_atoms = [a for a in all_atoms if a["id"] not in existing_ids]

    if not new_atoms:
        print("所有原子已索引，无需去重")
        return

    print(f"已有索引: {vec_idx.size} 条")
    print(f"新增待索引: {len(new_atoms)} 条\n")

    added = 0
    deduped = 0
    skipped = 0

    for i, atom in enumerate(new_atoms):
        content = atom["content"]

        # Step 1: Vector pre-screen
        similar = vec_idx.search(content, top_k=5, threshold=SIMILARITY_THRESHOLD)

        is_duplicate = False
        matched_id = None

        if similar and llm:
            # Step 2: LLM judge for high-similarity candidates
            candidate_texts = []
            for s in similar[:3]:  # Only check top 3
                c = dao_atom.get_atom(s["atom_id"])
                if c:
                    candidate_texts.append(f"[{s['atom_id']}] sim={s['similarity']:.3f}: {c['content'][:200]}")

            if candidate_texts:
                user_prompt = f"新知识:\n{content}\n\n候选相似:\n" + "\n".join(candidate_texts)
                try:
                    result = llm.chat(DEDUP_SYSTEM, user_prompt, DEDUP_SCHEMA, temperature=0.1)
                    if result.get("same"):
                        is_duplicate = True
                        matched_id = similar[0]["atom_id"]
                except Exception as e:
                    print(f"  [!] LLM 去重判定失败: {e}")
                    # Fall back to threshold-only (⚠ 向量相似度≠知识等价性, 可能误判)
                    if similar[0]["similarity"] > 0.95:
                        print(f"  [W] 仅凭向量相似度 {similar[0]['similarity']:.3f} 判定重复, 请人工复核")
                        is_duplicate = True
                        matched_id = similar[0]["atom_id"]

        if is_duplicate:
            # Update time tracking on existing atom
            if atom.get("published_at"):
                dao_atom.update_atom_time(matched_id, last_confirmed_at=atom["published_at"])
            deduped += 1
        else:
            # Add to index
            ok = vec_idx.add(atom["id"], content)
            if ok:
                added += 1
            else:
                skipped += 1

        if (i + 1) % 20 == 0:
            print(f"  进度: {i+1}/{len(new_atoms)}  add={added} dedup={deduped}")

    print(f"\n去重完成: 新增索引 {added} | 去重合并 {deduped} | 跳过 {skipped}")
    print(f"索引总数: {vec_idx.size}")


def main():
    parser = argparse.ArgumentParser(description="L1 去重")
    parser.add_argument("--author", required=True, help="作者名")
    args = parser.parse_args()
    run_dedup(args.author)


if __name__ == "__main__":
    main()
