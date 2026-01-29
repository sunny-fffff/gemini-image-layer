#!/usr/bin/env python3
"""
使用 Google Cloud Vision API 和 Gemini 3 Pro Preview 生成 fabric.js 图层
流程:
1. 使用 Cloud Vision API 分析图片获取文字坐标
2. 使用 Gemini 3 Pro Preview 分析图片生成初步的 fabric.js JSON
3. 将 Vision API 结果 + 初步 fabric.js + 原图送给 Gemini 做整合修正
4. 使用最终的 fabric.js JSON 渲染生成新图片
"""

import os
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


def get_image_part(image_path: str) -> types.Part:
    """获取图片 Part"""
    with open(image_path, "rb") as f:
        image_data = f.read()
    
    if image_path.lower().endswith('.png'):
        mime_type = "image/png"
    elif image_path.lower().endswith('.gif'):
        mime_type = "image/gif"
    elif image_path.lower().endswith('.webp'):
        mime_type = "image/webp"
    else:
        mime_type = "image/jpeg"
    
    return types.Part.from_bytes(data=image_data, mime_type=mime_type)


def generate_initial_fabric_json(image_path: str) -> dict:
    """
    Step 2: 使用 Gemini 分析图片生成初步的 fabric.js JSON（不考虑 Vision API 结果）
    """
    print(f"\n[Step 2] 使用 Gemini 分析图片生成初步 fabric.js JSON")
    
    client = get_gemini_client()
    width, height = get_image_dimensions(image_path)
    image_part = get_image_part(image_path)
    
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

## 输出格式（只输出 JSON）
```json
{{
  "version": "5.3.0",
  "objects": [
    {{"type": "rect", "left": 0, "top": 0, "width": {width}, "height": {height}, "fill": "#FFFFFF"}},
    {{"type": "textbox", "text": "文字", "left": 100, "top": 50, "fontSize": 24, "fontWeight": "bold", "fontStyle": "normal", "fill": "#000000"}}
  ],
  "background": "#FFFFFF"
}}
```

请只输出 JSON，不要有其他说明。"""

    contents = [types.Content(role="user", parts=[image_part, types.Part.from_text(text=prompt_text)])]
    
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
    """根据字体样式获取字体"""
    font_paths = {
        "bold": [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ],
        "normal": [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ],
    }
    
    is_bold = font_weight.lower() == "bold" or "bold" in font_family.lower() or "black" in font_family.lower()
    paths_to_try = font_paths["bold"] if is_bold else font_paths["normal"]
    
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


def render_fabric_to_image(fabric_json: dict, output_path: str, original_image_path: str = None):
    """
    Step 4: 使用 PIL 根据 fabric.js JSON 渲染生成新图片
    """
    print(f"\n[Step 4] 根据 fabric.js JSON 生成新图片: {output_path}")
    
    if "error" in fabric_json:
        print("   错误: fabric.js JSON 无效，无法生成图片")
        return
    
    # 获取画布尺寸
    if original_image_path:
        width, height = get_image_dimensions(original_image_path)
    else:
        width, height = 800, 600
    
    # 创建画布
    background_color = fabric_json.get("background", "#ffffff")
    if not background_color or background_color == "transparent":
        background_color = "#ffffff"
    if background_color.startswith("rgba"):
        background_color = "#ffffff"
    
    img = PILImage.new("RGB", (width, height), background_color)
    draw = ImageDraw.Draw(img)
    
    # 渲染每个对象
    objects = fabric_json.get("objects", [])
    print(f"   渲染 {len(objects)} 个对象...")
    
    for obj in objects:
        obj_type = obj.get("type", "")
        left = float(obj.get("left", 0))
        top = float(obj.get("top", 0))
        
        if obj_type == "rect":
            obj_width = float(obj.get("width", 100))
            obj_height = float(obj.get("height", 100))
            fill = obj.get("fill", "#ffffff")
            
            if fill and fill != "transparent":
                draw.rectangle(
                    [int(left), int(top), int(left + obj_width), int(top + obj_height)],
                    fill=fill
                )
        
        elif obj_type == "circle":
            radius = float(obj.get("radius", 50))
            fill = obj.get("fill", "#ffffff")
            
            if fill and fill != "transparent":
                draw.ellipse(
                    [int(left - radius), int(top - radius), int(left + radius), int(top + radius)],
                    fill=fill
                )
        
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
        
        elif obj_type == "line":
            x2 = float(obj.get("x2", left + 100))
            y2 = float(obj.get("y2", top))
            stroke = obj.get("stroke", "#000000")
            stroke_width = int(obj.get("strokeWidth", 1))
            draw.line([int(left), int(top), int(x2), int(y2)], fill=stroke, width=stroke_width)
    
    img.save(output_path)
    print(f"   图片已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="使用 Vision API 和 Gemini 生成 fabric.js 图层")
    parser.add_argument("--image", "-i", default="layer1.jpg", help="输入图片名称")
    parser.add_argument("--output", "-o", default="output.jpg", help="输出图片名称")
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
    json_output_path = image_dir / args.json_output
    vision_output_path = image_dir / args.vision_output
    initial_output_path = image_dir / args.initial_output
    
    if not image_path.exists():
        print(f"错误: 图片文件不存在: {image_path}")
        return
    
    print("=" * 60)
    print("Vision API + Gemini 图层生成工具 (三步流程)")
    print("=" * 60)
    
    # Step 1: Vision API 分析
    vision_result = analyze_image_with_vision_api(str(image_path))
    with open(vision_output_path, "w", encoding="utf-8") as f:
        json.dump(vision_result, f, indent=2, ensure_ascii=False)
    print(f"   保存到: {vision_output_path}")
    
    # Step 2: Gemini 初步分析
    initial_fabric = generate_initial_fabric_json(str(image_path))
    with open(initial_output_path, "w", encoding="utf-8") as f:
        json.dump(initial_fabric, f, indent=2, ensure_ascii=False)
    print(f"   保存到: {initial_output_path}")
    
    # Step 3: 整合修正
    final_fabric = merge_and_correct_fabric_json(str(image_path), vision_result, initial_fabric)
    with open(json_output_path, "w", encoding="utf-8") as f:
        json.dump(final_fabric, f, indent=2, ensure_ascii=False)
    print(f"   保存到: {json_output_path}")
    
    # Step 4: 渲染
    render_fabric_to_image(final_fabric, str(output_path), str(image_path))
    
    print("\n" + "=" * 60)
    print("处理完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
