# Vision API + Gemini 图层生成工具

使用 Google Cloud Vision API 和 Gemini 3 Pro 模型，通过五步流程精确识别并重建图片中的文字和图层。

## 功能概述

本工具采用**五步流程**，结合 Vision API 的精确坐标检测、Gemini 的视觉分析能力和 Gemini Image 的图像生成能力：

1. **Step 1: Vision API 文字识别** - 获取精确的文字坐标
2. **Step 2: Gemini 初步分析** - 生成初步的 fabric.js JSON（背景、形状、文字样式、字体类型）
3. **Step 3: Gemini 整合修正** - 将前两步结果 + 原图一起送给 Gemini 做最终整合
4. **Step 4: Gemini Image 移除文字** - 使用 gemini-3-pro-image-preview 移除原图文字，生成干净的 base image
5. **Step 5: 渲染输出** - 在 base image 上渲染新文字

## 前置要求

### 1. Google Cloud 项目配置

确保您已经：
- 创建了 Google Cloud 项目
- 启用了 Cloud Vision API
- 启用了 Vertex AI API
- 配置了身份验证

```bash
# 设置项目 ID
export GOOGLE_CLOUD_PROJECT="your-project-id"
export GOOGLE_CLOUD_LOCATION="global"

# 设置 API Key（可选，如果使用 API Key 认证）
export GOOGLE_CLOUD_API_KEY="your-api-key"

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
python vision_to_fabric.py -i myimage.jpg -o result.jpg -b base.png -j final.json
```

### 命令行参数

| 参数 | 短参数 | 默认值 | 说明 |
|------|--------|--------|------|
| `--image` | `-i` | `layer1.jpg` | 输入图片名称（放在 image/ 目录下）|
| `--output` | `-o` | `output.jpg` | 输出图片名称 |
| `--base-output` | `-b` | `base_image.png` | **移除文字后的 base image** |
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
| `base_image.png` | **Gemini Image 生成的无文字干净图片** |
| `output.jpg` | 最终渲染的输出图片 |

## 五步流程详解

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
    {
      "type": "textbox", 
      "text": "TEMU HOME", 
      "fontWeight": "bold", 
      "fontStyle": "normal",
      "fontFamily": "Sans-serif",
      "fill": "#FFFFFF"
    }
  ],
  "background": "#8B4513"
}
```

**关键特性：**
- 背景颜色识别
- 形状识别（矩形、圆形等）
- 文字样式识别（粗体、斜体、颜色）
- **字体类型识别**（Serif、Sans-serif、Cursive 等）

### Step 3: Gemini 整合修正

**核心步骤**：将 Vision API 结果 + 初步 fabric.js + 原图一起发送给 Gemini，进行最终整合：

**整合规则：**
1. **文字坐标** → 使用 Vision API 的精确坐标
2. **文字样式** → 使用 Gemini 分析的样式（fontWeight, fontStyle, fontFamily, fill）
3. **同行文字** → 使用 unified_top 确保垂直对齐
4. **背景/形状** → 使用 Gemini 分析的结果

```json
{
  "type": "textbox",
  "text": "TEMU",
  "left": 234,           // ← 来自 Vision API
  "top": 56,             // ← 来自 Vision API
  "width": 122,          // ← 来自 Vision API
  "height": 31,          // ← 来自 Vision API
  "fontWeight": "bold",  // ← 来自 Gemini 分析
  "fontStyle": "normal", // ← 来自 Gemini 分析
  "fontFamily": "Serif", // ← 来自 Gemini 分析
  "fill": "#FFFFFF"      // ← 来自 Gemini 分析
}
```

### Step 4: Gemini Image 移除文字 (新功能)

使用 **gemini-3-pro-image-preview** 模型移除原图中的所有文字，生成干净的 base image：

```python
prompt = """Please remove ALL text and words from this image. 
Keep the product, background, and all other visual elements intact.
Only remove the text overlays, logos with text, and any written words.
The result should be a clean image without any text."""
```

**优势：**
- 比简单的颜色覆盖效果更自然
- 保留商品和背景的完整性
- 避免文字重叠问题

### Step 5: 渲染输出

在 base image 上渲染新文字和形状：

- 自动计算坐标缩放比例（如果 base image 尺寸与原图不同）
- 支持多种字体类型（Serif、Sans-serif、Cursive 等）
- 支持粗体、斜体样式

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
                       │  fabric_output.json
                       │
         ┌─────────────┴─────────────┐
         │                           │
         ▼                           ▼
┌──────────────────┐      ┌──────────────────┐
│   Step 4         │      │   fabric.js      │
│   Gemini Image   │      │   JSON 数据      │
│   移除文字       │      │                  │
└────────┬─────────┘      └────────┬─────────┘
         │                         │
         │  base_image.png         │
         │                         │
         └───────────┬─────────────┘
                     │
                     ▼
          ┌──────────────────┐
          │   Step 5         │
          │   渲染文字       │
          │   到 base image  │
          └────────┬─────────┘
                   │
                   ▼
          ┌──────────────────┐
          │   输出图片        │
          │   output.jpg     │
          └──────────────────┘
```

