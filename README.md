# MarkItDown CLI

将 PDF、Office 文档、图片、音视频等文件转换为 Markdown/HTML 的命令行工具。基于 [microsoft/markitdown](https://github.com/microsoft/markitdown) 构建，集成 OCR、缩略图提取、PDF 导出等功能。

## 目录结构

```
markitdown/              git submodule — 上游源码
overrides/               自定义补丁（ocr、thumbnail、html、pdf、router、extractor、server）
scripts/
  build.py               跨平台构建脚本
  markitdown_cli_wrapper.py  PyInstaller 入口
  render_page.py         UNO 逐页渲染脚本（由 LO 内置 Python 执行）
  probe_uno.py           UNO listener 探活脚本
markitdown.spec          PyInstaller 配置
```

## 构建

### 前置依赖

| 平台 | 需求 |
|---|---|
| Windows | 7-Zip（自动下载 Tesseract）；LibreOffice（Office 转 PDF / OCR 需要） |
| macOS | Homebrew + `brew install tesseract dylibbundler`；LibreOffice |
| Linux | —（自动下载静态 musl Tesseract）；LibreOffice |

### 命令

```bash
python scripts/build.py
```

构建产物输出到 `dist/`：

```
dist/            ← 自包含，不依赖系统环境
├── markitdown.exe
├── _internal/              Python 依赖与运行时
├── tesseract/              便携版 Tesseract OCR
│   ├── tesseract.exe
│   ├── *.dll
│   └── tessdata/ (eng, chi_sim, chi_tra)
├── exiftool/               ExifTool 元数据
└── render_page.py          UNO 逐页渲染脚本
```

### 构建选项

```bash
python scripts/build.py --skip-tesseract   # 跳过 Tesseract 打包
python scripts/build.py --skip-deps        # 跳过 pip install
python scripts/build.py --skip-overrides   # 跳过应用补丁
```

## 路由与分块处理（`_router.py`）

文档处理根据文件类型、大小和 OCR 模式自动路由：

- **PDF**：支持 `--pages` 选页，大文件自动按块处理（普通模式 50 页/块，OCR 模式 5 页/块）
- **大 XLSX**（>20MB 且非 OCR）：`openpyxl(read_only=True)` 流式读取，避免 OOM
- **Office 文件 + OCR**：经 LibreOffice / win32com 转为 PDF 后递归路由，`--pages` 在 PDF 阶段精确选页
- **Office 文件 + 普通**：直接提取文本（PPTX 使用 python-pptx，DOCX 使用 mammoth/markitdown）

## 子命令

### 1. `markitdown` — 转换为 Markdown

```bash
markitdown document.pdf                              # 输出到 stdout
markitdown document.pdf -o output.md                 # 输出到文件
markitdown < document.pdf > output.md                # 从 stdin
markitdown document.pdf --pages "1,3,5-7" -o out.md  # 选页
```

### 2. `markitdown thumbnail` — 提取封面/预览图

```bash
# 默认输出第 1 页（PDF 用 PyMuPDF 渲染，Office 用 LibreOffice 或嵌入图）
markitdown thumbnail document.pdf -o preview.png

# 指定页（多页时自动命名为 preview_1.png, preview_3.png）
markitdown thumbnail document.pdf -o preview.png --pages "1,3,5-7"

# 指定格式（或从输出后缀自动识别：.png .jpg .webp）
markitdown thumbnail document.pdf -o preview.jpg
markitdown thumbnail document.pptx -o preview.webp
markitdown thumbnail document.pdf -o out.img --format webp

# 调整渲染 DPI（默认 150）
markitdown thumbnail document.pdf -o preview.png --dpi 300
```

**渲染优先级：**

| 格式 | 首选 | 回退 1 | 回退 2 |
|---|---|---|---|
| PDF | PyMuPDF 直接渲染 | — | — |
| PPTX | win32com COM（Windows + Office） | LibreOffice → PDF → 渲染 | docProps/thumbnail.jpeg |
| DOCX | LibreOffice → PDF → 渲染 | word/media/ 嵌入图 | — |
| XLSX | LibreOffice → PDF → 渲染 | — | — |

### 3. `markitdown html` — 转换为 HTML

```bash
# 默认输出整个文档
markitdown html document.docx -o output.html

# 选页（PDF 直接支持，Office 需 LibreOffice）
markitdown html document.pdf --pages "1-3" -o preview.html
markitdown html document.pptx -o out.html
```

### 4. `markitdown pdf` — Office 文档转 PDF

```bash
markitdown pdf document.docx -o output.pdf                 # 整篇文档
markitdown pdf document.pptx -o preview.pdf --pages "1,3"  # 选页
markitdown pdf book.xlsx -o chapter.pdf --pages "5-10"
```

**转换优先级：**

| 格式 | 首选 | 回退 |
|---|---|---|
| DOCX | docx2pdf（Word COM / LO） | LibreOffice |
| PPTX | win32com COM（PowerPoint） | LibreOffice |
| XLSX | LibreOffice | — |

需要安装 **LibreOffice**（https://libreoffice.org）或 Microsoft Office（Windows COM）。

### LibreOffice 自动检测（`_libreoffice_detect.py`）

跨平台多级降级策略，自动定位 LibreOffice 安装路径：

| 平台 | 检测层级 |
|------|----------|
| **Windows** | ① 注册表（`HKLM\SOFTWARE\LibreOffice\UNO` → `WOW6432Node` → `HKCU`）→ ② 默认路径（`Program Files` / `Program Files (x86)`）→ ③ `where soffice`（PATH） |
| **macOS** | ① `/Applications/LibreOffice.app/Contents/MacOS/soffice` → ② `which soffice` |
| **Linux** | ① `which soffice` / `which libreoffice` → ② `dpkg -l` → ③ `rpm -qa` |

- 检测到后自动转换为短路径格式（Windows 8.3 路径，如 `C:\PROGRA~1\...`）
- 版本号惰性获取：路径检测不调用 `soffice --version`，仅在显式请求时执行
- 自动隐藏 Windows 控制台弹窗（`STARTF_USESHOWWINDOW` + `stdin=DEVNULL`）

## 统一 `--pages` 语法

所有子命令（`thumbnail`、`html`、`pdf`）支持相同的页码语法：

| 表达式 | 含义 |
|---|---|
| `1` | 第 1 页 |
| `1,3,5` | 第 1、3、5 页 |
| `1-5` | 第 1~5 页 |
| `1,3,5-7,10-12` | 混合 |
| `-5` | 第 1~5 页 |
| `5-` | 第 5 页至末尾 |

## OCR 支持

```bash
markitdown document.pdf --use-ocr --tesseract-lang eng+chi_sim
markitdown document.pdf --use-ocr --ocr-engine llm --llm-model gpt-4o
```

| 参数 | 说明 |
|---|---|
| `--use-ocr` | 启用 OCR |
| `--ocr-engine` | `tesseract`（默认）或 `llm` |
| `--tesseract-path` | 指定 Tesseract 可执行文件路径。省略时自动检测 |
| `--tesseract-lang` | 语言，如 `eng`、`chi_sim`、`eng+chi_sim` |
| `--llm-model` | LLM 模型名（`--ocr-engine=llm` 时需要） |

### Tesseract 自动检测（`_tesseract_service.py`）

当未通过 `--tesseract-path` 或环境变量指定路径时，按以下顺序自动查找：

| 优先级 | 来源 | 说明 |
|:---:|---|------|
| ① | `--tesseract-path` 参数 | 用户显式指定 |
| ② | `TESSERACT_PATH` 环境变量 | 全局环境变量 |
| ③ | `tesseract/` 子目录 | 打包后 `markitdown.exe` 同目录下的 `tesseract/tesseract.exe` |
| ④ | 可执行文件同目录 | 打包后 `markitdown.exe` 同目录的 `tesseract.exe` |
| ⑤ | `C:\Program Files\Tesseract-OCR\tesseract.exe` | 系统默认安装路径 |
| ⑥ | `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe` | 32 位备选路径 |

自动设置 `TESSDATA_PREFIX` 环境变量指向 `tessdata/` 目录（如果该目录存在于 Tesseract 同目录下）。

## LibreOffice 选项

```bash
markitdown document.pptx --libreoffice-path "C:\Program Files\LibreOffice\program\soffice.exe"
```

| 参数 | 说明 |
|---|---|
| `--libreoffice-path` | 指定 LibreOffice 可执行文件路径。省略时自动检测（注册表/常见路径/PATH） |

## 元数据支持

```bash
markitdown document.pdf --with-metadata        # 包含元数据
markitdown document.pdf --metadata-only        # 仅输出元数据
```

## 多指标并行提取（`--extract`）

`--extract` 支持一次调用同时提取多个指标，所有指标**并行执行**（`ThreadPoolExecutor`），总耗时 ≈ 最慢的单个指标。

```bash
# 所有指标并行提取，结果 JSON 输出到 stdout
markitdown document.pdf \
  --extract text,ocr,metadata,magika,thumbnail \
  --pages "1-3"
```

### `--extract` 取值

| 值 | 说明 | 对应 `--xxx-out` | 无 `--xxx-out` 时 |
|:---|------|:-----------------:|:-----------------:|
| `text` | 纯文本文件（magika group: `text`，如 `.txt` `.csv` `.html`） | `--text-out FILE` | 内联 `result.text.content` |
| `document` | PDF / Office 文档（magika group: `document`） | `--document-out FILE` | 内联 `result.document.content` |
| `ocr` | OCR 识别文本（**自动启用 OCR**，无需额外 `--use-ocr`） | `--ocr-out FILE` | 内联 `result.ocr.content` |
| `html` | HTML 转换 | `--html-out FILE` | 内联 `result.html.content` |
| `metadata` | 文件元数据 | `--metadata-out FILE` | 内联 `metadata` |
| `magika` | magika 文件类型识别 | `--magika-out FILE` | 内联 `magika` |
| `thumbnail` | 封面/预览图（**无需 `--pages`**） | `--thumbnail-out FILE` | base64 内联 `thumbnail.data` |

### 各指标输出路径控制

```bash
# 全部输出到文件，JSON 返回路径
markitdown document.pdf \
  --extract text,ocr,metadata,magika,thumbnail \
  --pages "1-3" \
  --text-out output.md \
  --ocr-out ocr.txt \
  --thumbnail-out preview.png

# 混合：部分存文件，部分内联
markitdown document.pdf \
  --extract text,ocr,metadata \
  --text-out output.md
  # ocr 和 metadata 无对应 --xxx-out → 内联在 JSON 中
```

**规则**：
- 指定 `--xxx-out` 则对应字段 `content=null`、`path=路径`；否则 `content=内容`、`path=null`
- `thumbnail` 不接受 `--pages`，总是取嵌入缩略图或渲染第 1 页
- 当 `--extract` 包含多个指标时自动输出 JSON（单个指标且无 `-o` 时兼容旧行为输出纯文本）

### 并行提取示意图

```
                ┌──────────┐
                │ file_bytes│
                └────┬─────┘
        ┌───────┬───┼───┬───────┬───────┬───────┐
        ▼       ▼   ▼   ▼       ▼       ▼       ▼
    magika  meta  thumb text   doc     ocr     html
    (0.01s) (0.1)(0.5) (1.3) (1.3)  (4~10s) (1.3s)
        └───────┴───┴───┴───────┴───────┴───────┘
                    总耗时 ≈ max(...) = 4~10s

优化：当 `document` + `ocr` 同时请求时，LO 仅运行一次，
预渲染的 PDF 在两者间共享，`document` 从 PDF 提取文本
（跳过原生提取，节省 ~1.3s）。
```

## Server 模式

常驻 HTTP API 服务，LibreOffice / Tesseract / magika 只加载一次，后续请求复用。

```bash
markitdown server                    # 5052, 被占用→5053→5054…
markitdown server --port 8080        # 指定端口
markitdown server --port 0           # 系统分配，stdout 获取端口号
markitdown server --port-file /tmp/port.txt  # 端口号写入文件
```

**常驻优化**：

| 组件 | 策略 |
|------|------|
| LibreOffice | UNO socket listener（端口 2083），`atexit` 自动清理 |
| LO 逐页渲染 | **PPTX 部分页面**时通过 `render_page.py`（LO 内置 Python）按需渲染，跳过全量转换 |
| Tesseract | `TesseractOCRService` 单例 |
| magika | `Magika()` 单例，模型常驻 |
| MarkItDown | 实例复用 |

> **PPTX OCR 第 1 页性能对比**：
> - CLI `--convert-to pdf`（全量 42 页）：**~10.3s**
> - UNO 逐页渲染（仅第 1 页）：**~6.3s**（-39%）
> - listener 已预热后：**~3.5s**（-66%）
>
> 当 UNO 不可用时，PPTX 直接返回空（避免 CLI 全量渲染的 10s 开销），其他格式降级到 CLI 模式。

### 端口号获取

启动时 stdout 首行打印 `PORT=5053`，调用方解析：

```javascript
const proc = spawn('markitdown', ['server']);
proc.stdout.on('data', (data) => {
  const m = data.toString().match(/PORT=(\d+)/);
  if (m) fetch(`http://127.0.0.1:${m[1]}/health`);
});
```

或 `--port-file` 方式：

```bash
markitdown server --port-file /tmp/markitdown.port
# 等待文件出现，内容即端口号
```

### API 接口

#### `GET /health`

```json
{
  "status": "ok",
  "version": "0.1.6",
  "uptime_sec": 3600,
  "libreoffice": {
    "detected": true,
    "version": "26.2.0.3",
    "mode": "cli"
  },
  "tesseract": {
    "detected": true,
    "lang": "eng+chi_sim"
  },
  "magika": {
    "detected": true
  }
}
```

#### `POST /extract`

支持两种请求方式：

**方式 A — JSON 模式（本地调用，推荐）**：

```json
POST /extract
Content-Type: application/json

