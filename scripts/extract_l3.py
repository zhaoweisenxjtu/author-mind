"""L2→L3 提炼: 从心智模型生成作者认知画像 + 表达DNA.

流程:
  1. 读取作者所有 L2 心智模型 (mental_models 表)
  2. 读取 L1 style 类型原子 → 聚合表达DNA
  3. LLM 综合提炼 → persona.md
  4. 写入 author_profiles (l3_available=1)

使用方式:
  python scripts/extract_l3.py --author "作者名"
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import dao_model
import dao_atom
import dao_author
from database import get_connection
from llm_client import LLMClient

AUTHORS_DIR = Path.home() / ".astromind-praxis" / "authors"

L3_SYSTEM = """你是认知心理学专家。基于作者的多条心智模型和表达特征，提炼一份结构化的作者认知画像。

这份画像需要揭示：作者如何思考、如何判断、如何表达，以及这三者之间的内在联系。"""

L3_USER = """## 作者心智模型

{models_text}

## 表达DNA片段（来自原文的标志性语句）

{styles_text}

## 文章覆盖领域统计

{domain_stats}

请提炼该作者的认知画像，输出以下结构:

# 认知画像: {author_name}

## 判断逻辑链
[面对问题X → 先看A → 再查B → 决策。描述作者的典型思维路径，3-5 步]

## 隐含假设
[跨多个心智模型反复出现的底层信念，即使作者没有明确说出来的前提。含支撑线索]

## 价值取向
- 极度重视: [3-5 项]
- 明确排斥: [3-5 项]
- 内在矛盾: [作者不同文章中观点不一致之处，如存在]

## 思维盲区
[作者几乎不讨论的领域、可能的过度简化、习惯性忽视的角度]

## 判断启发式 (5-10 条)
[作者做决策时的快捷规则，格式: "遇到 X → 先看 Y → 如果 Z 则 A 否则 B"]

## 反模式 / 诚实边界
[作者明确不会做的事、不会说的话、承认的局限]

## 可教授性评估
- 显性知识密度: high/medium/low — 方法步骤是否可以直接学习
- 隐式认知提取难度: high/medium/low — 是否需要大量案例才能体会
- 推荐学习路径: [阶段1 → 阶段2 → 阶段3]

## 表达DNA

### 高频句式
| 类型 | 占比 | 示例 |
|------|------|------|
| 反问 | X% | ... |
| 断言 | X% | ... |
| 比喻 | X% | ... |
| 调侃 | X% | ... |

### 标志性词汇
[作者反复使用的高辨识度词语，3-8 个，含使用频率和典型语境]

### 语气模式
[笃定/调侃/循循善诱/尖锐，含具体表现]

### 节奏特征
[短句爆破/长句铺陈/排比/留白，含典型句长和段落结构]

### 叙事结构偏好
[原文→译文→解析 / 论点→论证→案例 / 故事→道理 / 其他]

