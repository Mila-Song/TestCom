from __future__ import annotations

import base64
import io
import os
import random
import time
from typing import Any, Dict, List, Tuple
from urllib.parse import quote, urlparse

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _post_json_with_rate_retry(
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: int = 120,
    max_retries: int = 3,
) -> requests.Response:
    """
    Retry on provider rate-limit (429) with exponential backoff.
    """
    last_resp: requests.Response | None = None
    for i in range(max_retries + 1):
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        last_resp = resp
        if resp.status_code != 429:
            return resp
        # 1.2s, 2.4s, 4.8s ...
        time.sleep(1.2 * (2 ** i))
    return last_resp  # type: ignore[return-value]


def analyze_reference_style(ref_img: Image.Image) -> str:
    arr = np.asarray(ref_img.convert("RGB"), dtype=np.float32)
    mean = arr.mean(axis=(0, 1))
    brightness = float(mean.mean())
    saturation = float(np.std(arr, axis=2).mean())
    contrast = float(arr.std())
    tone = "明亮通透" if brightness > 160 else "低照度氛围"
    color = "高饱和" if saturation > 38 else "低饱和"
    ct = "高对比" if contrast > 62 else "柔和对比"
    rgb = ",".join(str(int(x)) for x in mean)
    return f"参考图风格：{tone}，{color}，{ct}，主色RGB约({rgb})，构图风格请贴近参考图。"


def build_focus_constraints(width: int, height: int) -> str:
    if width > height:
        copy_area = "右侧留白约20%"
    elif height > width:
        copy_area = "顶部留白约20%"
    else:
        copy_area = "上方或右侧留白约20%"
    return (
        f"单一产品主体，近景特写，产品在画面中占比约65%-80%，"
        f"严禁多主体或杂乱道具，背景简洁弱化，{copy_area}用于文案，"
        "边缘清晰，主体高对比，视觉焦点必须锁定产品本体。"
    )


def build_mock_ai_image(prompt: str, size: Tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, "#f2f5f8")
    draw = ImageDraw.Draw(img)
    for y in range(h):
        r = int(30 + (190 - 30) * (y / max(1, h - 1)))
        g = int(42 + (214 - 42) * (y / max(1, h - 1)))
        b = int(68 + (235 - 68) * (y / max(1, h - 1)))
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    margin = int(min(w, h) * 0.08)
    draw.rounded_rectangle([margin, margin, w - margin, h - margin], radius=24, fill=(248, 249, 251))
    draw.rounded_rectangle([w * 0.18, h * 0.4, w * 0.6, h * 0.8], radius=20, fill=(51, 108, 222))
    draw.ellipse([w * 0.66, h * 0.2, w * 0.88, h * 0.42], fill=(255, 187, 95))
    # Default bitmap font cannot render arbitrary CJK reliably; keep mock text ASCII-safe.
    safe_prompt = (prompt or "").encode("ascii", "ignore").decode("ascii").strip()[:80]
    if not safe_prompt:
        safe_prompt = "prompt preview"
    draw.text((margin + 20, margin + 16), "AI Image", fill=(19, 32, 48), font=ImageFont.load_default())
    draw.text((margin + 20, margin + 42), safe_prompt, fill=(59, 74, 92), font=ImageFont.load_default())
    return img


