#!/usr/bin/env python3
"""
使用 Google Cloud Vision API 和 Gemini 3 Pro 生成 fabric.js 图层
流程:
1. 使用 Cloud Vision API 分析图片获取文字坐标
2. 使用 Gemini 3 Pro Preview 分析图片生成初步的 fabric.js JSON
3. 将 Vision API 结果 + 初步 fabric.js + 原图送给 Gemini 做整合修正
4. 使用 Gemini 3 Pro Image Preview 移除原图中的文字，生成 base image
5. 在 base image 上渲染新文字
"""

import os
import io
import json
import base64
import argparse
from pathlib import Path
from google.cloud import vision
from google import genai
from google.genai import types
from PIL import Image as PILImage, ImageDraw, ImageFont

# 配置 - 使用 API Key
GOOGLE_CLOUD_API_KEY = "AQ.Ab8RN6JO7GKV2YNMXiDE_QrsJEyljMRcgHoOQypxLe1I9s1AWw"

# 默认图片目录
IMAGE_DIR = "image"


def analyze_image_with_vision_api(image_path: str) -> dict:
    """
    Step 1: 使用 Google Cloud Vision API 分析图片，获取所有文字及其坐标信息
    """
    print(f"[Step 1] 使用 Vision API 分析图片: {image_path}")
    
    vision_client = vision.ImageAnnotatorClient()
    
    with open(image_path, "rb") as image_file:
        content = image_file.read()
    
    image = vision.Image(content=content)
    response = vision_client.text_detection(image=image)
    
    if response.error.message:
        raise Exception(f"Vision API 错误: {response.error.message}")
    
    texts = response.text_annotations
    
    result = {
        "full_text": "",
        "text_blocks": [],
        "text_lines": [],
        "image_path": image_path
    }
    
    if texts:
        result["full_text"] = texts[0].description
        
        for text in texts[1:]:
            vertices = text.bounding_poly.vertices
            min_x = min(v.x for v in vertices)
            max_x = max(v.x for v in vertices)
            min_y = min(v.y for v in vertices)
            max_y = max(v.y for v in vertices)
            
            block = {
                "text": text.description,
                "left": min_x,
                "top": min_y,
                "right": max_x,
                "bottom": max_y,
                "width": max_x - min_x,
                "height": max_y - min_y,
                "center_x": (min_x + max_x) / 2,
                "center_y": (min_y + max_y) / 2,
            }
            result["text_blocks"].append(block)
        
        # 按行分组
        result["text_lines"] = group_text_by_lines(result["text_blocks"])
    
    print(f"   找到 {len(result['text_blocks'])} 个文字块，{len(result.get('text_lines', []))} 行")
    return result


def group_text_by_lines(text_blocks: list, threshold: float = 10) -> list:
    """将文字块按行分组"""
    if not text_blocks:
        return []
    
    sorted_blocks = sorted(text_blocks, key=lambda b: b["center_y"])
    lines = []
    current_line = [sorted_blocks[0]]
    current_center_y = sorted_blocks[0]["center_y"]
    
    for block in sorted_blocks[1:]:
        if abs(block["center_y"] - current_center_y) <= threshold:
            current_line.append(block)
        else:
            lines.append(finalize_line(current_line))
            current_line = [block]
            current_center_y = block["center_y"]
    
    if current_line:
        lines.append(finalize_line(current_line))
    
    return lines


def finalize_line(line_blocks: list) -> dict:
    """完成一行的处理"""
    sorted_blocks = sorted(line_blocks, key=lambda b: b["left"])
    unified_top = min(b["top"] for b in sorted_blocks)
    unified_bottom = max(b["bottom"] for b in sorted_blocks)
    
    return {
        "texts": [b["text"] for b in sorted_blocks],
        "full_text": " ".join(b["text"] for b in sorted_blocks),
        "unified_top": unified_top,
        "unified_bottom": unified_bottom,
        "unified_height": unified_bottom - unified_top,
    }


def get_image_dimensions(image_path: str) -> tuple:
    """获取图片尺寸"""
    with PILImage.open(image_path) as img:
        return img.size


def get_gemini_client():
    """获取 Gemini Client"""
    return genai.Client(
        vertexai=True,
        api_key=GOOGLE_CLOUD_API_KEY,
    )


