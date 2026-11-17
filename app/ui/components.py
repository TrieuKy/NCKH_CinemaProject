"""
app/ui/components.py
--------------------
Thư viện thành phần giao diện dùng chung (UI Components):
- Từ điển KPI tập trung (Single Source of Truth: tên, công thức, tooltip).
- Thẻ chỉ số (kpi_card) & mẹo công thức (formula_tip).
- Huy hiệu trạng thái & 4 mức ưu tiên (badge).
- Thẻ nguồn dữ liệu (data_source_tag: MOCK, MANUAL, SYSTEM).
- Tiêu đề phân mục (section_header) & Dòng tóm tắt (summary_line).
- Khung thông báo (notice) & Trạng thái trống (empty_state).
- Nút chạy tác vụ nền (run_job) & Hộp xác nhận (confirm_dialog).
- Bảng dữ liệu tương tác (data_table) kèm xuất CSV / Excel.
"""

from __future__ import annotations

import io
from typing import Any, Callable, Sequence
import pandas as pd
import streamlit as st

from app.ui.theme import (
    COLOR_PRIMARY,
    COLOR_SECONDARY,
    PRIORITY_COLORS,
    DATA_SOURCE_COLORS,
    COLOR_TEXT_MAIN,
    COLOR_TEXT_MUTED,
)
from app.ui.format import (
    format_currency,
    format_decimal,
    format_percent,
    format_delta,
)


# =============================================================================
# 1. TỪ ĐIỂN KPI TẬP TRUNG (SINGLE SOURCE OF TRUTH)
# =============================================================================

KPI_DICTIONARY: dict[str, dict[str, str]] = {
    "revenue": {
        "name": "Doanh thu vé",
        "formula": "SUM(tong_tien)",
        "tooltip": "Tổng số tiền thu được từ bán vé xem phim trong khoảng thời gian đã chọn.",
        "unit": "đ",
        "format": "compact_currency",
    },
    "tickets": {
        "name": "Số vé bán",
        "formula": "SUM(so_luong)",
        "tooltip": "Tổng số lượng vé phát hành thực tế đến tay khán giả.",
        "unit": "vé",
        "format": "integer",
    },
    "atp": {
        "name": "Giá vé trung bình (ATP)",
        "formula": "Doanh thu vé / Số vé bán",
        "tooltip": "Average Ticket Price: Mức chi trả bình quân của khán giả cho mỗi tấm vé.",
        "unit": "đ/vé",
        "format": "currency",
    },
    "occupancy": {
        "name": "Tỷ lệ lấp đầy (Occupancy)",
        "formula": "Số vé bán / (Sức chứa × Số suất)",
        "tooltip": "Tỷ lệ phần trăm số ghế có khách ngồi trên tổng sức chứa danh định của phòng chiếu.",
        "unit": "%",
        "format": "percent",
    },
    "revpas": {
        "name": "Doanh thu trên mỗi ghế (RevPAS)",
        "formula": "Doanh thu vé / (Sức chứa × Số suất)",
        "tooltip": "Revenue Per Available Seat: Đo lường hiệu suất sinh lời trên mỗi ghế khả dụng.",
        "unit": "đ/ghế",
        "format": "currency",
    },
    "low_occupancy_rate": {
        "name": "Tỷ lệ suất thấp điểm",
        "formula": "Số suất (Occupancy < 30%) / Tổng số suất",
        "tooltip": "Tỷ lệ các suất chiếu vắng khách (< 30% chỗ) cần kích cầu hoặc cơ cấu lại giờ chiếu.",
        "unit": "%",
        "format": "percent",
    },
    "fnb_attach_rate": {
        "name": "Tỷ lệ gắn bắp nước (Attach Rate)",
        "formula": "Số giao dịch vé có F&B / Tổng giao dịch vé",
        "tooltip": "Tỷ lệ khán giả mua kèm bắp nước khi mua vé xem phim.",
        "unit": "%",
        "format": "percent",
    },
    "sph": {
        "name": "Chi tiêu bắp nước/khách (SPH)",
        "formula": "Doanh thu bắp nước / Tổng số khách",
        "tooltip": "Spend Per Head: Doanh thu F&B bình quân trên mỗi khách hàng đến rạp.",
        "unit": "đ/khách",
        "format": "currency",
    },
    "repeat_rate": {
        "name": "Tỷ lệ khách quay lại (90 ngày)",
        "formula": "Khách mua lại trong 90 ngày / Tổng khách có thẻ",
        "tooltip": "Tỷ lệ hội viên thực hiện giao dịch tiếp theo trong vòng 90 ngày kể từ lần gần nhất.",
        "unit": "%",
        "format": "percent",
    },
    "anomaly_count": {
        "name": "Số cảnh báo bất thường",
        "formula": "COUNT(suất / giao dịch gắn cờ cảnh báo)",
        "tooltip": "Số lượng suất chiếu hoặc giao dịch vi phạm ngưỡng kiểm soát (lỗi doanh thu, tràn sức chứa).",
        "unit": "vụ",
        "format": "integer",
    },
}


