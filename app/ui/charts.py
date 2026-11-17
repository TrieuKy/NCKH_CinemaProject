"""
app/ui/charts.py
----------------
Tạo và cấu hình biểu đồ Plotly chuẩn cho toàn bộ ứng dụng:
- Mẫu Plotly chung: Nền trong suốt, lưới nhạt, bảng màu phân loại, màu cảm xúc.
- Hàm vẽ Heatmap tỷ lệ lấp đầy (Occupancy Heatmap).
- Hàm vẽ biểu đồ cảm xúc đánh giá (Sentiment Chart).
"""

from __future__ import annotations

from typing import Any, Sequence
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from app.ui.theme import (
    CHART_PALETTE,
    SENTIMENT_COLORS,
    HEATMAP_COLORSCALE,
    COLOR_TEXT_MAIN,
    COLOR_BORDER,
)


# =============================================================================
# 1. MẪU PLOTLY CHUNG (THEME & STYLING)
# =============================================================================

def apply_chart_theme(
    fig: go.Figure,
    title: str | None = None,
    height: int = 400,
    show_legend: bool = True,
    x_title: str | None = None,
    y_title: str | None = None,
) -> go.Figure:
    """
    Áp dụng chuẩn giao diện Plotly cho đồ thị:
    - Nền trong suốt (paper & plot background).
    - Đường gióng (grid) màu nhạt, không gây rối mắt.
    - Phông chữ hệ thống đồng bộ.
    - Margin hợp lý cho màn hình web.
    """
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        font=dict(
            family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
            color=COLOR_TEXT_MAIN,
            size=12,
        ),
        title=dict(
            text=title or "",
            font=dict(size=15, color=COLOR_TEXT_MAIN, weight=600),
            x=0.01,
            y=0.96,
        ) if title else None,
        margin=dict(l=45, r=25, t=55 if title else 25, b=45),
        showlegend=show_legend,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11),
        ),
        hoverlabel=dict(
            bgcolor="#1E293B",
            font_size=12,
            font_color="#FFFFFF",
            font_family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        ),
        colorway=CHART_PALETTE,
    )

    fig.update_xaxes(
        showgrid=True,
        gridcolor=COLOR_BORDER,
        gridwidth=1,
        zeroline=False,
        title_text=x_title,
        title_font=dict(size=12, color="#64748B"),
        tickfont=dict(size=11, color="#64748B"),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=COLOR_BORDER,
        gridwidth=1,
        zeroline=True,
        zerolinecolor="#CBD5E1",
        title_text=y_title,
        title_font=dict(size=12, color="#64748B"),
        tickfont=dict(size=11, color="#64748B"),
    )
    return fig


# =============================================================================
# 2. HEATMAP TỶ LỆ LẤP ĐẦY (OCCUPANCY HEATMAP)
# =============================================================================

def plot_occupancy_heatmap(
    data: pd.DataFrame | np.ndarray | Sequence[Sequence[float]],
    x_labels: Sequence[str] | None = None,
    y_labels: Sequence[str] | None = None,
    title: str = "Tỷ lệ lấp đầy theo suất chiếu (%)",
    height: int = 420,
    show_text: bool = True,
) -> go.Figure:
    """
    Vẽ heatmap tỷ lệ lấp đầy phòng chiếu / suất chiếu:
    - Data: ma trận giá trị phần trăm (0 - 100 hoặc 0.0 - 1.0)
    - Trục X: Khung giờ (ví dụ: 09:00, 13:00, 16:00, 19:00, 21:30)
    - Trục Y: Phòng chiếu hoặc Phim
    - Hiển thị nhãn giá trị kèm ký hiệu % và định dạng dấu phẩy tiếng Việt.
    """
    if isinstance(data, pd.DataFrame):
        z_values = data.values.copy()
        if x_labels is None:
            x_labels = [str(c) for c in data.columns]
        if y_labels is None:
            y_labels = [str(i) for i in data.index]
    else:
        z_values = np.array(data, dtype=float)

    # Chuẩn hóa về thang 0 - 100 nếu đầu vào là 0.0 - 1.0
    if np.nanmax(z_values) <= 1.0 and np.nanmax(z_values) > 0:
        z_values = z_values * 100.0

    # Tạo ma trận text hiển thị dấu phẩy kiểu Việt Nam (ví dụ: 78,5%)
    text_matrix = []
    for row in z_values:
        row_text = []
        for val in row:
            if np.isnan(val):
                row_text.append("-")
            else:
                row_text.append(f"{val:.1f}%".replace(".", ","))
        text_matrix.append(row_text)

    heatmap = go.Heatmap(
        z=z_values,
        x=list(x_labels) if x_labels is not None else None,
        y=list(y_labels) if y_labels is not None else None,
        text=text_matrix if show_text else None,
        texttemplate="%{text}" if show_text else None,
        textfont=dict(size=12, color="#0F172A", weight=600),
        colorscale=HEATMAP_COLORSCALE,
        zmin=0,
        zmax=100,
        colorbar=dict(
            title=dict(text="Lấp đầy (%)", font=dict(size=11, color="#64748B")),
            ticksuffix="%",
            thickness=14,
            len=0.9,
            tickfont=dict(size=10, color="#64748B"),
        ),
        hovertemplate="<b>%{y}</b><br>Khung giờ: %{x}<br>Tỷ lệ lấp đầy: <b>%{text}</b><extra></extra>",
    )

    fig = go.Figure(data=heatmap)
    fig = apply_chart_theme(fig, title=title, height=height, show_legend=False)

    # Ẩn lưới x/y cho heatmap để liền mạch
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=False)
    return fig


# =============================================================================
# 3. BIỂU ĐỒ CẢM XÚC ĐÁNH GIÁ (SENTIMENT PIE / DONUT)
# =============================================================================

def plot_sentiment_donut(
    sentiment_counts: dict[str, int],
    title: str = "Tỷ lệ cảm xúc đánh giá khách hàng",
    height: int = 350,
) -> go.Figure:
    """
    Vẽ biểu đồ hình vành khuyên (Donut) cảm xúc:
    - Tích cực: Xanh lá (#10B981)
    - Trung tính: Cam vàng (#F59E0B)
    - Tiêu cực: Đỏ (#EF4444)
    """
    labels = list(sentiment_counts.keys())
    values = list(sentiment_counts.values())
    colors = [SENTIMENT_COLORS.get(label, "#94A3B8") for label in labels]

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=colors, line=dict(color="#FFFFFF", width=2)),
                textinfo="label+percent",
                hovertemplate="<b>%{label}</b>: %{value:,} đánh giá (%{percent})<extra></extra>",
                textposition="inside",
                insidetextorientation="horizontal",
            )
        ]
    )

    fig = apply_chart_theme(fig, title=title, height=height, show_legend=True)
    fig.update_layout(showlegend=True)
    return fig

