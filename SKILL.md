---
name: author-mind
version: "0.1.1"
description: >
  作者心智提炼系统。从公众号/知乎文章中提取作者的知识体系，
  经过 L0-L4 五层渐进式提炼，生成认知画像、心智模型和写作镜像。
trigger:
  - /extract [合集URL] # 获取文章 + L1 提取
  - /extract author [作者名] # 对已入库文章做 L1 提取
  - /merge [作者名] # L1→L2 归并
  - /persona [作者名] # L2→L3 认知画像
  - /mirror [作者名] # L4 写作镜像
  - /author status [作者名] # 查看进度
dependencies:
  python: ">=3.10"
  packages: [openai, httpx, numpy, pyyaml]
  external: astromind-praxis (>=0.1.2)
  database: ~/.astromind-praxis/astromind_praxis.db
note: |
  脚本必须从 scripts/ 目录内运行（模块使用裸导入）:
    cd D:\workdata\shared\skills\author-mind\scripts
    python extract.py --author "作者名"
  or set PYTHONPATH:
    PYTHONPATH=scripts python -c "from extract import ..."
  v6.1 表（articles/knowledge_atoms/mental_models/author_profiles）
  在首次连接时自动创建，无需手动迁移。
---

# 作者心智提炼系统 (author-mind)

从公众号/知乎文章中提炼作者的显性知识+隐性认知，通过 astromind 教学引擎让用户逐步掌握。

## 架构

```
L0 原始文章 → L1 原子知识点 → L2 心智模型 → L3 认知画像 → L4 写作镜像
   (全文)      (6类型Schema)    (三重验证)     (persona.md)   (writing-mirror.md)
```

## 快速开始

```bash
# 获取合集文章
python scripts/fetch_articles.py --author "作者名" "https://mp.weixin.qq.com/mp/appmsgalbum?..."


# L1 提取（对 L0 中的文章）
python scripts/extract.py --author "作者名"

# 查看进度
python scripts/status.py "作者名"

# L1→L2 归并
python scripts/merge_to_l2.py --author "作者名"

# L2→L3 认知画像
python scripts/extract_l3.py --author "作者名"

# L4 写作镜像
python scripts/extract_l4.py --author "作者名"
```

## 输入通道

| 通道 | 优先级 | 说明 |
|------|--------|------|
| 合集 API + WebFetch | P0 | 合集 API 获取文章列表，WebFetch 抓全文 |
| 用户粘贴 | P0 | 直接粘贴全文到对话中 |
| wewe-rss | P1 | 微信读书 API（备选） |

## 目录结构

```
author-mind/
├── SKILL.md
├── scripts/
│   ├── fetch_articles.py     # 合集 API 分页获取文章列表
│   ├── extract.py            # L1 提取（6 类型 Schema）
│   ├── dedup.py              # 去重（向量预筛 + LLM 精判）
│   ├── merge_to_l2.py        # L1→L2 归并（topic聚类+三重验证）
│   ├── extract_l3.py         # L2→L3 提炼（认知画像+表达DNA）
│   ├── extract_l4.py         # L4 写作镜像
│   └── status.py             # 进度查看
├── prompts/
│   ├── extract_l1.txt        # L1 提取 prompt
│   ├── triple_validation.txt # 三重验证 prompt
│   └── merge_l2.txt          # L1→L2 归并 prompt
├── references/
│   └── extraction-schema.md  # 6 类型 Schema 说明
└── requirements.txt
```

## 与 astromind 的关系

- `author-mind` 写入 astromind DB 的 articles/knowledge_atoms/mental_models 表
- `astromind` 教练模块读取这些表实现 `train_by_author()` 和 `write_as_author()`
- 共享 `~/.astromind-praxis/astromind_praxis.db`
