---
name: <task-slug>
description: <一句话概括问题本质与可复用结论>
category: <业务类别>
techniques:
  - <技术或方法 A>
  - <技术或方法 B>
related:
  - <other-note-name>
# superseded_by: <note-name>   # 可选：本 note 已被哪篇取代（被取代后索引移入归档区，正文保留）
---

<!--
最小 frontmatter 规约：
- name / description / category 必须是单行值。
- techniques / related 使用两空格缩进的 "- " 列表。
- related 必须双向。无关联时删除示例项，保留空 related 列表。
- 不得写入可直接使用的凭据、token、cookie、私钥、会话值或其他需保密业务数据。
-->

# <任务/经验标题> (<yy-mm-dd>)

## 类别

<业务域 / 子类别>

## 关键特征

- <哪些输入、现象或环境特征能让后续 Agent 识别同类问题>

## 有效方法

1. <成功方法、关键条件与验证证据>

## 失效方法 / 弯路

- <失败路径、失败原因与何时应重试>

## 陷阱

- <容易误判、漂移或引发副作用的点>

## 可复用要点

1. <脱离本任务仍成立的经验>

## 本次查阅的 notes

- `notes/<name>-<date>.md` — ✅ 正向：<帮助> / ❌ 反向：<误导> / ➖ 无关

## 技术扩展推荐

- **<other-note-name>**（`notes/<file>.md`）— 共享方法：<关联原因>

## 敏感信息处理

- <不适用；或只记录秘密类型、来源、受控位置/不可逆指纹与脱敏方式>

## 环境

- <Python 模式、工具/MCP 版本、skills revision/指纹、任务目录>