def get_file_part(file_path: str) -> types.Part:
    """获取文件 Part (支持图片和 PDF)"""
    with open(file_path, "rb") as f:
        file_data = f.read()
    
    file_lower = file_path.lower()
    if file_lower.endswith('.png'):
        mime_type = "image/png"
    elif file_lower.endswith('.gif'):
        mime_type = "image/gif"
    elif file_lower.endswith('.webp'):
        mime_type = "image/webp"
    elif file_lower.endswith('.pdf'):
        mime_type = "application/pdf"
    else:
        mime_type = "image/jpeg"
    
    return types.Part.from_bytes(data=file_data, mime_type=mime_type)


def get_image_part(image_path: str) -> types.Part:
    """获取图片 Part (兼容旧接口)"""
    return get_file_part(image_path)


# 字体参考文件路径 (支持图片或 PDF)
FONT_STYLE_REFERENCE = "textstyle.pdf"

def generate_initial_fabric_json(image_path: str, image_dir: Path = None) -> dict:
    """
    Step 2: 使用 Gemini 分析图片生成初步的 fabric.js JSON（不考虑 Vision API 结果）
    
    Args:
        image_path: 输入图片路径
        image_dir: 图片目录，用于查找字体参考图片
    """
    print(f"\n[Step 2] 使用 Gemini 分析图片生成初步 fabric.js JSON")
    
    client = get_gemini_client()
    width, height = get_image_dimensions(image_path)
    image_part = get_image_part(image_path)
    
    # 检查是否有字体参考图片
    font_reference_part = None
    
    if image_dir:
        font_ref_path = image_dir / FONT_STYLE_REFERENCE
        if font_ref_path.exists():
            print(f"   使用字体参考文件: {font_ref_path}")
            font_reference_part = get_file_part(str(font_ref_path))
        else:
            print(f"   字体参考文件不存在，使用文字 prompt 识别字体")
    else:
        # 尝试在脚本目录和 image 目录查找
        script_dir = Path(__file__).parent
        for search_dir in [script_dir / IMAGE_DIR, script_dir]:
            font_ref_path = search_dir / FONT_STYLE_REFERENCE
            if font_ref_path.exists():
                print(f"   使用字体参考文件: {font_ref_path}")
                font_reference_part = get_file_part(str(font_ref_path))
                break
        if font_reference_part is None:
            print(f"   字体参考文件不存在，使用文字 prompt 识别字体")
    
    # 根据是否有参考图片调整 prompt
    if font_reference_part:
        prompt_text = f"""请分析第一张图片，生成 fabric.js JSON 结构来重建图片中的所有图层。
第二张图片是字体样式参考指南，请参考它来识别第一张图片中的字体类型。

## 图片尺寸（第一张图片）
- 宽度: {width}px
- 高度: {height}px

## 任务
1. 识别背景颜色
2. 识别所有形状（矩形、圆形等）
3. 识别所有文字，包括：
   - 文字内容
   - 位置坐标 (left, top)
   - 字体样式 (fontWeight: bold/normal, fontStyle: italic/normal)
   - 颜色
   - 字体大小
   - **字体类型 (fontFamily)** - 请参考第二张图片的字体样式指南来识别！

**重要**: 请参考第二张字体样式参考图来判断每个文字的字体类型！

## 输出格式（只输出 JSON）
```json
{{
  "version": "5.3.0",
  "objects": [
    {{"type": "rect", "left": 0, "top": 0, "width": {width}, "height": {height}, "fill": "#FFFFFF"}},
    {{"type": "textbox", "text": "文字", "left": 100, "top": 50, "fontSize": 24, "fontWeight": "bold", "fontStyle": "normal", "fontFamily": "Serif", "fill": "#000000"}}
  ],
  "background": "#FFFFFF"
}}
```

请只输出 JSON，不要有其他说明。"""
    else:
        prompt_text = f"""请分析这张图片，生成 fabric.js JSON 结构来重建图片中的所有图层。

## 图片尺寸
- 宽度: {width}px
- 高度: {height}px

## 任务
1. 识别背景颜色
2. 识别所有形状（矩形、圆形等）
3. 识别所有文字，包括：
   - 文字内容
   - 位置坐标 (left, top)
   - 字体样式 (fontWeight: bold/normal, fontStyle: italic/normal)
   - 颜色
   - 字体大小
   - **字体类型 (fontFamily)** - 请仔细识别！

## 字体识别指南（非常重要！请仔细分析每个文字）

请放大并仔细观察每个文字的字母特征，特别是:
- 字母 "l", "i", "t" 的末端是否有装饰线
- 字母 "O", "C", "S" 的笔画粗细是否均匀
- 字母 "e", "a" 的形状特征

### Serif 衬线字体（有装饰性笔画末端）- 使用 "Serif"
### Sans-Serif 无衬线字体（干净简洁）- 使用 "Sans-serif"

**重要**: 请为每个文字单独判断字体类型！

## 输出格式（只输出 JSON）
```json
{{
  "version": "5.3.0",
  "objects": [
    {{"type": "rect", "left": 0, "top": 0, "width": {width}, "height": {height}, "fill": "#FFFFFF"}},
    {{"type": "textbox", "text": "文字", "left": 100, "top": 50, "fontSize": 24, "fontWeight": "bold", "fontStyle": "normal", "fontFamily": "Serif", "fill": "#000000"}}
  ],
  "background": "#FFFFFF"
}}
```

请只输出 JSON，不要有其他说明。"""

    # 构建 parts 列表
    parts = [image_part]
    if font_reference_part:
        parts.append(font_reference_part)
    parts.append(types.Part.from_text(text=prompt_text))
    
    contents = [types.Content(role="user", parts=parts)]
    
    config = types.GenerateContentConfig(
        temperature=0.5,
        max_output_tokens=65535,
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
    )
    
    response_text = ""
    for chunk in client.models.generate_content_stream(
        model="gemini-3-pro-preview",
        contents=contents,
        config=config,
    ):
        if chunk.text:
            response_text += chunk.text
            print(".", end="", flush=True)
    
    print()
    
    # 提取 JSON
    response_text = response_text.strip()
    if "```json" in response_text:
        start = response_text.find("```json") + 7
        end = response_text.find("```", start)
        response_text = response_text[start:end].strip()
    elif "```" in response_text:
        start = response_text.find("```") + 3
        end = response_text.find("```", start)
        response_text = response_text[start:end].strip()
    
    try:
        fabric_json = json.loads(response_text)
        print(f"   生成初步 fabric.js JSON，包含 {len(fabric_json.get('objects', []))} 个对象")
        return fabric_json
    except json.JSONDecodeError as e:
        print(f"   警告: JSON 解析失败: {e}")
        return {"error": str(e), "raw": response_text[:500]}


