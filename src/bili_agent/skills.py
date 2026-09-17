from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class SkillRoute:
    name: str
    direct: bool


def classify_question(question: str) -> SkillRoute:
    """Route a question to the cheapest reliable capability."""
    text = re.sub(r"\s+", "", question.lower())
    if re.search(r"标题|up主|作者|主播是谁|发布时间|什么时候发布|时长|多长|链接|bvid|av号", text):
        return SkillRoute("metadata", True)
    if re.search(r"章节|时间线|分p|哪一段|时间点|先后顺序", text):
        return SkillRoute("timeline", True)
    if re.search(r"知识点|重点|概念|术语|方法|要点|关键内容", text):
        return SkillRoute("knowledge", True)
    if re.search(r"整体|概括|概要|摘要|总结|主要讲|讲了什么", text):
        return SkillRoute("summary", True)
    # A question that mentions subtitles can still be asking for an
    # explanation. Keep retrieval/LLM QA ahead of the transcript-only route.
    if re.search(r"为什么|为何|怎么|如何|是否|能否|原因|依据|解释|区别|影响", text):
        return SkillRoute("evidence_qa", False)
    if re.search(r"字幕|原文|逐字|完整内容|说了什么", text):
        return SkillRoute("transcript", False)
    return SkillRoute("evidence_qa", False)


def expand_with_history(question: str, history: list[dict[str, str]] | None) -> str:
    """Give short follow-ups enough terms for timestamp retrieval."""
    if not history:
        return question
    compact = re.sub(r"\s+", "", question)
    needs_context = len(compact) <= 16 or bool(re.search(r"刚才|上面|这个|那个|它|为什么|然后|继续|第二个|第三个", compact))
    if not needs_context:
        return question
    previous_user_questions = [
        item.get("content", "") for item in history[-6:] if item.get("role") == "user"
    ]
    context = " ".join(previous_user_questions[-2:]).strip()
    return f"{context} {question}".strip() if context else question


def skill_label(name: str) -> str:
    return {
        "metadata": "元数据查询",
        "summary": "总结理解",
        "timeline": "时间线整理",
        "knowledge": "知识点提取",
        "transcript": "字幕检索",
        "evidence_qa": "证据问答",
    }.get(name, name)