{
    "file_path": "/path/to/document.pdf",
    "extract": ["text", "ocr", "metadata"],
    "pages": "1-3"
}
```

**方式 B — `multipart/form-data`（远程上传）**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `file` | file | ✅ | 上传文件 |
| `extract` | string | ✅ | 逗号分隔：`text,document,ocr,html,metadata,magika,thumbnail` |
| `pages` | string | 否 | 页码（仅影响 text/document/ocr/html） |
| `ocr_lang` | string | 否 | 默认 `"eng+chi_sim"` |
| `thumbnail_format` | string | 否 | `"png"` / `"jpg"` / `"webp"`，默认 `"png"` |

**Response**：

```json
{
  "status": "ok",
  "time_ms": 1234,
  "file": {
    "name": "document.pdf",
    "size": 1048576,
    "pages": 10
  },
  "extract": ["text", "ocr", "metadata", "magika", "thumbnail"],
  "pages": "1-3",
  "magika": {
    "label": "pdf",
    "mime_type": "application/pdf",
    "description": "PDF document",
    "group": "document",
    "extensions": ["pdf"]
  },
  "metadata": {
    "title": "英语语法系统学习",
    "author": null,
    "page_count": 10,
    "file_size": 1048576,
    "created": null,
    "modified": "2026-07-01T08:00:00"
  },
  "result": {
    "text": { "content": "## Page 1\n\n正文...", "length": 1234 },
    "ocr":  { "content": "OCR结果...", "length": 567 },
    "html": { "content": "<h1>Page 1</h1><p>...</p>", "length": 2000 },
    "pages_processed": 3
  },
  "thumbnail": {
    "format": "png", "dpi": 150,
    "data": "iVBORw0KGgo..."
  }
}
```

#### `GET /extract/:file_id`（大文件异步轮询）

```
→ 202 Accepted  { "status": "accepted", "file_id": "uuid", "poll_url": "/extract/uuid" }
→ GET /extract/uuid  → 200 OK  { "status": "ok", ... }
```

## CLI 输出 JSON 结构（`--extract` 多指标时）

```json
{
  "status": "ok",
  "time_ms": 1234,
  "file": {
    "name": "document.pdf",
    "size": 1048576,
    "pages": 10
  },
  "extract": ["text", "document", "ocr", "metadata", "magika", "thumbnail"],
  "pages": "1-3",
  "magika": {
    "label": "pdf",
    "mime_type": "application/pdf",
    "group": "document",
    "extensions": ["pdf"]
  },
  "metadata": {
    "file_size": 1048576,
    "page_count": 10,
    "created": null,
    "modified": "2026-07-01T08:00:00"
  },
  "result": {
    "text": {
      "content": "纯文本内容...",
      "length": 1234,
      "path": null
    },
    "document": {
      "content": "## 文档正文...",
      "length": 5678,
      "path": null
    },
    "ocr": {
      "content": "OCR结果...",
      "length": 567,
      "path": null
    },
    "pages_processed": 3
  },
  "thumbnail": {
    "format": "png", "dpi": 150,
    "data": "iVBORw0KGgo...",
    "path": null
  }
}
```

## 支持的输入格式

| 类别 | 格式 |
|---|---|
| PDF | `.pdf` |
| Office | `.docx` `.doc` `.pptx` `.ppt` `.xlsx` `.xls` |
| OpenDocument | `.odt` `.odp` `.ods` |
| 图片 | `.jpg` `.jpeg` `.png` `.gif` `.bmp` `.svg` `.webp` |
| 音视频 | `.mp3` `.wav` `.m4a` `.flac` `.mp4` `.mov` `.avi` |
| 网页 | `.html` `.htm` |
| 其他 | `.txt` `.csv` `.epub` `.msg` `.ipynb` `.xml` `.rss` |

## 输出格式

| 子命令 | 输出格式 | 页数支持 |
|---|---|---|
| `markitdown`（默认） | Markdown (`.md`) | ✅ |
| `markitdown thumbnail` | PNG / JPEG / WebP | ✅ |
| `markitdown html` | HTML (`.html`) | ✅ |
| `markitdown pdf` | PDF (`.pdf`) | ✅ |

## Electron 集成示例

```typescript
// 封面图获取（含回退链）
async function getCoverPreview(filePath: string): Promise<Buffer> {
  // 1. 直接提取 / 渲染封面
  const { exitCode } = await exec(
    `markitdown thumbnail "${filePath}" -o preview.png --pages "1"`
  );
  if (exitCode === 0) return readFile('preview.png');

  // 2. 回退：转 HTML 后用 Electron 截图
  await exec(`markitdown html "${filePath}" -o preview.html --pages "1"`);
  await win.loadFile('preview.html');
  return (await win.webContents.capturePage()).toPNG();
}

// 文档转 PDF
const { exitCode } = await exec(
  `markitdown pdf "${docxPath}" -o "${pdfPath}"`
);

// OCR 识别
const { stdout } = await exec(
  `markitdown "${scannedPDF}" --use-ocr --tesseract-lang chi_sim+eng`
);
```

## License

MIT