def merge_and_correct_fabric_json(image_path: str, vision_result: dict, initial_fabric: dict) -> dict:
    """
    Step 3: 将 Vision API 结果、初步 fabric.js 和原图一起送给 Gemini 做整合修正
    """
    print(f"\n[Step 3] 使用 Gemini 整合 Vision API 和初步分析结果，生成最终 fabric.js")
    
    client = get_gemini_client()
    width, height = get_image_dimensions(image_path)
    image_part = get_image_part(image_path)
    
    vision_info = json.dumps(vision_result["text_blocks"], indent=2, ensure_ascii=False)
    fabric_info = json.dumps(initial_fabric, indent=2, ensure_ascii=False)
    lines_info = json.dumps(vision_result.get("text_lines", []), indent=2, ensure_ascii=False)
    
    prompt_text = f"""你是专业的图像分析专家。请整合以下两个数据源，生成最终的、精确的 fabric.js JSON。

## 图片尺寸
- 宽度: {width}px
- 高度: {height}px

## 数据源 1: Google Cloud Vision API 文字检测结果
这是精确的文字坐标，每个文字块包含 left, top, width, height 等精确坐标信息：
```json
{vision_info}
```

## 数据源 1 补充: 按行分组的文字信息
同一行的文字应该有相同的垂直基线：
```json
{lines_info}
```

## 数据源 2: Gemini 初步分析的 fabric.js
这是对图片的视觉分析，包含背景、形状、文字样式等信息：
```json
{fabric_info}
```

## 整合任务

请生成最终的 fabric.js JSON，遵循以下规则：

### 1. 文字坐标 - 必须使用 Vision API 的精确坐标
- `left` 和 `top` 必须来自 Vision API
- `width` 和 `height` 可以参考 Vision API
- 同一行的文字应该有相同的 `top` 值（使用 text_lines 中的 unified_top）

### 2. 文字样式 - 使用 Gemini 分析的样式
- `fontWeight`: 粗体文字使用 "bold"
- `fontStyle`: 斜体文字使用 "italic"（仔细观察原图中是否有倾斜的文字）
- `fill`: 文字颜色
- `fontFamily`: 字体

### 3. 背景和形状 - 使用 Gemini 分析的结果
- 背景颜色
- 矩形、按钮背景等形状

### 4. 重要规则
- 文字必须与 Vision API 检测的完全一致（不能修改文字内容）
- 形状要先于其上的文字渲染（先背景后文字）

## 输出格式
```json
{{
  "version": "5.3.0",
  "objects": [
    {{"type": "rect", "left": 0, "top": 0, "width": {width}, "height": {height}, "fill": "#FFFFFF"}},
    {{"type": "textbox", "text": "精确文字", "left": <Vision API 的 left>, "top": <Vision API 的 top>, "width": <Vision API 的 width>, "height": <Vision API 的 height>, "fontSize": 24, "fontWeight": "bold", "fontStyle": "normal", "fill": "#000000"}}
  ],
  "background": "#FFFFFF"
}}
```

请只输出最终整合后的 JSON，不要有其他说明。"""

    contents = [types.Content(role="user", parts=[image_part, types.Part.from_text(text=prompt_text)])]
    
    config = types.GenerateContentConfig(
        temperature=0.3,  # 更低的温度确保稳定输出
        max_output_tokens=65535,
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
    )
    
    response_text = ""
    for chunk in client.models.generate_content_stream(
        model="gemini-3-pro-preview",
        contents=contents,
        config=config,
    ):
        if chunk.text:
            response_text += chunk.text
            print(".", end="", flush=True)
    
    print()
    
    # 提取 JSON
    response_text = response_text.strip()
    if "```json" in response_text:
        start = response_text.find("```json") + 7
        end = response_text.find("```", start)
        response_text = response_text[start:end].strip()
    elif "```" in response_text:
        start = response_text.find("```") + 3
        end = response_text.find("```", start)
        response_text = response_text[start:end].strip()
    
    try:
        fabric_json = json.loads(response_text)
        print(f"   生成最终 fabric.js JSON，包含 {len(fabric_json.get('objects', []))} 个对象")
        return fabric_json
    except json.JSONDecodeError as e:
        print(f"   警告: JSON 解析失败: {e}")
        print(f"   使用初步结果作为备选")
        return initial_fabric


