# Vision API + Gemini 图层生成工具

使用 Google Cloud Vision API 和 Gemini 3 Pro Preview 模型，通过三步流程精确识别并重建图片中的文字和图层。

## 功能概述

本工具采用**三步流程**，结合 Vision API 的精确坐标检测和 Gemini 的视觉分析能力：

1. **Step 1: Vision API 文字识别** - 获取精确的文字坐标
2. **Step 2: Gemini 初步分析** - 生成初步的 fabric.js JSON（背景、形状、文字样式）
3. **Step 3: Gemini 整合修正** - 将前两步结果 + 原图一起送给 Gemini 做最终整合
4. **Step 4: 渲染输出** - 基于最终 JSON 渲染生成新图片

## 前置要求

### 1. Google Cloud 项目配置

确保您已经：
- 创建了 Google Cloud 项目
- 启用了 Cloud Vision API
- 启用了 Vertex AI API
- 配置了身份验证

```bash
# 设置项目 ID
export GOOGLE_CLOUD_PROJECT="handy-cathode-440703-r2"
export GOOGLE_CLOUD_LOCATION="global"

# 使用 gcloud 进行身份验证
gcloud auth application-default login
```

### 2. 安装依赖

```bash
cd /Users/sunnyfang/Documents/git/imagelayer
pip install -r requirements.txt
```

## 使用方法

### 基本用法

```bash
# 使用默认配置（分析 image/layer1.jpg）
python vision_to_fabric.py

# 指定输入图片
python vision_to_fabric.py -i myimage.jpg

# 指定所有输出路径
python vision_to_fabric.py -i myimage.jpg -o result.jpg -j final.json
```

### 命令行参数

| 参数 | 短参数 | 默认值 | 说明 |
|------|--------|--------|------|
| `--image` | `-i` | `layer1.jpg` | 输入图片名称（放在 image/ 目录下）|
| `--output` | `-o` | `output.jpg` | 输出图片名称 |
| `--json-output` | `-j` | `fabric_output.json` | 最终 fabric.js JSON |
| `--vision-output` | `-v` | `vision_result.json` | Vision API 结果 |
| `--initial-output` | - | `fabric_initial.json` | 初步 fabric.js JSON |

## 输出文件

运行后会在 `image/` 目录下生成以下文件：

| 文件 | 说明 |
|------|------|
| `vision_result.json` | Vision API 返回的文字坐标信息 |
| `fabric_initial.json` | Gemini 初步分析的 fabric.js JSON |
| `fabric_output.json` | **最终整合修正后的 fabric.js JSON** |
| `output.jpg` | 根据最终 JSON 渲染的新图片 |

## 三步流程详解

### Step 1: Vision API 文字识别

使用 Google Cloud Vision API 精确检测图片中的文字和坐标：

```json
{
  "text_blocks": [
    {
      "text": "TEMU",
      "left": 234,
      "top": 56,
      "width": 122,
      "height": 31,
      "center_x": 295.0,
      "center_y": 71.5
    }
  ],
  "text_lines": [
    {
      "texts": ["TEMU", "HOME"],
      "full_text": "TEMU HOME",
      "unified_top": 56,
      "unified_height": 31
    }
  ]
}
```

**关键特性：**
- 精确的边界框坐标 (left, top, width, height)
- 按行分组 (text_lines)，用于文字垂直对齐

### Step 2: Gemini 初步分析

Gemini 独立分析图片，生成初步的 fabric.js JSON：

```json
{
  "version": "5.3.0",
  "objects": [
    {"type": "rect", "left": 0, "top": 0, "width": 600, "height": 600, "fill": "#8B4513"},
    {"type": "textbox", "text": "TEMU HOME", "fontWeight": "bold", "fontStyle": "normal", "fill": "#FFFFFF"}
  ],
  "background": "#8B4513"
}
```

**关键特性：**
- 背景颜色识别
- 形状识别（矩形、圆形等）
- 文字样式识别（粗体、斜体、颜色）

