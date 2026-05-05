from __future__ import annotations

import httpx

from .config import settings


async def call_llm(messages: list[dict], purpose: str) -> str:
    cfg = settings()
    if cfg["llm_api_key"]:
        try:
            base = cfg["llm_base_url"].rstrip("/")
            async with httpx.AsyncClient(timeout=45) as client:
                res = await client.post(
                    f"{base}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {cfg['llm_api_key']}"},
                    json={
                        "model": cfg["llm_model"],
                        "messages": messages,
                        "temperature": 0.4,
                    },
                )
                res.raise_for_status()
                data = res.json()
                return data["choices"][0]["message"]["content"]
        except Exception:
            if not cfg["allow_demo_fallback"]:
                raise
    return demo_response(messages, purpose)


def demo_response(messages: list[dict], purpose: str) -> str:
    user_text = "\n".join(m["content"] for m in messages if m.get("role") == "user")[-1600:]
    if purpose == "report":
        return f"""# 项目需求分析报告

## 背景
围绕用户提供的会议内容与知识库材料，系统识别出一个需要被结构化沉淀的产品需求场景。

## 目标
- 明确业务目标、核心用户和关键功能边界
- 输出可评审、可拆解、可追踪的 PRD 文档
- 将不确定问题显式列出，降低后续研发返工

## 功能列表
- 用户登录注册、JWT 鉴权与数据隔离
- 多轮会议对话记录与上下文记忆
- 私人知识库上传、解析、检索与引用
- 多 Agent 分工协作生成需求分析、计划和报告
- Markdown 与 PDF 报告导出
- 管理后台查看用户、日志、限额和系统配置

## 用户故事
- 作为产品经理，我希望上传会议资料并对话澄清需求，以便快速生成 PRD。
- 作为管理员，我希望查看调用日志并调整限额，以便控制 Demo 或 SaaS 成本。

## 技术方案
后端采用 FastAPI + SQLite，Agent 层按 Supervisor、Analyzer、RAG、Requirement、Planner、Writer、Critic 串联。前端采用 React + TypeScript，实现用户端工作台与管理后台。

## 数据结构设计
核心表包括 users、usage_limits、usage_logs、sessions、messages、kb_files、kb_chunks、reports。

## 风险分析
- 外部大模型 API 不稳定时需要本地降级
- 上传文档质量差会影响 RAG 检索准确率
- 每日调用限额需要在真实商业化时接入支付或套餐系统

## 待确认问题
- 目标行业与典型会议场景是否固定
- PDF 是否需要企业模板、页眉页脚和水印
- 知识库是否需要团队共享空间

## 任务拆解
1. 完成用户、会话和限额系统
2. 完成知识库上传解析和检索
3. 完成多 Agent 工作流与报告生成
4. 完成管理后台和导出能力

> 本报告由 MeetingMind Agent Pro Demo 工作流生成。输入摘要：{user_text[:300]}
"""
    if purpose == "summary":
        return f"根据当前会议内容，初步总结为：\n\n- 核心诉求：{user_text[:160] or '需要围绕产品需求进行分析和沉淀'}\n- 建议下一步：补充目标用户、业务约束、验收标准和排期优先级。\n- 可交付物：PRD、任务拆解、风险清单。"
    return f"我已记录这轮信息，并会从需求背景、目标用户、功能边界、风险和待确认问题继续分析。\n\n当前要点：{user_text[:260] or '请描述会议内容或上传知识库文件。'}"