def get_font(font_family: str, font_weight: str, font_size: int) -> ImageFont.FreeTypeFont:
    """根据字体样式获取字体，支持多种字体类型"""
    
    # 判断字体类型
    font_family_lower = font_family.lower()
    is_bold = font_weight.lower() == "bold" or "bold" in font_family_lower or "black" in font_family_lower
    is_italic = "italic" in font_family_lower or "oblique" in font_family_lower
    
    # 字体路径映射（macOS）
    font_mapping = {
        # Serif 衬线字体
        "serif": {
            "bold_italic": ["/System/Library/Fonts/Supplemental/Times New Roman Bold Italic.ttf"],
            "bold": ["/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf", "/System/Library/Fonts/Times.ttc"],
            "italic": ["/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf"],
            "normal": ["/System/Library/Fonts/Supplemental/Times New Roman.ttf", "/System/Library/Fonts/Times.ttc"],
        },
        "times": {
            "bold_italic": ["/System/Library/Fonts/Supplemental/Times New Roman Bold Italic.ttf"],
            "bold": ["/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf", "/System/Library/Fonts/Times.ttc"],
            "italic": ["/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf"],
            "normal": ["/System/Library/Fonts/Supplemental/Times New Roman.ttf", "/System/Library/Fonts/Times.ttc"],
        },
        "georgia": {
            "bold_italic": ["/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf"],
            "bold": ["/System/Library/Fonts/Supplemental/Georgia Bold.ttf"],
            "italic": ["/System/Library/Fonts/Supplemental/Georgia Italic.ttf"],
            "normal": ["/System/Library/Fonts/Supplemental/Georgia.ttf"],
        },
        # Sans-serif 无衬线字体
        "sans-serif": {
            "bold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"],
            "normal": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"],
        },
        "arial": {
            "bold_italic": ["/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"],
            "bold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
            "italic": ["/System/Library/Fonts/Supplemental/Arial Italic.ttf"],
            "normal": ["/System/Library/Fonts/Supplemental/Arial.ttf"],
        },
        "helvetica": {
            "bold": ["/System/Library/Fonts/Helvetica.ttc"],
            "normal": ["/System/Library/Fonts/Helvetica.ttc"],
        },
        # Cursive 手写体
        "cursive": {
            "bold": ["/System/Library/Fonts/Supplemental/Brush Script.ttf"],
            "normal": ["/System/Library/Fonts/Supplemental/Brush Script.ttf"],
        },
        # Impact
        "impact": {
            "bold": ["/System/Library/Fonts/Supplemental/Impact.ttf"],
            "normal": ["/System/Library/Fonts/Supplemental/Impact.ttf"],
        },
    }
    
    # 确定字体类型
    font_type = None
    for key in font_mapping.keys():
        if key in font_family_lower:
            font_type = key
            break
    
    # 默认使用 sans-serif
    if font_type is None:
        font_type = "sans-serif"
    
    # 确定样式键
    if is_bold and is_italic:
        style_key = "bold_italic"
    elif is_bold:
        style_key = "bold"
    elif is_italic:
        style_key = "italic"
    else:
        style_key = "normal"
    
    # 获取字体路径列表
    font_dict = font_mapping.get(font_type, font_mapping["sans-serif"])
    paths_to_try = font_dict.get(style_key, font_dict.get("normal", []))
    
    # 尝试加载字体
    for font_path in paths_to_try:
        if os.path.exists(font_path):
            try:
                if font_path.endswith('.ttc'):
                    index = 1 if is_bold else 0
                    return ImageFont.truetype(font_path, font_size, index=index)
                else:
                    return ImageFont.truetype(font_path, font_size)
            except (OSError, IOError):
                continue
    
    # 备用：尝试通用字体
    fallback_paths = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for font_path in fallback_paths:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, font_size)
            except (OSError, IOError):
                continue
    
    return ImageFont.load_default()


