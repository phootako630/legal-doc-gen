# 盘点案卷材料：Word 转 UTF-8 文本，PDF 判断是否扫描件，扫描页渲染成 PNG 供逐页查看。
# 用法：python3 -I inspect.py <案卷目录> <输出目录>
# 输出：<输出目录>/summary.md（每份文件的类型、页数、是否扫描件、文本开头）、
#       <输出目录>/text/*.txt、<输出目录>/pages/<文件序号>-<页码>.png
import shutil
import subprocess
import sys
from pathlib import Path

import fitz  # PyMuPDF，后端 requirements 已包含

SCANNED_CHARS = 50  # 与 backend/app/config.py 的 SCANNED_PDF_TEXT_THRESHOLD 一致
ZOOM = 1.1  # 渲染倍率：A4 约 650×920 像素，Read 看图时文字清晰、体积小


def ensure_soffice() -> str:
    exe = shutil.which("soffice")
    if exe is None:
        subprocess.run(
            ["apt-get", "install", "-y", "-q", "libreoffice-writer"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        exe = shutil.which("soffice")
    return exe or "soffice"


def word_to_text(src: Path, out_dir: Path) -> Path:
    subprocess.run(
        [
            ensure_soffice(),
            "--headless",
            "--convert-to",
            "txt:Text (encoded):UTF8",
            "--outdir",
            str(out_dir),
            str(src),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return out_dir / (src.stem + ".txt")


def main() -> None:
    case_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    (out / "text").mkdir(parents=True, exist_ok=True)
    (out / "pages").mkdir(parents=True, exist_ok=True)
    lines = ["# 案卷材料盘点", ""]
    files = sorted(p for p in case_dir.rglob("*") if p.is_file())
    for idx, f in enumerate(files, start=1):
        ext = f.suffix.lower()
        lines.append(f"## {idx}. {f.relative_to(case_dir)}")
        if ext in (".doc", ".docx"):
            txt = word_to_text(f, out / "text")
            body = txt.read_text(encoding="utf-8-sig", errors="replace")
            lines += [
                f"- Word，{len(body)} 字，全文：`{txt}`",
                "",
                "```",
                body[:400],
                "```",
                "",
            ]
        elif ext == ".pdf":
            doc = fitz.open(f)
            texts = [p.get_text() for p in doc]
            scanned = [
                i + 1 for i, t in enumerate(texts) if len(t.strip()) < SCANNED_CHARS
            ]
            kind = (
                "扫描件"
                if len(scanned) == len(texts)
                else ("部分扫描" if scanned else "可解析文本")
            )
            lines.append(f"- PDF，{len(doc)} 页，{kind}")
            if len(scanned) < len(texts):
                txt = out / "text" / (f.stem + ".txt")
                txt.write_text(
                    "\n\n".join(f"--- 第 {i + 1} 页\n{t}" for i, t in enumerate(texts)),
                    encoding="utf-8",
                )
                lines += [f"- 文本层：`{txt}`", "", "```", "".join(texts)[:400], "```"]
            for page_no in scanned:
                png = out / "pages" / f"{idx:02d}-{page_no:03d}.png"
                doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM)).save(png)
            if scanned:
                lines.append(
                    f"- 扫描页图片：`{out / 'pages'}/{idx:02d}-*.png`（{len(scanned)} 张）"
                )
            lines.append("")
        else:
            lines += [f"- 其他格式（{ext or '无扩展名'}），未处理", ""]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(out / "summary.md")


if __name__ == "__main__":
    main()