以 JSON 格式输出。"""

L3_SCHEMA = {
    "name": "persona",
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
        return None
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        llm_cfg = config.get("llm", {})
        if llm_cfg.get("api_key"):
            return LLMClient(llm_cfg["base_url"], llm_cfg["api_key"], llm_cfg["model"])
    except Exception:
        pass
    return None


def collect_style_atoms(author_name: str) -> list[dict]:
    """Collect all style-type atoms for expression DNA analysis."""
    atoms = dao_atom.list_atoms_by_author(author_name, "style")
    return atoms


def classify_styles(atoms: list[dict]) -> dict:
    """Classify style atoms by pattern type and extract statistics."""
    patterns = Counter()
    examples = []
    all_sentences = []

    for a in atoms:
        content = a.get("content", "")
        # content for style atoms is the original sentence
        if content:
            all_sentences.append(content)

    # Count patterns
    for a in atoms:
        evidence = a.get("evidence", "")
        if evidence:
            try:
                meta = json.loads(evidence) if isinstance(evidence, str) else evidence
                pattern = meta.get("pattern", "断言")
                patterns[pattern] += 1
                if len(examples) < 20:
                    examples.append({"sentence": a.get("content", ""), "pattern": pattern})
            except (json.JSONDecodeError, TypeError):
                patterns["断言"] += 1

    total = sum(patterns.values()) or 1

    result = {
        "total_styles": len(atoms),
        "pattern_distribution": {k: round(v / total * 100) for k, v in patterns.most_common()},
        "examples": examples,
        "all_sentences": all_sentences[:50],
    }
    return result


def collect_domain_stats(author_name: str, models: list[dict]) -> str:
    """Build domain coverage statistics from models and atoms."""
    topics = [m.get("topic", "") for m in models]
    all_atoms = dao_atom.list_atoms_by_author(author_name)

    # Count by type
    type_counts = Counter(a.get("type", "?") for a in all_atoms)

    lines = [
        f"L2 心智模型: {len(models)} 个",
        f"覆盖 topic: {', '.join(topics[:20])}",
        f"L1 原子总数: {len(all_atoms)}",
        f"类型分布: {dict(type_counts)}",
    ]
    return "\n".join(lines)


def extract_l3(author_name: str):
    """Run L2→L3 persona extraction."""
    print(f"\n{'='*60}")
    print(f"L2→L3 提炼: {author_name}")
    print(f"{'='*60}")

    llm = load_llm()
    if not llm:
        print("[!] 未配置 LLM，无法执行提炼")
        return

    # Load L2 models
    models = dao_model.list_models(author_name)
    if len(models) < 3:
        print(f"[!] L2 心智模型只有 {len(models)} 个，需要至少 3 个才能提炼 L3")
        return

    print(f"L2 模型数: {len(models)}")

    # Build models text for LLM
    models_text_parts = []
    for m in models:
        part = f"### {m['title']} (topic: {m['topic']})\n{m['content_md'][:1500]}"
        models_text_parts.append(part)
    models_text = "\n\n".join(models_text_parts)

    # Collect and classify style atoms
    style_atoms = collect_style_atoms(author_name)
    style_stats = classify_styles(style_atoms)
    print(f"Style 原子数: {style_stats['total_styles']}")

    styles_text = "\n".join(
        f"- [{ex['pattern']}] {ex['sentence'][:120]}"
        for ex in style_stats["examples"][:30]
    )
    if not styles_text:
        styles_text = "（暂无 style 原子数据）"

    # Domain stats
    domain_stats = collect_domain_stats(author_name, models)

    # LLM generation
    user_prompt = L3_USER.format(
        author_name=author_name,
        models_text=models_text[:10000],
        styles_text=styles_text[:3000],
        domain_stats=domain_stats,
    )

    print("\n调用 LLM 生成 persona...")
    try:
        result = llm.chat(L3_SYSTEM, user_prompt, L3_SCHEMA, temperature=0.5, max_tokens=8192)
    except Exception as e:
        print(f"[!] LLM 调用失败: {e}")
        return

    title = result.get("title", f"认知画像: {author_name}")
    content_md = result.get("content_md", "")

    # Save persona.md
    author_dir = AUTHORS_DIR / author_name
    author_dir.mkdir(parents=True, exist_ok=True)
    persona_path = author_dir / "persona.md"
    persona_path.write_text(content_md, encoding="utf-8")
    print(f"persona.md 已保存: {persona_path}")

    # Update author profile (file + DB)
    dao_author.update_profile(
        author_name,
        l3_available=1,
        persona_md=content_md,
    )

    print(f"\n提炼完成: {title}")
    print(f"文件: {persona_path}")
    print(f"字数: {len(content_md)}")

    return persona_path


def main():
    parser = argparse.ArgumentParser(description="L2→L3 认知画像提炼")
    parser.add_argument("--author", required=True, help="作者名")
    args = parser.parse_args()
    extract_l3(args.author)


if __name__ == "__main__":
    main()