def generate_image_via_pollinations(prompt: str, w: int, h: int, seed: int | None = None) -> Image.Image:
    safe_prompt = quote(prompt)
    seed_val = seed if seed is not None else random.randint(1, 2_147_483_000)
    url = f"https://image.pollinations.ai/prompt/{safe_prompt}?width={w}&height={h}&nologo=true&seed={seed_val}"
    r = requests.get(url, timeout=40)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def generate_image_via_qwen(prompt: str, w: int, h: int) -> Image.Image:
    api_key = os.getenv("QWEN_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少 QWEN_API_KEY")
    api_url = os.getenv("QWEN_IMAGE_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations").strip()
    model = os.getenv("QWEN_IMAGE_MODEL", "qwen-image-2.0-2026-03-03").strip()
    size_compat = f"{w}x{h}"
    size_native = f"{w}*{h}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def parse_and_fetch(obj: Dict[str, Any]) -> Image.Image | None:
        data = obj.get("data") or []
        if isinstance(data, list) and data:
            first = data[0] or {}
            url = first.get("url")
            b64 = first.get("b64_json")
            if isinstance(url, str) and url:
                ir = requests.get(url, timeout=60)
                ir.raise_for_status()
                return Image.open(io.BytesIO(ir.content)).convert("RGB")
            if isinstance(b64, str) and b64:
                raw = base64.b64decode(b64)
                return Image.open(io.BytesIO(raw)).convert("RGB")

        output = obj.get("output") or {}
        results = output.get("results") or []
        if isinstance(results, list) and results:
            url = (results[0] or {}).get("url")
            if isinstance(url, str) and url:
                ir = requests.get(url, timeout=60)
                ir.raise_for_status()
                return Image.open(io.BytesIO(ir.content)).convert("RGB")
        return None

    def extract_task_id(obj: Dict[str, Any]) -> str:
        output = obj.get("output") or {}
        for key in ("task_id", "taskId", "id"):
            v = output.get(key) or obj.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    def task_status(obj: Dict[str, Any]) -> str:
        output = obj.get("output") or {}
        for key in ("task_status", "taskStatus", "status"):
            v = output.get(key) or obj.get(key)
            if isinstance(v, str):
                return v.upper()
        return ""

    errors: List[str] = []

    def is_native_text2img_url(url: str) -> bool:
        return "/api/v1/services/aigc/text2image/image-synthesis" in url

    host = (urlparse(api_url).hostname or "").lower()
    if "dashscope-intl.aliyuncs.com" in host:
        native_url = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    else:
        native_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"

    first_payload = (
        {"model": model, "input": {"prompt": prompt}, "parameters": {"size": size_native}}
        if is_native_text2img_url(api_url)
        else {"model": model, "prompt": prompt, "size": size_compat}
    )
    second_payload = {"model": model, "input": {"prompt": prompt}, "parameters": {"size": size_native}}
    try_urls: List[Tuple[str, Dict[str, Any]]] = [(api_url, first_payload)]
    if native_url != api_url:
        try_urls.append((native_url, second_payload))

    for url, payload in try_urls:
        try:
            r = _post_json_with_rate_retry(url, headers, payload, timeout=90, max_retries=2)
            if r.status_code >= 400:
                if r.status_code == 403 and "synchronous calls" in r.text.lower():
                    async_headers = dict(headers)
                    async_headers["X-DashScope-Async"] = "enable"
                    ar = _post_json_with_rate_retry(url, async_headers, payload, timeout=90, max_retries=2)
                    if ar.status_code >= 400:
                        errors.append(f"{ar.status_code}@{url}(async):{ar.text[:180]}")
                        continue
                    aobj = ar.json()
                    tid = extract_task_id(aobj)
                    if not tid:
                        errors.append(f"no_task_id@{url}(async):{str(aobj)[:180]}")
                        continue
                    base = url.split("/api/v1/services/")[0].rstrip("/")
                    task_url = f"{base}/api/v1/tasks/{tid}"
                    done_obj: Dict[str, Any] | None = None
                    terminal_state_reached = False
                    for _ in range(40):
                        tr = requests.get(task_url, headers=headers, timeout=60)
                        if tr.status_code >= 400:
                            errors.append(f"{tr.status_code}@{task_url}:{tr.text[:160]}")
                            done_obj = None
                            terminal_state_reached = True
                            break
                        tobj = tr.json()
                        st = task_status(tobj)
                        if st in ("SUCCEEDED", "SUCCESS", "DONE"):
                            done_obj = tobj
                            terminal_state_reached = True
                            break
                        if st in ("FAILED", "CANCELED", "CANCELLED"):
                            output = tobj.get("output") or {}
                            err_code = output.get("code") or tobj.get("code") or "TASK_FAILED"
                            err_msg = output.get("message") or tobj.get("message") or str(tobj)[:240]
                            errors.append(f"task_failed@{task_url}:{err_code}:{str(err_msg)[:220]}")
                            done_obj = None
                            terminal_state_reached = True
                            break
                        time.sleep(2.0)
                    if done_obj is not None:
                        out = parse_and_fetch(done_obj)
                        if out is not None:
                            return out
                        errors.append(f"no_image_data@{task_url}:{str(done_obj)[:180]}")
                    else:
                        if not terminal_state_reached:
                            errors.append(f"task_timeout@{task_url}")
                    continue

                errors.append(f"{r.status_code}@{url}:{r.text[:180]}")
                continue
            obj = r.json()
            out = parse_and_fetch(obj)
            if out is not None:
                return out
            errors.append(f"no_image_data@{url}:{str(obj)[:180]}")
        except Exception as e:
            errors.append(f"exception@{url}:{str(e)[:180]}")

    raise ValueError("Qwen API error: " + " | ".join(errors[:3]))