# =============================================================================
# 2. THẺ KPI (KPI CARD) & MẸO CÔNG THỨC (FORMULA TIP)
# =============================================================================

def formula_tip(kpi_key: str) -> None:
    """Hiển thị popover giải thích công thức và ý nghĩa của KPI từ từ điển duy nhất."""
    info = KPI_DICTIONARY.get(kpi_key)
    if not info:
        return

    with st.popover(f"ℹ️ Công thức {info['name']}", use_container_width=False):
        st.markdown(f"**Chỉ số:** {info['name']}")
        st.code(info["formula"], language="sql")
        st.caption(info["tooltip"])


def kpi_card(
    title: str | None = None,
    value: Any = None,
    delta: Any = None,
    delta_text: str | None = None,
    help_text: str | None = None,
    kpi_key: str | None = None,
    compact_currency: bool = False,
    unit: str = "",
    reverse_delta_color: bool = False,
) -> None:
    """
    Hiển thị thẻ KPI Card chuẩn nghiệp vụ:
    - kpi_key: Tự động lấy tên, công thức, đơn vị từ KPI_DICTIONARY nếu có.
    - value: Giá trị hiển thị.
    - delta: Giá trị so sánh (tự động gắn mũi tên ↑, ↓, →).
    - compact_currency: Rút gọn tỷ / triệu nếu là số tiền lớn.
    """
    card_title = title
    card_help = help_text
    card_unit = unit

    if kpi_key and kpi_key in KPI_DICTIONARY:
        meta = KPI_DICTIONARY[kpi_key]
        if not card_title:
            card_title = meta["name"]
        if not card_help:
            card_help = f"Công thức: {meta['formula']} — {meta['tooltip']}"
        if not card_unit and meta.get("unit"):
            card_unit = meta["unit"]

    card_title = card_title or "CHỈ SỐ"

    # Định dạng giá trị hiển thị
    val_str = "-"
    if value is not None:
        if isinstance(value, (int, float)):
            if compact_currency:
                val_str = format_currency(value, compact=True)
            elif card_unit == "đ" or card_unit.startswith("đ/"):
                val_str = format_currency(value, compact=False)
            elif card_unit == "%":
                # Nếu giá trị <= 1.0 (và > 0) thì nhân 100
                is_frac = 0.0 < abs(value) <= 1.0
                val_str = format_percent(value, multiply=is_frac)
            else:
                formatted_num = format_decimal(value, decimals=0 if isinstance(value, int) else 1)
                val_str = f"{formatted_num} {card_unit}".strip()
        else:
            val_str = str(value)

    # Xử lý delta nếu có
    delta_html = ""
    if delta_text is not None:
        delta_html = f'<div class="kpi-delta delta-flat">{delta_text}</div>'
    elif delta is not None:
        d_info = format_delta(delta, reverse_color=reverse_delta_color)
        color_class = f"delta-{d_info['direction']}"
        if d_info["color"] == "red":
            color_class = "delta-down"
        elif d_info["color"] == "green":
            color_class = "delta-up"
        delta_html = f'<div class="kpi-delta {color_class}">{d_info["text"]} so với kỳ trước</div>'

    help_attr = f'title="{card_help}"' if card_help else ""

    html = f"""
    <div class="kpi-card" {help_attr}>
        <div class="kpi-title">
            <span>{card_title}</span>
            {"<span style='cursor:help; font-size:12px;'>ⓘ</span>" if card_help else ""}
        </div>
        <div class="kpi-value">{val_str}</div>
        {delta_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# =============================================================================
# 3. HUY HIỆU (BADGE) KỂ CẢ 4 MỨC ƯU TIÊN
# =============================================================================

def badge(text: str, level: str = "info", size: str = "normal") -> str:
    """
    Sinh mã HTML hiển thị thẻ huy hiệu (Badge Pill):
    - Hỗ trợ 4 mức ưu tiên: 'Rất cao', 'Cao', 'Trung bình', 'Thấp'.
    - Hỗ trợ các trạng thái: 'success', 'warning', 'danger', 'info', 'secondary'.
    Có thể dùng trực tiếp hoặc chèn trong st.markdown(..., unsafe_allow_html=True).
    """
    font_size = "11px" if size == "small" else "12px"
    padding = "2px 8px" if size == "small" else "3px 10px"

    # Kiểm tra theo 4 mức ưu tiên
    p_match = None
    for p_name in PRIORITY_COLORS:
        if p_name.lower() == text.strip().lower() or level.lower() == p_name.lower():
            p_match = PRIORITY_COLORS[p_name]
            break

    if p_match:
        bg = p_match["bg"]
        color = p_match["text"]
        border = p_match["border"]
    else:
        lvl = level.lower()
        if lvl in ("success", "tích cực", "ok"):
            bg, color, border = "#ECFDF5", "#065F46", "#A7F3D0"
        elif lvl in ("warning", "trung tính", "warn"):
            bg, color, border = "#FFFBEB", "#92400E", "#FDE68A"
        elif lvl in ("danger", "error", "tiêu cực", "critical"):
            bg, color, border = "#FEF2F2", "#991B1B", "#FECACA"
        elif lvl in ("info", "blue"):
            bg, color, border = "#EFF6FF", "#1E40AF", "#BFDBFE"
        else:
            bg, color, border = "#F1F5F9", "#475569", "#CBD5E1"

    return (
        f'<span class="custom-badge" style="background-color: {bg}; color: {color}; '
        f'border: 1px solid {border}; font-size: {font_size}; padding: {padding};">{text}</span>'
    )


# =============================================================================
# 4. THẺ NGUỒN DỮ LIỆU (DATA SOURCE TAG)
# =============================================================================

def data_source_tag(source: str = "MOCK") -> str:
    """
    Huy hiệu đánh dấu nguồn gốc dữ liệu:
    - MOCK: Dữ liệu mô phỏng / sinh tự động.
    - MANUAL: Dữ liệu nhân viên nhập thủ công.
    - SYSTEM: Dữ liệu gốc từ kho hoặc tích hợp.
    """
    src_upper = str(source).upper().strip()
    cfg = DATA_SOURCE_COLORS.get(src_upper, DATA_SOURCE_COLORS["MOCK"])
    return (
        f'<span class="custom-badge" style="background-color: {cfg["bg"]}; color: {cfg["text"]}; '
        f'border: 1px solid {cfg["border"]}; font-size: 11px; padding: 2px 7px; font-weight: 700;">'
        f'[{src_upper}]</span>'
    )


# =============================================================================
# 5. TIÊU ĐỀ PHÂN MỤC (SECTION HEADER) & DÒNG TÓM TẮT (SUMMARY LINE)
# =============================================================================

def section_header(title: str, subtitle: str | None = None, icon: str | None = None) -> None:
    """Hiển thị tiêu đề phân mục có đường kẻ ngăn cách nhẹ."""
    icon_part = f"<span>{icon}</span> " if icon else ""
    sub_part = f'<div class="section-subtitle">{subtitle}</div>' if subtitle else ""
    html = f"""
    <div class="section-header-box">
        <h3 class="section-title">{icon_part}{title}</h3>
        {sub_part}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def summary_line(items: Sequence[tuple[str, Any]]) -> None:
    """
    Dòng tóm tắt nhanh số liệu ngang (Summary Strip):
    Ví dụ: summary_line([("Tổng suất chiếu", "45"), ("Vé bán", "3.200"), ("Doanh thu", "240.000.000 đ")])
    """
    items_html = []
    for label, val in items:
        items_html.append(f'<span class="summary-item">{label}: <strong>{val}</strong></span>')
    full_html = f'<div class="summary-strip">{" ".join(items_html)}</div>'
    st.markdown(full_html, unsafe_allow_html=True)


