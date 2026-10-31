# 同名材料自动编号（#121）：字段出处、查看原件、来源锚定和 eval 的来源核对都按文件名认文件，
# 两份文件同名时分不清字段出自哪一份。上传时给第 2 份起的同名文件加序号「安装合同(2).pdf」，
# 一次会话内文件名唯一；第一份保留原名。
from __future__ import annotations

import os


def unique_filenames(names: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """返回 (改名后的文件名列表, [(原名, 新名)…])；新名与已有文件名也不冲突。"""
    taken = set(names)
    seen: set[str] = set()
    result: list[str] = []
    renamed: list[tuple[str, str]] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            result.append(name)
            continue
        stem, ext = os.path.splitext(name)
        n = 2
        while f"{stem}({n}){ext}" in taken:
            n += 1
        new = f"{stem}({n}){ext}"
        taken.add(new)
        seen.add(new)
        result.append(new)
        renamed.append((name, new))
    return result, renamed


def renamed_notes(renamed: list[tuple[str, str]]) -> list[str]:
    """上传页给律师看的说明。"""
    return [f"「{old}」有多份，这一份改称「{new}」，出处和查看原件按新名称区分" for old, new in renamed]