def _qwen_base_api_url() -> str:
    base_from_compat = os.getenv("QWEN_IMAGE_API_URL", "").strip()
    host = (urlparse(base_from_compat).hostname or "").lower()
    if "dashscope-intl.aliyuncs.com" in host:
        return "https://dashscope-intl.aliyuncs.com/api/v1"
    return "https://dashscope.aliyuncs.com/api/v1"


def _image_to_data_uri(img: Image.Image, max_side: int = 1536) -> str:
    rgb = img.convert("RGB")
    w, h = rgb.size
    scale = min(1.0, float(max_side) / float(max(1, max(w, h))))
    if scale < 1.0:
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        rgb = rgb.resize((nw, nh), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _qwen_extract_image_from_multimodal_response(obj: Dict[str, Any]) -> Image.Image | None:
    output = obj.get("output") or {}
    choices = output.get("choices") or []
    if not isinstance(choices, list) or not choices:
        return None
    msg = (choices[0] or {}).get("message") or {}
    content = msg.get("content") or []
    if not isinstance(content, list):
        return None
    for part in content:
        if not isinstance(part, dict):
            continue
        img_url = part.get("image")
        if isinstance(img_url, str) and img_url:
            ir = requests.get(img_url, timeout=90)
            ir.raise_for_status()
            return Image.open(io.BytesIO(ir.content)).convert("RGB")
    return None


def _qwen_edit_instruction_for_product(strength: float, size: str) -> str:
    s = float(np.clip(strength, 0.0, 1.0))
    style_weight = int(35 + s * 55)
    preserve_weight = int(90 - s * 40)
    return (
        "请基于图1(商品图)进行电商主图优化，并参考图2(风格参考图)的光线、色调和背景氛围。"
        "必须保持图1中的商品主体形状、材质和品牌识别特征，不改变产品类别与核心外观。"
        f"风格迁移强度约{style_weight}%，商品保真度约{preserve_weight}%。"
        "输出单主体构图，主体清晰突出，背景简洁，画面干净高级，适合电商首图。"
        "禁止新增水印、logo、无关文字、英文字符和复杂装饰元素。"
        f"最终输出分辨率为{size}。"
    )


def estimate_fg_mask(img: Image.Image) -> np.ndarray:
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    border = np.concatenate([rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]], axis=0)
    bg = border.mean(axis=0)
    dist = np.linalg.norm(rgb - bg, axis=2)
    thresh = max(18.0, float(np.percentile(dist, 58)))
    mask = dist > thresh
    m = Image.fromarray((mask.astype(np.uint8) * 255)).filter(ImageFilter.GaussianBlur(radius=1.2))
    return (np.asarray(m, dtype=np.uint8) > 90)


def _style_summary_from_ref_image(ref_img: Image.Image) -> str:
    arr = np.asarray(ref_img.convert("RGB"), dtype=np.float32)
    mean = arr.mean(axis=(0, 1))
    brightness = float(mean.mean())
    saturation = float(np.std(arr, axis=2).mean())
    contrast = float(arr.std())
    tone = "明亮通透" if brightness > 160 else "偏低照度氛围"
    color = "高饱和" if saturation > 38 else "低饱和"
    ct = "高对比" if contrast > 62 else "柔和对比"
    return f"{tone}、{color}、{ct}"


