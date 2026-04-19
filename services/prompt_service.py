from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Tuple

import requests

PROMPT_LIBRARY = {}


def parse_resolution(resolution: str) -> Tuple[int, int]:
    try:
        w_str, h_str = resolution.lower().split("x")
        w, h = int(w_str), int(h_str)
        if w <= 0 or h <= 0:
            raise ValueError
        return w, h
    except Exception:
        return 1024, 1024


def text_position_by_size(width: int, height: int) -> str:
    if width > height:
        return "右侧留白区"
    if height > width:
        return "顶部留白区"
    return "右上留白区"


def build_prompt(
    product_name: str,
    primary_selling: str,
    secondary_selling: str,
    other_selling: str,
    resolution: str = "1024x1024",
    display_text: str = "",
) -> str:
    product_name = (product_name or "商品").strip()
    p1 = (primary_selling or "").strip()
    p2 = (secondary_selling or "").strip()
    p3 = (other_selling or "").strip()
    segment_text = "，".join([x for x in [p1, p2, p3] if x])
    w, h = parse_resolution(resolution)
    text_pos = text_position_by_size(w, h)
    if display_text:
        text_rule = (
            f"画面仅允许出现这段文字：{display_text}，文字固定放在{text_pos}，"
            "字体清晰可读，不遮挡主体，除该文字外禁止出现任何其他文字、字母、数字、logo、水印。"
        )
    else:
        text_rule = "画面中不要出现任何文字、字母、数字、logo、水印。"
    return (
        f"电商商品主图，产品名称:{product_name}，"
        f"目标分辨率:{resolution}，按该画幅设计构图与留白，"
        f"主体突出，背景干净，构图平衡，预留文案区，细节高清，卖点信息:{segment_text}，"
        "单一产品主体，近景特写，产品占画面约70%，严禁多主体，"
        f"{text_rule}，适合平台商品首图和活动图，避免复杂背景与版权元素。"
    )


def refine_prompt_with_llm(
    base_prompt: str,
    product_name: str,
    primary_selling: str,
    secondary_selling: str,
    other_selling: str,
    resolution: str = "1024x1024",
    display_text: str = "",
) -> Tuple[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("未检测到 OPENROUTER_API_KEY，无法调用LLM润色")

    api_url_env = os.getenv("LLM_API_URL", "").strip()
    api_candidates = [x for x in [
        api_url_env or None,
        "https://openrouter.ai/api/v1/chat/completions",
    ] if x]
    model_candidates = [x for x in [
        os.getenv("LLM_MODEL", "").strip() or None,
        "openrouter/free",
    ] if x]

    system = (
        "你是资深电商视觉提示词工程师。"
        "请把用户给的电商图片提示词润色为更可执行、更稳定的图像生成提示词。"
        "要求：中文；保留原始意图；结构清晰；避免侵权与违规表述；"
        "严禁输出分析过程、解释、前后缀、英文句子；只输出最终提示词正文。"
    )
    user = (
        f"产品名称: {product_name}\n"
        f"主要卖点: {primary_selling}\n"
        f"次要卖点: {secondary_selling}\n"
        f"其他卖点: {other_selling}\n"
        f"分辨率: {resolution}\n"
        f"指定展示文字: {display_text or '无'}\n"
        f"基础提示词: {base_prompt}\n\n"
        "请生成一条更专业的最终提示词，包含：主体、场景、光线、构图、材质细节、留白文案区、质量约束、文字规则与文字位置。"
        "只输出1段中文提示词，不要输出“We need to”“分析”等内容。"
    )

    def clean_prompt_text(raw: str) -> str:
        s = (raw or "").strip()
        if not s:
            return ""
        s = s.strip().strip("\"'`")
        bad_prefixes = [
            "we need to", "let's", "analysis", "thought", "must be",
            "we should", "final prompt", "here is", "prompt:"
        ]
        low = s.lower()
        if any(low.startswith(x) for x in bad_prefixes):
            s = "\n".join([ln.strip() for ln in s.splitlines() if ln.strip()])

        lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
        zh_lines = [ln for ln in lines if re.search(r"[\u4e00-\u9fff]", ln)]
        if zh_lines:
            s = "，".join(zh_lines)

        tail_markers = [
            "check:", "检查:", "校验:", "validation:", "self-check:", "review:"
        ]
        low2 = s.lower()
        cut_pos = -1
        for m in tail_markers:
            idx = low2.find(m)
            if idx != -1 and (cut_pos == -1 or idx < cut_pos):
                cut_pos = idx
        if cut_pos != -1:
            s = s[:cut_pos].strip()

        s = re.sub(r"^[>\-\*\d\.\)\s]+", "", s)
        s = s.replace("最终提示词：", "").replace("提示词：", "").strip()
        s = re.sub(r"\s+", " ", s).strip()
        s = s.strip().strip("\"'`")
        if s and s[-1] not in "。！？":
            s += "。"

        zh_count = len(re.findall(r"[\u4e00-\u9fff]", s))
        en_count = len(re.findall(r"[A-Za-z]", s))
        if zh_count < 20 or (en_count > zh_count * 1.2):
            return ""
        return s

    def extract_text(data: Dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for part in content:
                if isinstance(part, dict):
                    txt = part.get("text")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
                elif isinstance(part, str) and part.strip():
                    parts.append(part.strip())
            return "\n".join(parts).strip()
        return ""

    errors: List[str] = []
    for api_url in api_candidates:
        for model in model_candidates:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 280,
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:8090",
                "X-Title": "pdd-image-workbench",
                "X-OpenRouter-Title": "pdd-image-workbench",
            }
            try:
                r = requests.post(api_url, headers=headers, json=payload, timeout=45)
                if r.status_code == 429:
                    time.sleep(1.2)
                    r = requests.post(api_url, headers=headers, json=payload, timeout=45)
                if r.status_code >= 400:
                    body = r.text[:260].replace("\n", " ")
                    errors.append(f"{r.status_code} @{api_url} model={model} body={body}")
                    continue
                data = r.json()
                text = clean_prompt_text(extract_text(data))
                if not text:
                    errors.append(f"empty_response @{api_url} model={model}")
                    continue
                return text, model
            except Exception as e:
                errors.append(f"exception @{api_url} model={model} err={e}")
                continue

    joined = " | ".join(errors[:4])
    if "429" in joined:
        raise ValueError("OpenRouter免费模型当前限流(429)，请1-3分钟后重试。")
    if "empty_response" in joined:
        raise ValueError("OpenRouter本次未返回可用文本，请重试。")
    raise ValueError("OpenRouter调用失败，请稍后重试。")


def is_prompt_complete(prompt: str, resolution: str) -> bool:
    p = (prompt or "").strip()
    if len(p) < 55:
        return False
    if resolution not in p:
        return False
    required_any = [("构图", "留白"), ("背景", "主体"), ("高清", "细节")]
    for a, b in required_any:
        if a not in p and b not in p:
            return False
    return True