## 字体识别指南

Gemini 会根据以下特征识别字体类型：

### Serif 衬线字体
- **特征**: 字母末端有小的横线或装饰
- **示例**: Times New Roman, Georgia, Garamond
- **渲染字体**: Times New Roman

### Sans-serif 无衬线字体
- **特征**: 字母末端没有装饰，线条均匀
- **示例**: Arial, Helvetica, Verdana
- **渲染字体**: Arial

### Cursive 手写体
- **特征**: 模仿手写，有连笔或书法风格
- **示例**: Brush Script, Pacifico
- **渲染字体**: Brush Script

### 支持的字体映射

| Gemini 识别 | 渲染字体 |
|-------------|----------|
| Serif | Times New Roman |
| Times, Times New Roman | Times New Roman |
| Georgia | Georgia |
| Sans-serif | Arial |
| Arial | Arial |
| Helvetica | Helvetica |
| Cursive | Brush Script |
| Impact | Impact |

## 为什么使用五步流程？

| 问题 | 解决方案 |
|------|----------|
| Gemini 单独分析时坐标不够精确 | Step 1: Vision API 提供精确坐标 |
| Vision API 不提供字体样式信息 | Step 2: Gemini 分析字体样式和类型 |
| 需要同时利用两者的优势 | Step 3: Gemini 整合两个数据源 |
| 简单颜色覆盖不够自然 | Step 4: Gemini Image 智能移除文字 |
| 需要处理尺寸差异 | Step 5: 自动坐标缩放渲染 |

## 备用方案

如果 Step 4 (Gemini Image) 失败，系统会自动使用备用方案：
- 使用原图作为背景
- 用周围颜色覆盖原文字区域
- 渲染新文字

## 注意事项

1. **API 配额**: Vision API、Vertex AI 和 Gemini Image 都有使用配额限制
2. **图片格式**: 支持 JPEG、PNG、GIF、WebP 格式
3. **文字识别**: Vision API 对清晰、高对比度的文字效果更好
4. **字体渲染**: 使用系统字体（macOS），可能与原图字体有差异
5. **图片目录**: 图片需要放在 `image/` 子目录下
6. **尺寸差异**: Gemini Image 生成的 base image 可能与原图尺寸不同，系统会自动缩放

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

### Gemini Image 生成失败

如果 Step 4 失败，系统会自动使用备用方案。可能的原因：
- API 配额用尽
- 模型暂时不可用
- 图片内容触发安全过滤

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

## 版本历史

### v2.0 (当前版本)
- 新增 Step 4: Gemini Image 移除文字
- 新增 Step 5: 在 base image 上渲染
- 增强字体识别（支持 Serif、Sans-serif、Cursive 等）
- 自动坐标缩放支持

### v1.0
- 初始版本
- 三步流程：Vision API + Gemini 分析 + 整合渲染

## 许可证

MIT License