def build_style_background_prompt(reference_img: Image.Image, width: int, height: int, strength: float) -> str:
    style = _style_summary_from_ref_image(reference_img)
    s = float(np.clip(strength, 0.0, 1.0))
    style_weight = int(45 + s * 45)
    size = f"{width}x{height}"
    return (
        f"电商商品背景图，仅背景无主体，无文字无logo无水印，分辨率{size}。"
        f"整体风格参考样图，风格贴合强度约{style_weight}%，气质为{style}。"
        "背景简洁高级，光线自然，层次干净，保留中心主体摆放区域和轻微地面透视。"
        "不得出现任何产品、人物、品牌元素、字符。"
    )


def extract_product_cutout(product_img: Image.Image) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    rgb = product_img.convert("RGB")
    mask_bool = estimate_fg_mask(rgb)
    m = Image.fromarray((mask_bool.astype(np.uint8) * 255), mode="L").filter(ImageFilter.GaussianBlur(radius=1.5))
    m_arr = np.asarray(m, dtype=np.uint8)
    ys, xs = np.where(m_arr > 20)
    if len(xs) < 20 or len(ys) < 20:
        rgba = rgb.convert("RGBA")
        return rgba, (0, 0, rgb.width, rgb.height)
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    rgba = rgb.convert("RGBA")
    rgba.putalpha(m)
    crop = rgba.crop((x1, y1, x2, y2))
    return crop, (x1, y1, x2, y2)


def compose_cutout_on_background(bg_img: Image.Image, cutout_rgba: Image.Image, strength: float = 0.75) -> Image.Image:
    bg = bg_img.convert("RGB")
    w, h = bg.size
    s = float(np.clip(strength, 0.0, 1.0))
    target_ratio = 0.58 + (1.0 - s) * 0.12
    max_w = int(w * target_ratio)
    max_h = int(h * target_ratio)

    cw, ch = cutout_rgba.size
    scale = min(max_w / max(1, cw), max_h / max(1, ch))
    nw = max(1, int(cw * scale))
    nh = max(1, int(ch * scale))
    prod = cutout_rgba.resize((nw, nh), Image.Resampling.LANCZOS)

    out = bg.convert("RGBA")
    px = (w - nw) // 2
    py = int(h * 0.56 - nh / 2)
    py = max(8, min(py, h - nh - 8))

    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sx1 = px + int(nw * 0.1)
    sx2 = px + int(nw * 0.9)
    sy1 = py + int(nh * 0.84)
    sy2 = py + int(nh * 1.02)
    sd.ellipse((sx1, sy1, sx2, sy2), fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=10))
    out.alpha_composite(shadow)
    out.alpha_composite(prod, dest=(px, py))
    return out.convert("RGB")


def generate_stylized_product_composition_via_qwen(product_img: Image.Image, reference_img: Image.Image, strength: float = 0.75) -> Image.Image:
    w, h = product_img.size
    cutout, _ = extract_product_cutout(product_img)
    bg_prompt = build_style_background_prompt(reference_img, w, h, strength=strength)
    style_bg = generate_image_via_qwen(bg_prompt, w, h)
    return compose_cutout_on_background(style_bg, cutout, strength=strength)