def render_text_stretched(img, text: str, left: int, top: int, 
                          target_width: int, target_height: int,
                          fill: str, font_family: str, font_weight: str):
    """渲染拉伸的文字以精确匹配边界框"""
    temp_draw = ImageDraw.Draw(img)
    
    # 根据高度估算字体大小
    font_size = int(target_height * 0.95)
    font = get_font(font_family, font_weight, font_size)
    
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # 调整字体大小以匹配宽度
    if text_width > 0 and abs(text_width - target_width) > target_width * 0.1:
        ratio = target_width / text_width
        font_size = max(8, int(font_size * ratio))
        font = get_font(font_family, font_weight, font_size)
        bbox = temp_draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
    
    # 如果尺寸接近，直接绘制
    if abs(text_width - target_width) <= target_width * 0.1:
        temp_draw.text((left, top), text, fill=fill, font=font)
        return
    
    # 否则拉伸渲染
    temp_width = text_width + 10
    temp_height = text_height + 10
    temp_img = PILImage.new("RGBA", (temp_width, temp_height), (0, 0, 0, 0))
    temp_draw2 = ImageDraw.Draw(temp_img)
    temp_draw2.text((0, -bbox[1]), text, fill=fill, font=font)
    
    temp_img = temp_img.resize((target_width, target_height), PILImage.Resampling.LANCZOS)
    img.paste(temp_img, (left, top), temp_img)


def get_area_average_color(img, left: int, top: int, width: int, height: int) -> str:
    """获取区域边缘的平均颜色，用于覆盖文字（备用方案）"""
    pixels = []
    img_width, img_height = img.size
    
    for x in range(max(0, left), min(img_width, left + width)):
        if top > 0:
            pixels.append(img.getpixel((x, max(0, top - 1))))
    
    for x in range(max(0, left), min(img_width, left + width)):
        if top + height < img_height:
            pixels.append(img.getpixel((x, min(img_height - 1, top + height))))
    
    if not pixels:
        return "#C8BEB4"
    
    avg_r = sum(p[0] for p in pixels) // len(pixels)
    avg_g = sum(p[1] for p in pixels) // len(pixels)
    avg_b = sum(p[2] for p in pixels) // len(pixels)
    
    return f"#{avg_r:02x}{avg_g:02x}{avg_b:02x}"


def get_aspect_ratio_string(width: int, height: int) -> str:
    """根据宽高计算最接近的标准比例字符串"""
    ratio = width / height
    
    # 支持的比例列表
    standard_ratios = {
        "1:1": 1.0,
        "3:4": 0.75,
        "4:3": 1.333,
        "9:16": 0.5625,
        "16:9": 1.778,
    }
    
    # 找到最接近的比例
    closest_ratio = "1:1"
    min_diff = float('inf')
    for ratio_str, ratio_val in standard_ratios.items():
        diff = abs(ratio - ratio_val)
        if diff < min_diff:
            min_diff = diff
            closest_ratio = ratio_str
    
    return closest_ratio