# =============================================================================
# 6. KHUNG THÔNG BÁO (NOTICE) & TRẠNG THÁI TRỐNG (EMPTY STATE)
# =============================================================================

def notice(message: str, type: str = "info") -> None:
    """Hiển thị khung thông báo kiểu callout: info, success, warning, danger."""
    t = type.lower()
    if t in ("error", "danger", "critical"):
        cls_name = "notice-danger"
        icon = "⚠️ "
    elif t in ("warning", "warn"):
        cls_name = "notice-warning"
        icon = "⚡ "
    elif t in ("success", "ok"):
        cls_name = "notice-success"
        icon = "✅ "
    else:
        cls_name = "notice-info"
        icon = "ℹ️ "

    html = f'<div class="notice-box {cls_name}">{icon}{message}</div>'
    st.markdown(html, unsafe_allow_html=True)


def empty_state(
    message: str = "Không tìm thấy dữ liệu phù hợp với bộ lọc hiện tại.",
    icon: str = "📭",
    action_label: str | None = None,
    on_action: Callable[[], None] | None = None,
) -> None:
    """Hiển thị khi danh sách hoặc bảng trả về rỗng."""
    st.markdown(
        f"""
        <div class="empty-box">
            <div class="empty-box-icon">{icon}</div>
            <div class="empty-box-text">{message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if action_label and on_action:
        col1, col2, col3 = st.columns([2, 1, 2])
        with col2:
            if st.button(action_label, use_container_width=True):
                on_action()


# =============================================================================
# 7. NÚT CHẠY TÁC VỤ (RUN JOB) & HỘP THOẠI XÁC NHẬN (CONFIRM DIALOG)
# =============================================================================

def run_job(
    job_name: str,
    func: Callable[..., Any],
    *args: Any,
    button_label: str | None = None,
    success_message: str | None = None,
    **kwargs: Any,
) -> Any:
    """
    Bọc thao tác chạy job / script với nút bấm, spinner trạng thái và thông báo kết quả.
    """
    lbl = button_label or f"🚀 Chạy {job_name}"
    if st.button(lbl, key=f"btn_job_{job_name}", type="primary"):
        with st.spinner(f"Đang thực hiện: {job_name}... Vui lòng đợi."):
            try:
                res = func(*args, **kwargs)
                st.success(success_message or f"Đã hoàn thành tác vụ '{job_name}' thành công!")
                return res
            except Exception as e:
                st.error(f"Lỗi khi thực thi '{job_name}': {e}")
                return None
    return None


def confirm_dialog(
    title: str,
    message: str,
    confirm_label: str = "Xác nhận",
    cancel_label: str = "Hủy bỏ",
    key: str = "confirm_dialog",
) -> bool:
    """
    Hộp thoại yêu cầu xác nhận thao tác quan trọng (như xóa dữ liệu, khóa số liệu).
    Trả về True khi người dùng nhấn Xác nhận.
    """
    with st.expander(f"⚠️ {title}", expanded=True):
        st.warning(message)
        col1, col2, col3 = st.columns([1, 1, 2])
        confirmed = False
        with col1:
            if st.button(confirm_label, key=f"{key}_yes", type="primary", use_container_width=True):
                confirmed = True
        with col2:
            st.button(cancel_label, key=f"{key}_no", use_container_width=True)
        return confirmed


# =============================================================================
# 8. BẢNG DỮ LIỆU TƯƠNG TÁC (DATA TABLE)
# =============================================================================

def data_table(
    df: pd.DataFrame,
    max_rows: int = 50,
    download_name: str = "du_lieu_cinema.csv",
    allow_download: bool = True,
    key: str | None = None,
) -> None:
    """
    Hiển thị DataFrame đẹp mắt kèm tùy chọn tải file CSV và Excel (.xlsx).
    """
    if df is None or df.empty:
        empty_state("Bảng hiện chưa có dòng dữ liệu nào.")
        return

    # Hiển thị bảng
    st.dataframe(df.head(max_rows), use_container_width=True)

    if allow_download:
        col_info, col_csv, col_excel = st.columns([3, 1, 1])
        with col_info:
            st.caption(f"Đang hiển thị {min(len(df), max_rows):,} / {len(df):,} dòng.")

        # Tải CSV
        with col_csv:
            csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                label="📥 Tải CSV",
                data=csv_bytes,
                file_name=download_name,
                mime="text/csv",
                key=f"{key}_csv" if key else None,
                use_container_width=True,
            )

        # Tải Excel
        with col_excel:
            try:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False, sheet_name="Data")
                buffer.seek(0)
                excel_name = download_name.replace(".csv", ".xlsx")
                st.download_button(
                    label="📊 Tải Excel",
                    data=buffer.getvalue(),
                    file_name=excel_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"{key}_excel" if key else None,
                    use_container_width=True,
                )
            except Exception:
                pass