def generate_image_edit_via_qwen(product_img: Image.Image, reference_img: Image.Image, strength: float = 0.75) -> Image.Image:
    api_key = os.getenv("QWEN_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少 QWEN_API_KEY")

    model = os.getenv("QWEN_IMAGE_EDIT_MODEL", "qwen-image-2.0-pro").strip()
    base_api = os.getenv("QWEN_MM_BASE_API_URL", "").strip() or _qwen_base_api_url()
    call_url = f"{base_api.rstrip('/')}/services/aigc/multimodal-generation/generation"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    w, h = product_img.size
    size = f"{w}*{h}"
    payload = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": _image_to_data_uri(product_img)},
                        {"image": _image_to_data_uri(reference_img)},
                        {"text": _qwen_edit_instruction_for_product(strength, size)},
                    ],
                }
            ]
        },
        "parameters": {
            "n": 1,
            "size": size,
            "watermark": False,
            "prompt_extend": True,
            "negative_prompt": "文字水印, logo, 英文字符, 多主体, 杂乱背景",
        },
    }

    errors: List[str] = []
    try:
        r = _post_json_with_rate_retry(call_url, headers, payload, timeout=120, max_retries=3)
        if r.status_code >= 400 and r.status_code == 403 and "synchronous calls" in r.text.lower():
            async_headers = dict(headers)
            async_headers["X-DashScope-Async"] = "enable"
            ar = _post_json_with_rate_retry(call_url, async_headers, payload, timeout=120, max_retries=3)
            if ar.status_code >= 400:
                errors.append(f"{ar.status_code}@{call_url}(async):{ar.text[:220]}")
            else:
                aobj = ar.json()
                task_id = ""
                output = aobj.get("output") or {}
                for k in ("task_id", "taskId", "id"):
                    v = output.get(k) or aobj.get(k)
                    if isinstance(v, str) and v.strip():
                        task_id = v.strip()
                        break
                if not task_id:
                    errors.append(f"no_task_id@{call_url}:{str(aobj)[:220]}")
                else:
                    task_url = f"{base_api.rstrip('/')}/tasks/{task_id}"
                    done_obj: Dict[str, Any] | None = None
                    for _ in range(40):
                        tr = requests.get(task_url, headers=headers, timeout=90)
                        if tr.status_code >= 400:
                            errors.append(f"{tr.status_code}@{task_url}:{tr.text[:180]}")
                            break
                        tobj = tr.json()
                        out = tobj.get("output") or {}
                        st = str(out.get("task_status") or out.get("status") or tobj.get("status") or "").upper()
                        if st in ("SUCCEEDED", "SUCCESS", "DONE"):
                            done_obj = tobj
                            break
                        if st in ("FAILED", "CANCELED", "CANCELLED"):
                            code = out.get("code") or tobj.get("code") or "TASK_FAILED"
                            msg = out.get("message") or tobj.get("message") or str(tobj)[:220]
                            errors.append(f"task_failed@{task_url}:{code}:{msg}")
                            break
                        time.sleep(2.0)
                    if done_obj:
                        out_img = _qwen_extract_image_from_multimodal_response(done_obj)
                        if out_img is not None:
                            return out_img
                        errors.append(f"no_image_data@{task_url}:{str(done_obj)[:180]}")
                    elif not errors:
                        errors.append(f"task_timeout@{task_url}")
        elif r.status_code >= 400:
            errors.append(f"{r.status_code}@{call_url}:{r.text[:220]}")
        else:
            obj = r.json()
            out_img = _qwen_extract_image_from_multimodal_response(obj)
            if out_img is not None:
                return out_img
            errors.append(f"no_image_data@{call_url}:{str(obj)[:220]}")
    except Exception as e:
        errors.append(f"exception@{call_url}:{str(e)[:200]}")
    raise ValueError("Qwen 图片编辑失败: " + " | ".join(errors[:2]))


def _qwen_prompt_opt_instruction(user_prompt: str, size: str, strength: float, variation_directive: str = "") -> str:
    s = float(np.clip(strength, 0.0, 1.0))
    change_weight = int(30 + s * 60)
    keep_weight = int(90 - s * 40)
    variation_text = f"变体要求：{variation_directive}。" if variation_directive.strip() else ""
    return (
        "请基于输入图片进行电商图优化。"
        "必须保持原图商品主体类别与核心外观，不得替换成其他商品。"
        f"优化改动强度约{change_weight}%，主体保真度约{keep_weight}%。"
        f"按以下要求优化：{user_prompt}。"
        f"{variation_text}"
        f"输出分辨率{size}。"
        "结果需主体清晰突出、构图干净、适合电商首图。"
        "禁止出现无关文字、logo、水印、英文乱码。"
    )


