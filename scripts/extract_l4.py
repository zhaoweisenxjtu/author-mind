"""L4 写作镜像: 从 L2+L3 生成可加载的 writing-mirror.md system prompt.

流程:
  1. 读取 L2 心智模型 + L3 persona
  2. LLM 合成完整的写作镜像 Skill 文件
  3. 保存 writing-mirror.md
  4. 可选: 运行质量校验

使用方式:
  python scripts/extract_l4.py --author "作者名"
  python scripts/extract_l4.py --author "作者名" --validate  # 含质量校验
"""

import argparse
import json
import sys
from pathlib import Path

import dao_model
import dao_atom
import dao_author
from database import get_connection
from llm_client import LLMClient

AUTHORS_DIR = Path.home() / ".astromind-praxis" / "authors"

L4_SYSTEM = """你是写作教练和风格分析师。基于作者的认知体系和表达特征，生成一个完整的"写作镜像"文件。

写作镜像是一个可直接作为 LLM system prompt 加载的文件，使 LLM 能够以目标作者的风格、思维方式和表达习惯进行写作。"""

L4_USER = """## 作者认知画像 (L3 persona)

{persona_text}

## 心智模型 (L2)

{models_text}

## 表达DNA片段

{styles_text}

请生成写作镜像文件，输出 Markdown:

# 写作镜像: {author_name}

## 认知引擎

### 核心心智模型
[从 L2 加载，概括每个模型的核心理念，3-5 条]
[每条含: 模型名称 + 一句话核心理念 + 适用场景]

### 判断启发式
[从 L3 加载，作者做决策时的快捷规则]
[格式: "遇到 X → 先看 Y → 如果 Z 则 A 否则 B"]

### 反模式与诚实边界
[作者明确不会做的事、不会说的话]

## 表达DNA

### 语气模式
[从 L3: 笃定/调侃/循循善诱/尖锐]

### 句式偏好
[从 L3: 反问/断言/比喻/调侃 占比]
[写作时应自然地混用，不机械套用比例]

### 叙事结构
[从 L3 加载，含典型段落组织方式]

### 标志性词汇
[从 L3 加载，含使用建议: 哪个词适合哪种场景]

## 知识边界

### 擅长领域
[根据 L2 topic 统计]

### 不讨论领域
[从 L3 盲区]

### 诚实边界
[遇到没讨论过的话题 → 用作者的方式表达不确定]

## 写作指令

当用户要求你就某个新主题以「{author_name}」的风格写作时:

1. **认知先行**: 先用认知引擎推导立场和框架
   - 面对这个话题，作者会首先关注什么？
   - 作者会用什么心智模型来分析？
   - 作者的判断启发式会产生什么立场？

2. **表达包装**: 用表达DNA包装语言
   - 选择匹配该话题的句式（断言型观点用短句爆破，分析型用长句铺陈）
   - 自然融入 1-2 个标志性词汇
   - 匹配语气模式

3. **结构组织**: 遵循叙事结构偏好
   - 按作者偏好的段落结构组织文章
   - 如作者喜欢引用历史典故，考虑相关典故

4. **诚实检查**: 如同作者本人会做的那样
   - 不确定的地方诚实表达
   - 不越界到盲区领域假装精通

## 使用方式

将此文件作为 system prompt 加载到 LLM，然后输入:
"请以「{author_name}」的风格，写一篇关于「{{新主题}}」的文章。"

产出应满足:
- 读过 100 字就能认出是谁写的（表达DNA辨识度）
- 分析逻辑符合其心智模型（认知一致性）
- 遇到不熟悉的领域诚实表达不确定（诚实边界）

以 JSON 格式输出。"""

L4_SCHEMA = {
    "name": "writing_mirror",
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content_md": {"type": "string"},
        },
        "required": ["title", "content_md"],
    },
}

# ── Quality validation ──
# NOTE: Two separate scoring layers exist:
#   1. validate_mirror() → "faithful" check (L4 镜像是否忠实于 persona)
#   2. _validate_writing() in astromind workflow.py → expression/cognition/honesty (L4 生成的仿写文章质量)
# Layer 1 检查镜像本身, Layer 2 检查镜像输出. 两者互补, 不冲突.

VALIDATE_SYSTEM = """你是写作质量评估器。评估一篇仿写文章是否成功模仿了目标作者的风格和思维方式。
从以下三个维度评分（各 1-5 分），不评判内容好坏，只评判模仿准确性。"""

VALIDATE_USER = """目标作者: {author_name}
目标作者表达DNA (来自 L3 persona): {expression_dna}
目标作者认知体系 (来自 L2 心智模型列表): {mental_models_summary}

待评估仿写文章:
{generated_article}

评估维度:
1. 表达DNA辨识度 (1-5):
   - 1-2: 读不出来是谁写的，语气/句式/词汇无辨识度
   - 3: 部分片段像作者，但整体不稳定
   - 4-5: 读过 100 字就能认出是谁写的
   - 评分依据: 对照目标作者的句式偏好、标志性词汇、语气模式、叙事结构

2. 认知一致性 (1-5):
   - 1-2: 文章的逻辑/立场与作者已知心智模型矛盾
   - 3: 无矛盾但也没有体现作者的思维特征
   - 4-5: 分析框架和判断逻辑符合作者的认知体系
   - 评分依据: 对照作者的核心心智模型和判断启发式

3. 诚实边界 (1-5):
   - 1-2: 在不熟悉的领域假装很懂，或超出了作者的讨论范围但言之凿凿
   - 3: borderline
   - 4-5: 遇到没讨论过的点诚实表达不确定，或用作者的方式说"这超出我的范围"
   - 评分依据: 对照作者的反模式/诚实边界定义

综合判定:
- 3 项均 >= 3 → 通过，可直接交付
- 1 项 < 3 → 通过但标记弱项，建议人工复核
- 2+ 项 < 3 → 不通过，需要调整 prompt 或补充素材后重试"""

