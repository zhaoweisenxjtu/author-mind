# 提取 Schema 参考

## 6 种原子知识点类型

### 1. fact — 可验证事实/数据

```json
{
  "content": "作者引用的具体事实、数据或历史事件",
  "topic": "所属主题(3-8字)",
  "source_date": "事实发生/数据的日期",
  "verifiability": "high|medium|low"
}
```

### 2. method — 方法论/框架/步骤

```json
{
  "content": "作者提出的可执行方法论",
  "topic": "所属主题",
  "steps": ["步骤1", "步骤2"],
  "applicability": "适用场景",
  "limitations": "局限性"
}
```

### 3. value — 价值判断/观点/立场

```json
{
  "content": "作者的主观判断或观点",
  "topic": "所属主题",
  "stance": "strong|moderate|speculative",
  "counter_evidence": "可能反驳该观点的证据"
}
```

### 4. assumption — 隐含前提/默认假设

```json
{
  "content": "推断出的作者隐含假设(原文未明说)",
  "topic": "所属主题",
  "supporting_clues": "支撑此推断的原文线索",
  "alternative_possible": "yes|no"
}
```

### 5. counter — 反对观点/警惕陷阱

```json
{
  "content": "作者明确反对或警惕的内容",
  "topic": "所属主题",
  "what_is_countered": "被反对的观点/行为",
  "author_alternative": "作者的替代方案"
}
```

### 6. style — 表达DNA片段

```json
{
  "sentence": "原文代表性语句(直接摘录)",
  "pattern": "反问|断言|比喻|调侃|排比|留白",
  "usage": "惯用修辞或口头禅说明"
}
```

## 提取约束

1. 每条 content <200 字，只含一个独立信息
2. 必须有 topic 标签(3-8字)
3. 不编造原文没有的信息
4. assumption 类必须标注是"推断"而非"原文陈述"
5. style 类每篇 3-5 条，只摘录最有辨识度的原句