### Step 3: Gemini 整合修正

**核心步骤**：将 Vision API 结果 + 初步 fabric.js + 原图一起发送给 Gemini，进行最终整合：

**整合规则：**
1. **文字坐标** → 使用 Vision API 的精确坐标
2. **文字样式** → 使用 Gemini 分析的样式（fontWeight, fontStyle, fill）
3. **同行文字** → 使用 unified_top 确保垂直对齐
4. **背景/形状** → 使用 Gemini 分析的结果

```json
{
  "version": "5.3.0",
  "objects": [
    {"type": "rect", "left": 0, "top": 0, "width": 600, "height": 600, "fill": "#8B4513"},
    {
      "type": "textbox",
      "text": "TEMU",
      "left": 234,      // ← 来自 Vision API
      "top": 56,        // ← 来自 Vision API
      "width": 122,     // ← 来自 Vision API
      "height": 31,     // ← 来自 Vision API
      "fontWeight": "bold",    // ← 来自 Gemini 分析
      "fontStyle": "normal",   // ← 来自 Gemini 分析
      "fill": "#FFFFFF"        // ← 来自 Gemini 分析
    }
  ]
}
```

### Step 4: 渲染输出

使用 PIL 根据最终 fabric.js JSON 渲染图片：

- 支持矩形、圆形、线条等形状
- 文字拉伸渲染以精确匹配边界框
- 支持粗体、斜体字体样式

## 工作流程图

```
┌──────────────────┐
│     输入图片      │
│  (image/*.jpg)   │
└────────┬─────────┘
         │
         ├──────────────────────────────────────────┐
         │                                          │
         ▼                                          ▼
┌──────────────────┐                    ┌──────────────────┐
│   Step 1         │                    │   Step 2         │
│   Vision API     │                    │   Gemini 初步    │
│   文字坐标识别    │                    │   图层分析       │
└────────┬─────────┘                    └────────┬─────────┘
         │                                       │
         │  vision_result.json                   │  fabric_initial.json
         │                                       │
         └───────────────┬───────────────────────┘
                         │
                         ▼
              ┌──────────────────┐
              │   Step 3         │
              │   Gemini 整合    │  ◄─── + 原图
              │   修正坐标       │
              └────────┬─────────┘
                       │
                       │  fabric_output.json (最终结果)
                       │
                       ▼
              ┌──────────────────┐
              │   Step 4         │
              │   PIL 渲染       │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │   输出图片        │
              │   output.jpg     │
              └──────────────────┘
```

## 为什么使用三步流程？

| 问题 | 解决方案 |
|------|----------|
| Gemini 单独分析时坐标不够精确 | Step 1: Vision API 提供精确坐标 |
| Vision API 不提供字体样式信息 | Step 2: Gemini 分析字体样式 |
| 需要同时利用两者的优势 | Step 3: Gemini 整合两个数据源 |
| 同一行文字需要垂直对齐 | text_lines 提供 unified_top |

## 注意事项

1. **API 配额**: Vision API 和 Vertex AI 都有使用配额限制
2. **图片格式**: 支持 JPEG、PNG、GIF、WebP 格式
3. **文字识别**: Vision API 对清晰、高对比度的文字效果更好
4. **字体渲染**: 使用系统字体（Arial/Helvetica），可能与原图字体有差异
5. **图片目录**: 图片需要放在 `image/` 子目录下

## 故障排除

### 认证错误

```bash
# 重新进行身份验证
gcloud auth application-default login
```

### 缺少 API 权限

```bash
# 启用必要的 API
gcloud services enable vision.googleapis.com
gcloud services enable aiplatform.googleapis.com
```

### 图片文件不存在

确保图片放在 `imagelayer/image/` 目录下：

```bash
ls imagelayer/image/
# 应该看到 layer1.jpg 或其他图片文件
```

### 模块未找到

```bash
# 重新安装依赖
pip install -r requirements.txt --upgrade
```

## 许可证

MIT License