def generate_image_optimize_by_prompt_via_qwen(
    source_img: Image.Image,
    user_prompt: str,
    out_w: int,
    out_h: int,
    strength: float = 0.65,
    variation_directive: str = "",
) -> Image.Image:
    api_key = os.getenv("QWEN_API_KEY", "").strip()
    if not api_key:
        raise ValueError("缺少 QWEN_API_KEY")
    if not user_prompt.strip():
        raise ValueError("prompt 不能为空")

    model = os.getenv("QWEN_IMAGE_EDIT_MODEL", "qwen-image-2.0-pro").strip()
    base_api = os.getenv("QWEN_MM_BASE_API_URL", "").strip() or _qwen_base_api_url()
    call_url = f"{base_api.rstrip('/')}/services/aigc/multimodal-generation/generation"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    size = f"{out_w}*{out_h}"
    instruction = _qwen_prompt_opt_instruction(
        user_prompt,
        size=size,
        strength=strength,
        variation_directive=variation_directive,
    )

    payload = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": _image_to_data_uri(source_img)},
                        {"text": instruction},
                    ],
                }
            ]
        },
        "parameters": {
            "n": 1,
            "size": size,
            "watermark": False,
            "prompt_extend": True,
            "negative_prompt": "文字水印, logo, 英文字符, 多主体, 杂乱背景, 低清晰度",
        },
    }

    errors: List[str] = []
    try:
        r = _post_json_with_rate_retry(call_url, headers, payload, timeout=120, max_retries=3)
        if r.status_code >= 400 and r.status_code == 403 and "synchronous calls" in r.text.lower():
            async_headers = dict(headers)
            async_headers["X-DashScope-Async"] = "enable"
            ar = _post_json_with_rate_retry(call_url, async_headers, payload, timeout=120, max_retries=3)
            if ar.status_code >= 400:
                errors.append(f"{ar.status_code}@{call_url}(async):{ar.text[:220]}")
            else:
                aobj = ar.json()
                task_id = ""
                output = aobj.get("output") or {}
                for k in ("task_id", "taskId", "id"):
                    v = output.get(k) or aobj.get(k)
                    if isinstance(v, str) and v.strip():
                        task_id = v.strip()
                        break
                if not task_id:
                    errors.append(f"no_task_id@{call_url}:{str(aobj)[:220]}")
                else:
                    task_url = f"{base_api.rstrip('/')}/tasks/{task_id}"
                    done_obj: Dict[str, Any] | None = None
                    for _ in range(40):
                        tr = requests.get(task_url, headers=headers, timeout=90)
                        if tr.status_code >= 400:
                            errors.append(f"{tr.status_code}@{task_url}:{tr.text[:180]}")
                            break
                        tobj = tr.json()
                        out = tobj.get("output") or {}
                        st = str(out.get("task_status") or out.get("status") or tobj.get("status") or "").upper()
                        if st in ("SUCCEEDED", "SUCCESS", "DONE"):
                            done_obj = tobj
                            break
                        if st in ("FAILED", "CANCELED", "CANCELLED"):
                            code = out.get("code") or tobj.get("code") or "TASK_FAILED"
                            msg = out.get("message") or tobj.get("message") or str(tobj)[:220]
                            errors.append(f"task_failed@{task_url}:{code}:{msg}")
                            break
                        time.sleep(2.0)
                    if done_obj:
                        out_img = _qwen_extract_image_from_multimodal_response(done_obj)
                        if out_img is not None:
                            return out_img
                        errors.append(f"no_image_data@{task_url}:{str(done_obj)[:180]}")
                    elif not errors:
                        errors.append(f"task_timeout@{task_url}")
        elif r.status_code >= 400:
            errors.append(f"{r.status_code}@{call_url}:{r.text[:220]}")
        else:
            obj = r.json()
            out_img = _qwen_extract_image_from_multimodal_response(obj)
            if out_img is not None:
                return out_img
            errors.append(f"no_image_data@{call_url}:{str(obj)[:220]}")
    except Exception as e:
        errors.append(f"exception@{call_url}:{str(e)[:200]}")
    raise ValueError("Qwen 按提示词优化失败: " + " | ".join(errors[:2]))


def remove_watermark_via_qwen(source_img: Image.Image, strength: float = 0.35) -> Image.Image:
    """
    Qwen-based watermark removal via image edit.
    Keep product subject unchanged while removing watermark/logo/text artifacts.
    """
    w, h = source_img.size
    prompt = (
        "去除图片中的所有水印、logo、角标、无关文字与印章痕迹。"
        "保持商品主体类别、形状、颜色、材质与构图位置不变。"
        "修复被去除区域的背景与纹理，边缘自然，无涂抹感。"
        "输出干净电商图，不新增任何文字、logo、水印或多余元素。"
    )
    return generate_image_optimize_by_prompt_via_qwen(
        source_img=source_img,
        user_prompt=prompt,
        out_w=w,
        out_h=h,
        strength=float(np.clip(strength, 0.0, 1.0)),
        variation_directive="",
    )