def remove_text_with_gemini_image(image_path: str, output_path: str, original_size: tuple = None) -> bool:
    """
    Step 4: 使用 Gemini 3 Pro Image Preview 移除图片中的文字，生成干净的 base image
    
    Args:
        image_path: 原图路径
        output_path: 输出的 base image 路径
        original_size: 原图尺寸 (width, height)
        
    Returns:
        是否成功生成 base image
    """
    print(f"\n[Step 4] 使用 Gemini Image 移除图片中的文字")
    
    client = get_gemini_client()
    image_part = get_image_part(image_path)
    
    # 获取原图尺寸
    if original_size is None:
        original_size = get_image_dimensions(image_path)
    orig_width, orig_height = original_size
    
    # 计算最接近的标准比例
    aspect_ratio = get_aspect_ratio_string(orig_width, orig_height)
    print(f"   原图尺寸: {orig_width}x{orig_height}, 使用比例: {aspect_ratio}")
    
    prompt_text = """Please remove ALL text and words from this image.
Keep the product, background, all shapes, buttons, and visual elements in their EXACT positions.
Only remove the text overlays, logos with text, and any written words.
The result should be a clean image without any text, with all other elements preserved in place."""
    
    contents = [
        types.Content(
            role="user",
            parts=[image_part, types.Part.from_text(text=prompt_text)]
        )
    ]
    
    config = types.GenerateContentConfig(
        temperature=1,
        top_p=0.95,
        max_output_tokens=32768,
        response_modalities=["TEXT", "IMAGE"],
        safety_settings=[
            types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
            types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
        ],
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size="1K",
            output_mime_type="image/png",
        ),
    )
    
    try:
        print("   生成中", end="", flush=True)
        
        for chunk in client.models.generate_content_stream(
            model="gemini-3-pro-image-preview",
            contents=contents,
            config=config,
        ):
            print(".", end="", flush=True)
            
            # 检查是否有图片数据
            if hasattr(chunk, 'candidates') and chunk.candidates:
                for candidate in chunk.candidates:
                    if hasattr(candidate, 'content') and candidate.content:
                        for part in candidate.content.parts:
                            if hasattr(part, 'inline_data') and part.inline_data:
                                # 获取图片数据
                                image_data = part.inline_data.data
                                mime_type = part.inline_data.mime_type
                                
                                # 保存图片
                                img = PILImage.open(io.BytesIO(image_data))
                                img.save(output_path)
                                print(f"\n   Base image 已保存到: {output_path}")
                                return True
        
        print("\n   警告: 未能生成图片")
        return False
        
    except Exception as e:
        print(f"\n   错误: {e}")
        return False


