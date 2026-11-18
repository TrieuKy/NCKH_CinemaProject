"""
etl/sql_utils.py
----------------
Tiện ích thực thi và phân tách câu lệnh SQL an toàn cho PostgreSQL.
Bảo đảm không tách nhầm dấu chấm phẩy (;) bên trong chuỗi ký tự, comment hoặc dollar-quote.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Union
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


def split_sql_statements(sql: str) -> List[str]:
    """
    Tách nội dung SQL thành từng câu lệnh riêng biệt dựa trên dấu ';'.
    Xử lý an toàn:
      - Bỏ qua ';' nằm trong chuỗi ký tự đơn ('...').
      - Bỏ qua ';' nằm trong comment một dòng (-- ...).
      - Bỏ qua ';' nằm trong comment nhiều dòng (/* ... */).
      - Bỏ qua ';' nằm trong dollar-quoted block ($$...$$ hoặc $tag$...$tag$).
    Bảo toàn thứ tự câu lệnh và loại bỏ các câu lệnh rỗng.
    """
    statements: List[str] = []
    current: List[str] = []
    length = len(sql)
    i = 0

    in_single_quote = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag: str | None = None

    while i < length:
        ch = sql[i]
        next_ch = sql[i + 1] if i + 1 < length else ""

        # 1. Đang trong comment một dòng
        if in_line_comment:
            current.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        # 2. Đang trong comment nhiều dòng
        if in_block_comment:
            current.append(ch)
            if ch == "*" and next_ch == "/":
                current.append("/")
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        # 3. Đang trong dollar quote ($tag$...$tag$)
        if dollar_tag is not None:
            current.append(ch)
            if ch == "$":
                tag_len = len(dollar_tag)
                # Kiểm tra chuỗi đóng trùng tag
                if sql[i:i + tag_len] == dollar_tag:
                    current.append(sql[i + 1:i + tag_len])
                    i += tag_len
                    dollar_tag = None
                    continue
            i += 1
            continue

        # 4. Đang trong chuỗi ký tự đơn ('...')
        if in_single_quote:
            current.append(ch)
            if ch == "'":
                # Ký tự escape ''
                if next_ch == "'":
                    current.append("'")
                    i += 2
                    continue
                in_single_quote = False
            i += 1
            continue

        # --- Ngoài tất cả trạng thái trên ---

        # Bắt đầu comment một dòng
        if ch == "-" and next_ch == "-":
            in_line_comment = True
            current.append(ch)
            current.append(next_ch)
            i += 2
            continue

        # Bắt đầu comment nhiều dòng
        if ch == "/" and next_ch == "*":
            in_block_comment = True
            current.append(ch)
            current.append(next_ch)
            i += 2
            continue

        # Bắt đầu dollar quote
        if ch == "$":
            match = re.match(r"^\$[A-Za-z0-9_]*\$", sql[i:])
            if match:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                i += len(dollar_tag)
                continue

        # Bắt đầu chuỗi đơn
        if ch == "'":
            in_single_quote = True
            current.append(ch)
            i += 1
            continue

        # Dấu chấm phẩy ngắt câu lệnh
        if ch == ";":
            stmt_text = "".join(current).strip()
            # Kiểm tra xem có câu lệnh thực chất (không chỉ toàn comment)
            no_comments = re.sub(r"--[^\n]*", "", stmt_text)
            no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.DOTALL).strip()
            if no_comments:
                statements.append(stmt_text)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    # Phần còn lại cuối file nếu không có dấu ; kết thúc
    if current:
        stmt_text = "".join(current).strip()
        no_comments = re.sub(r"--[^\n]*", "", stmt_text)
        no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.DOTALL).strip()
        if no_comments:
            statements.append(stmt_text)

    return statements


def execute_sql_statements(
    target: Union[Connection, Engine],
    sql_content: str,
) -> int:
    """
    Tách và thực thi các câu lệnh SQL trong `sql_content`.
    Trả về số câu lệnh đã thực thi.
    """
    stmts = split_sql_statements(sql_content)
    if isinstance(target, Engine):
        with target.begin() as conn:
            for s in stmts:
                conn.execute(text(s))
    else:
        for s in stmts:
            target.execute(text(s))
    return len(stmts)