VALIDATE_SCHEMA = {
    "name": "quality_report",
    "schema": {
        "type": "object",
        "properties": {
            "expression_score": {"type": "integer", "minimum": 1, "maximum": 5},
            "expression_notes": {"type": "string"},
            "cognition_score": {"type": "integer", "minimum": 1, "maximum": 5},
            "cognition_notes": {"type": "string"},
            "honesty_score": {"type": "integer", "minimum": 1, "maximum": 5},
            "honesty_notes": {"type": "string"},
            "verdict": {"type": "string", "enum": ["pass", "pass_with_warnings", "fail"]},
            "improvement_suggestions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["expression_score", "cognition_score", "honesty_score", "verdict"],
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


def extract_l4(author_name: str, validate: bool = False):
    """Generate L4 writing mirror from L2+L3."""
    print(f"\n{'='*60}")
    print(f"L4 写作镜像: {author_name}")
    print(f"{'='*60}")

    llm = load_llm()
    if not llm:
        print("[!] 未配置 LLM，无法生成")
        return

    # Check prerequisites
    author_dir = AUTHORS_DIR / author_name
    persona_path = author_dir / "persona.md"
    if not persona_path.exists():
        print("[!] persona.md 不存在，请先运行 extract_l3.py")
        return

    persona_text = persona_path.read_text(encoding="utf-8")

    models = dao_model.list_models(author_name)
    if not models:
        print("[!] 无心智模型，无法生成写作镜像")
        return

    print(f"L2 模型: {len(models)} 个")

    models_text = "\n\n".join(
        f"### {m['title']}\n{m['content_md'][:800]}"
        for m in models[:10]
    )

    # Collect style atoms for reference
    style_atoms = dao_atom.list_atoms_by_author(author_name, "style")
    styles_text = "\n".join(
        f"- {a.get('content', '')[:120]}"
        for a in style_atoms[:20]
    ) if style_atoms else "（暂无 style 数据）"

    print(f"Style 原子: {len(style_atoms)} 条")

    # Generate writing mirror
    user_prompt = L4_USER.format(
        author_name=author_name,
        persona_text=persona_text[:8000],
        models_text=models_text[:4000],
        styles_text=styles_text[:2000],
    )

    print("\n调用 LLM 生成 writing-mirror...")
    try:
        result = llm.chat(L4_SYSTEM, user_prompt, L4_SCHEMA, temperature=0.5, max_tokens=8192)
    except Exception as e:
        print(f"[!] LLM 调用失败: {e}")
        return

    content_md = result.get("content_md", "")

    # Save
    mirror_path = author_dir / "writing-mirror.md"
    mirror_path.write_text(content_md, encoding="utf-8")
    print(f"writing-mirror.md 已保存: {mirror_path}")

    # Update author profile (DB)
    dao_author.update_profile(author_name, l4_available=1, mirror_md=content_md)

    # Quality validation
    if validate:
        print("\n--- 质量校验 ---")
        quality = validate_mirror(author_name, content_md, persona_text, models, llm)
        if quality:
            print(f"  表达DNA辨识度: {quality.get('expression_score')}/5")
            print(f"  认知一致性: {quality.get('cognition_score')}/5")
            print(f"  诚实边界: {quality.get('honesty_score')}/5")
            print(f"  判定: {quality.get('verdict')}")

            # Save quality report
            report_path = author_dir / "quality-report.json"
            report_path.write_text(
                json.dumps(quality, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"  质量报告: {report_path}")

    print(f"\n写作镜像生成完成: {mirror_path}")

    return mirror_path


def validate_mirror(author_name: str, mirror_content: str,
                    persona_text: str, models: list[dict],
                    llm: LLMClient) -> dict | None:
    """Validate the writing mirror by generating a test article and scoring it.

    Note: This is a self-consistency check on the mirror itself,
    not a test of generated output. For full validation with test output,
    use write_as_author() in astromind.
    """
    # Check mirror has all required sections
    required_sections = [
        "认知引擎", "表达DNA", "知识边界", "写作指令",
    ]

    missing = [s for s in required_sections if s not in mirror_content]

    # LLM-based quality check: does the mirror faithfully represent the persona?
    check_prompt = f"""请评估这个写作镜像文件是否忠实地反映了作者的 persona 和心智模型。

作者 persona 摘要:
{persona_text[:2000]}

写作镜像文件:
{mirror_content[:4000]}

请检查:
1. 镜像中的认知引擎是否与 persona 中的判断逻辑链一致？
2. 表达DNA 特征是否与 persona 描述一致？
3. 诚实边界是否合理？

输出 JSON:
{{"faithful": true/false, "issues": ["..."], "score": 1-5}}"""

    check_schema = {
        "name": "mirror_check",
        "schema": {
            "type": "object",
            "properties": {
                "faithful": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["faithful", "score"],
        },
    }

    try:
        result = llm.chat(
            "你是文档质量检查器。评估写作镜像文件的质量和忠实度。",
            check_prompt, check_schema, temperature=0.2,
        )
        return {
            "missing_sections": missing,
            "faithful": result.get("faithful", False),
            "issues": result.get("issues", []),
            "score": result.get("score", 0),
        }
    except Exception as e:
        print(f"  [!] 质量校验失败: {e}")
        return {"missing_sections": missing, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="L4 写作镜像生成")
    parser.add_argument("--author", required=True, help="作者名")
    parser.add_argument("--validate", action="store_true", help="运行质量校验")
    args = parser.parse_args()
    extract_l4(args.author, validate=args.validate)


if __name__ == "__main__":
    main()