def render_fabric_to_image(fabric_json: dict, output_path: str, original_image_path: str = None, 
                           use_original_as_background: bool = True, vision_result: dict = None):
    """
    Step 4: 使用 PIL 根据 fabric.js JSON 渲染生成新图片
    
    Args:
        fabric_json: fabric.js JSON 结构
        output_path: 输出图片路径
        original_image_path: 原图路径
        use_original_as_background: 是否使用原图作为背景（默认 True）
        vision_result: Vision API 的结果，用于定位需要覆盖的原文字区域
    """
    print(f"\n[Step 4] 根据 fabric.js JSON 生成新图片: {output_path}")
    
    if "error" in fabric_json:
        print("   错误: fabric.js JSON 无效，无法生成图片")
        return
    
    # 使用原图作为背景，或创建纯色背景
    if use_original_as_background and original_image_path and os.path.exists(original_image_path):
        print(f"   使用原图作为背景: {original_image_path}")
        img = PILImage.open(original_image_path).convert("RGB")
        width, height = img.size
        
        # 覆盖原图中的文字区域
        if vision_result and "text_blocks" in vision_result:
            print(f"   覆盖原图中 {len(vision_result['text_blocks'])} 个文字区域...")
            draw_cover = ImageDraw.Draw(img)
            for block in vision_result["text_blocks"]:
                left = int(block["left"])
                top = int(block["top"])
                right = int(block["right"])
                bottom = int(block["bottom"])
                
                # 获取文字区域周围的背景色
                cover_color = get_area_average_color(img, left, top, right - left, bottom - top)
                
                # 用背景色覆盖文字区域
                draw_cover.rectangle([left, top, right, bottom], fill=cover_color)
    else:
        # 获取画布尺寸
        if original_image_path:
            width, height = get_image_dimensions(original_image_path)
        else:
            width, height = 800, 600
        
        # 创建纯色背景
        background_color = fabric_json.get("background", "#ffffff")
        if not background_color or background_color == "transparent":
            background_color = "#ffffff"
        if background_color.startswith("rgba"):
            background_color = "#ffffff"
        
        img = PILImage.new("RGB", (width, height), background_color)
    
    draw = ImageDraw.Draw(img)
    
    # 渲染每个对象
    objects = fabric_json.get("objects", [])
    text_count = 0
    shape_count = 0
    
    for obj in objects:
        obj_type = obj.get("type", "")
        left = float(obj.get("left", 0))
        top = float(obj.get("top", 0))
        
        # 当使用原图作为背景时，跳过背景矩形（通常是第一个全尺寸矩形）
        if obj_type == "rect":
            obj_width = float(obj.get("width", 100))
            obj_height = float(obj.get("height", 100))
            fill = obj.get("fill", "#ffffff")
            
            # 判断是否是全尺寸背景矩形
            is_background_rect = (
                int(left) == 0 and int(top) == 0 and 
                abs(obj_width - width) < 10 and abs(obj_height - height) < 10
            )
            
            if use_original_as_background and is_background_rect:
                # 跳过背景矩形，使用原图
                continue
            
            # 渲染非背景的矩形（如文字背景条）
            if fill and fill != "transparent":
                draw.rectangle(
                    [int(left), int(top), int(left + obj_width), int(top + obj_height)],
                    fill=fill
                )
                shape_count += 1
        
        elif obj_type == "circle":
            radius = float(obj.get("radius", 50))
            fill = obj.get("fill", "#ffffff")
            
            if fill and fill != "transparent":
                draw.ellipse(
                    [int(left - radius), int(top - radius), int(left + radius), int(top + radius)],
                    fill=fill
                )
                shape_count += 1
        
        elif obj_type in ["text", "textbox", "i-text"]:
            text = obj.get("text", "")
            fill = obj.get("fill", "#000000")
            font_family = obj.get("fontFamily", "Arial")
            font_weight = obj.get("fontWeight", "normal")
            
            # 使用 fabric.js 中的尺寸信息
            target_width = int(obj.get("width", 100))
            target_height = int(obj.get("height", 30))
            
            render_text_stretched(
                img, text, int(left), int(top),
                target_width, target_height,
                fill, font_family, font_weight
            )
            text_count += 1
        
        elif obj_type == "line":
            x2 = float(obj.get("x2", left + 100))
            y2 = float(obj.get("y2", top))
            stroke = obj.get("stroke", "#000000")
            stroke_width = int(obj.get("strokeWidth", 1))
            draw.line([int(left), int(top), int(x2), int(y2)], fill=stroke, width=stroke_width)
            shape_count += 1
    
    print(f"   渲染完成: {text_count} 个文字, {shape_count} 个形状")
    img.save(output_path)
    print(f"   图片已保存到: {output_path}")


