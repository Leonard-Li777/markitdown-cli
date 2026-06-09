# MarkItDown CLI

将 PDF、Office 文档、图片、音视频等文件转换为 Markdown/HTML 的命令行工具。基于 [microsoft/markitdown](https://github.com/microsoft/markitdown) 构建，集成 OCR、缩略图提取、PDF 导出等功能。

## 目录结构

```
markitdown/              git submodule — 上游源码
overrides/               自定义补丁（ocr、thumbnail、html、pdf）
scripts/
  build.py               跨平台构建脚本
  markitdown_cli_wrapper.py  PyInstaller 入口
markitdown.spec          PyInstaller 配置
```

## 构建

### 前置依赖

| 平台 | 需求 |
|---|---|
| Windows | 7-Zip（自动下载 Tesseract） |
| macOS | Homebrew + `brew install tesseract dylibbundler` |
| Linux | —（自动下载静态 musl Tesseract） |

### 命令

```bash
python scripts/build.py
```

构建产物输出到 `dist/`：

```
dist/
├── markitdown.exe        单文件可执行
└── tesseract/            便携版 Tesseract OCR
    ├── tesseract.exe
    ├── *.dll
    └── tessdata/
        ├── eng.traineddata
        ├── chi_sim.traineddata
        └── chi_tra.traineddata
```

### 构建选项

```bash
python scripts/build.py --skip-tesseract   # 跳过 Tesseract 打包
python scripts/build.py --skip-deps        # 跳过 pip install
python scripts/build.py --skip-overrides   # 跳过应用补丁
```

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

需要安装 LibreOffice（https://libreoffice.org）或 Microsoft Office。

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
| `--tesseract-path` | 指定 Tesseract 路径 |
| `--tesseract-lang` | 语言，如 `eng`、`chi_sim`、`eng+chi_sim` |
| `--llm-model` | LLM 模型名（`--ocr-engine=llm` 时需要） |

## 元数据支持

```bash
markitdown document.pdf --with-metadata        # 包含元数据
markitdown document.pdf --metadata-only        # 仅输出元数据
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