def render_on_base_image(fabric_json: dict, base_image_path: str, output_path: str, original_size: tuple = None):
    """
    Step 5: 在 base image 上渲染文字和形状
    
    Args:
        fabric_json: fabric.js JSON 结构
        base_image_path: base image 路径（已移除文字）
        output_path: 输出图片路径
        original_size: 原图尺寸，用于将 base image resize 到原图尺寸
    """
    print(f"\n[Step 5] 在 base image 上渲染文字")
    
    if "error" in fabric_json:
        print("   错误: fabric.js JSON 无效，无法渲染")
        return
    
    # 加载 base image
    img = PILImage.open(base_image_path).convert("RGB")
    base_width, base_height = img.size
    print(f"   Base image 原始尺寸: {base_width}x{base_height}")
    
    # 如果原图尺寸与 base image 不同，resize base image 到原图尺寸
    # 这样可以避免坐标缩放问题，使形状和文字位置更精确
    if original_size and (base_width != original_size[0] or base_height != original_size[1]):
        orig_width, orig_height = original_size
        print(f"   Resize base image 到原图尺寸: {orig_width}x{orig_height}")
        img = img.resize((orig_width, orig_height), PILImage.Resampling.LANCZOS)
        base_width, base_height = orig_width, orig_height
    
    draw = ImageDraw.Draw(img)
    
    # 渲染每个对象（不需要缩放坐标，因为 base image 已 resize 到原图尺寸）
    objects = fabric_json.get("objects", [])
    text_count = 0
    shape_count = 0
    
    for obj in objects:
        obj_type = obj.get("type", "")
        left = float(obj.get("left", 0))
        top = float(obj.get("top", 0))
        
        # 跳过背景矩形
        if obj_type == "rect":
            obj_width = float(obj.get("width", 100))
            obj_height = float(obj.get("height", 100))
            fill = obj.get("fill", "#ffffff")
            
            is_background_rect = (
                int(left) == 0 and int(top) == 0 and 
                abs(obj_width - base_width) < 10 and abs(obj_height - base_height) < 10
            )
            
            if is_background_rect:
                continue
            
            if fill and fill != "transparent":
                draw.rectangle(
                    [int(left), int(top), int(left + obj_width), int(top + obj_height)],
                    fill=fill
                )
                shape_count += 1
        
        elif obj_type in ["text", "textbox", "i-text"]:
            text = obj.get("text", "")
            fill = obj.get("fill", "#000000")
            font_family = obj.get("fontFamily", "Arial")
            font_weight = obj.get("fontWeight", "normal")
            
            target_width = int(float(obj.get("width", 100)))
            target_height = int(float(obj.get("height", 30)))
            
            render_text_stretched(
                img, text, int(left), int(top),
                target_width, target_height,
                fill, font_family, font_weight
            )
            text_count += 1
    
    print(f"   渲染完成: {text_count} 个文字, {shape_count} 个形状")
    img.save(output_path)
    print(f"   图片已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="使用 Vision API 和 Gemini 生成 fabric.js 图层")
    parser.add_argument("--image", "-i", default="layer1.jpg", help="输入图片名称")
    parser.add_argument("--output", "-o", default="output.jpg", help="输出图片名称")
    parser.add_argument("--base-output", "-b", default="base_image.png", help="移除文字后的 base image")
    parser.add_argument("--json-output", "-j", default="fabric_output.json", help="最终 fabric.js JSON")
    parser.add_argument("--vision-output", "-v", default="vision_result.json", help="Vision API 结果")
    parser.add_argument("--initial-output", default="fabric_initial.json", help="初步 fabric.js JSON")
    
    args = parser.parse_args()
    
    script_dir = Path(__file__).parent
    image_dir = script_dir / IMAGE_DIR
    image_dir.mkdir(exist_ok=True)
    
    # 处理路径
    image_path = image_dir / args.image if not Path(args.image).is_absolute() else Path(args.image)
    output_path = image_dir / args.output
    base_image_path = image_dir / args.base_output
    json_output_path = image_dir / args.json_output
    vision_output_path = image_dir / args.vision_output
    initial_output_path = image_dir / args.initial_output
    
    if not image_path.exists():
        print(f"错误: 图片文件不存在: {image_path}")
        return
    
    print("=" * 60)
    print("Vision API + Gemini 图层生成工具 (五步流程)")
    print("=" * 60)
    print(f"   工作目录: {image_dir}")
    
    # 获取原图尺寸
    original_size = get_image_dimensions(str(image_path))
    print(f"   原图尺寸: {original_size[0]}x{original_size[1]}")
    
    # Step 1: Vision API 分析
    vision_result = analyze_image_with_vision_api(str(image_path))
    with open(vision_output_path, "w", encoding="utf-8") as f:
        json.dump(vision_result, f, indent=2, ensure_ascii=False)
    print(f"   保存到: {vision_output_path}")
    
    # Step 2: Gemini 初步分析（使用字体参考图片或 Google Search grounding）
    initial_fabric = generate_initial_fabric_json(str(image_path), image_dir)
    with open(initial_output_path, "w", encoding="utf-8") as f:
        json.dump(initial_fabric, f, indent=2, ensure_ascii=False)
    print(f"   保存到: {initial_output_path}")
    
    # Step 3: 整合修正
    final_fabric = merge_and_correct_fabric_json(str(image_path), vision_result, initial_fabric)
    with open(json_output_path, "w", encoding="utf-8") as f:
        json.dump(final_fabric, f, indent=2, ensure_ascii=False)
    print(f"   保存到: {json_output_path}")
    
    # Step 4: 使用 Gemini Image 移除文字
    base_image_success = remove_text_with_gemini_image(str(image_path), str(base_image_path))
    
    # Step 5: 在 base image 上渲染新文字
    if base_image_success:
        render_on_base_image(final_fabric, str(base_image_path), str(output_path), original_size)
    else:
        print("\n   警告: 使用备用方案（颜色覆盖）")
        render_fabric_to_image(final_fabric, str(output_path), str(image_path), 
                               use_original_as_background=True, vision_result=vision_result)
    
    print("\n" + "=" * 60)
    print("处理完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
