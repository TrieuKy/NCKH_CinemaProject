"""
etl/load_facts.py
-----------------
Script ETL nạp dữ liệu vào các bảng fact trong PostgreSQL từ file CSV:
1. Đọc dữ liệu từ staging_ticket.csv và staging_fnb.csv trong data/.
2. Làm sạch: Bỏ qua (drop) các dòng thiếu txn_id.
3. Nghiệp vụ: Gán 'GUEST' cho customer_id bị null/NaN.
4. Nạp dữ liệu vào các bảng fact tương ứng trong PostgreSQL.
5. In số lượng dòng nạp thành công ra terminal để đối soát.
"""

import csv
import io
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
from sqlalchemy import create_engine, text


def psql_insert_copy(table, conn, keys, data_iter):
    """
    Tối ưu tốc độ nạp dữ liệu lớn cho PostgreSQL qua lệnh COPY FROM STDIN.
    """
    dbapi_conn = conn.connection
    with dbapi_conn.cursor() as cur:
        s_buf = io.StringIO()
        writer = csv.writer(s_buf)
        writer.writerows(data_iter)
        s_buf.seek(0)
        columns = ", ".join([f'"{k}"' for k in keys])
        schema_prefix = f'"{table.schema}".' if table.schema else ""
        sql = (
            f'COPY {schema_prefix}"{table.name}" ({columns}) '
            f'FROM STDIN WITH (FORMAT CSV, NULL "")'
        )
        cur.copy_expert(sql=sql, file=s_buf)


def main():
    try:
        # Load cấu hình môi trường từ file .env
        load_dotenv()
        db_user = os.getenv("DB_USER", "postgres")
        db_password = os.getenv("DB_PASSWORD", "")
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "cinema_dw")

        db_url = (
            f"postgresql+psycopg2://{db_user}:{db_password}@"
            f"{db_host}:{db_port}/{db_name}"
        )
        engine = create_engine(db_url)

        base_dir = Path(__file__).resolve().parent.parent
        ticket_csv_path = base_dir / "data" / "staging_ticket.csv"
        fnb_csv_path = base_dir / "data" / "staging_fnb.csv"

        # 1. Đọc dữ liệu từ hai file CSV trong thư mục data/
        print("Đang đọc dữ liệu từ data/staging_ticket.csv và data/staging_fnb.csv...")
        df_ticket = pd.read_csv(ticket_csv_path)
        df_fnb = pd.read_csv(fnb_csv_path)

        # 2. Làm sạch dữ liệu: Bỏ qua các dòng không có mã giao dịch (txn_id)
        df_ticket = df_ticket.dropna(subset=["txn_id"]).copy()
        df_fnb = df_fnb.dropna(subset=["txn_id"]).copy()

        # 3. Xử lý logic nghiệp vụ: customer_id null/NaN gán là 'GUEST'
        df_ticket["customer_id"] = df_ticket["customer_id"].fillna("GUEST")
        df_fnb["customer_id"] = df_fnb["customer_id"].fillna("GUEST")

        # 4. Nạp dữ liệu vào các bảng fact tương ứng trong PostgreSQL
        with engine.connect() as conn:
            target_schema = "cinema_dw"
            cols_query = text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'fact_ticket'"
            )
            cols_ticket = [
                row[0]
                for row in conn.execute(
                    cols_query, {"schema": target_schema}
                ).fetchall()
            ]
            if not cols_ticket:
                target_schema = "public"
                cols_ticket = [
                    row[0]
                    for row in conn.execute(
                        cols_query, {"schema": target_schema}
                    ).fetchall()
                ]

        # Kiểm tra nếu bảng fact theo Star Schema (cần tra cứu surrogate keys)
        if "movie_key" in cols_ticket and "movie_id" not in cols_ticket:
            dim_movie = pd.read_sql(
                f"SELECT movie_key, movie_id FROM {target_schema}.dim_movie",
                engine,
            )
            dim_cinema = pd.read_sql(
                f"SELECT cinema_key, cinema_id FROM {target_schema}.dim_cinema",
                engine,
            )
            dim_room = pd.read_sql(
                f"SELECT room_key, room_id FROM {target_schema}.dim_room",
                engine,
            )
            dim_customer = pd.read_sql(
                f"SELECT customer_key, customer_id FROM {target_schema}.dim_customer",
                engine,
            )
            dim_pricing = pd.read_sql(
                f"SELECT pricing_key, ticket_format, day_type, time_slot, customer_type, "
                f"ROUND(discount_rate::numeric, 4) AS discount_rate FROM {target_schema}.dim_pricing",
                engine,
            )
            dim_channel = pd.read_sql(
                f"SELECT channel_key, channel_code FROM {target_schema}.dim_channel",
                engine,
            )
            dim_product = pd.read_sql(
                f"SELECT product_key, product_id FROM {target_schema}.dim_product",
                engine,
            )

            # Chuẩn bị date_key và time_key cho vé
            df_ticket["date_key"] = (
                pd.to_datetime(df_ticket["show_date"])
                .dt.strftime("%Y%m%d")
                .astype(int)
            )
            show_time_dt = pd.to_datetime(
                df_ticket["show_time"].astype(str), format="%H:%M:%S"
            )
            df_ticket["time_key"] = (
                show_time_dt.dt.hour * 100 + show_time_dt.dt.minute
            )
            df_ticket["discount_rate"] = df_ticket["discount_rate"].round(4)
            df_ticket["net_amount"] = df_ticket["paid_amount"]

            # Merge surrogate keys cho fact_ticket
            df_fact_ticket = (
                df_ticket.merge(dim_movie, on="movie_id", how="inner")
                .merge(dim_cinema, on="cinema_id", how="inner")
                .merge(dim_room, on="room_id", how="inner")
                .merge(dim_customer, on="customer_id", how="inner")
                .merge(
                    dim_pricing,
                    on=[
                        "ticket_format",
                        "day_type",
                        "time_slot",
                        "customer_type",
                        "discount_rate",
                    ],
                    how="inner",
                )
                .merge(dim_channel, on="channel_code", how="inner")
            )

            fact_ticket_cols = [
                "date_key",
                "time_key",
                "movie_key",
                "cinema_key",
                "room_key",
                "customer_key",
                "pricing_key",
                "channel_key",
                "txn_id",
                "qty",
                "list_price",
                "discount_amount",
                "net_amount",
                "is_anomaly",
                "anomaly_type",
            ]
            df_fact_ticket = df_fact_ticket[
                [c for c in fact_ticket_cols if c in df_fact_ticket.columns]
            ]

            # Chuẩn bị date_key và time_key cho F&B
            df_fnb["date_key"] = (
                pd.to_datetime(df_fnb["sale_date"])
                .dt.strftime("%Y%m%d")
                .astype(int)
            )
            sale_time_dt = pd.to_datetime(
                df_fnb["sale_time"].astype(str), format="%H:%M:%S"
            )
            df_fnb["time_key"] = (
                sale_time_dt.dt.hour * 100 + sale_time_dt.dt.minute
            )

            # Merge surrogate keys cho fact_fnb
            df_fact_fnb = (
                df_fnb.merge(dim_cinema, on="cinema_id", how="inner")
                .merge(dim_customer, on="customer_id", how="inner")
                .merge(dim_product, on="product_id", how="inner")
                .merge(dim_channel, on="channel_code", how="inner")
            )

            fact_fnb_cols = [
                "date_key",
                "time_key",
                "cinema_key",
                "customer_key",
                "product_key",
                "channel_key",
                "txn_id",
                "fnb_line_id",
                "qty",
                "unit_price",
                "line_amount",
                "is_anomaly",
                "anomaly_type",
            ]
            df_fact_fnb = df_fact_fnb[
                [c for c in fact_fnb_cols if c in df_fact_fnb.columns]
            ]
        else:
            df_fact_ticket = df_ticket[
                [c for c in cols_ticket if c in df_ticket.columns]
            ]
            df_fact_fnb = df_fnb

        # Xóa dữ liệu cũ trước khi nạp lại để đảm bảo tính toàn vẹn (idempotent)
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"TRUNCATE TABLE {target_schema}.fact_ticket RESTART IDENTITY CASCADE;"
                )
            )
            conn.execute(
                text(
                    f"TRUNCATE TABLE {target_schema}.fact_fnb RESTART IDENTITY CASCADE;"
                )
            )

        # Nạp dữ liệu vào PostgreSQL
        try:
            df_fact_ticket.to_sql(
                "fact_ticket",
                con=engine,
                schema=target_schema,
                if_exists="append",
                index=False,
                method=psql_insert_copy,
            )
            df_fact_fnb.to_sql(
                "fact_fnb",
                con=engine,
                schema=target_schema,
                if_exists="append",
                index=False,
                method=psql_insert_copy,
            )
        except Exception:
            df_fact_ticket.to_sql(
                "fact_ticket",
                con=engine,
                schema=target_schema,
                if_exists="append",
                index=False,
                chunksize=5000,
            )
            df_fact_fnb.to_sql(
                "fact_fnb",
                con=engine,
                schema=target_schema,
                if_exists="append",
                index=False,
                chunksize=5000,
            )

        # 5. In ra terminal số lượng dòng đã nạp thành công để đối soát nhanh
        print(
            f"Số lượng dòng đã nạp thành công vào fact_ticket: {len(df_fact_ticket):,}"
        )
        print(
            f"Số lượng dòng đã nạp thành công vào fact_fnb: {len(df_fact_fnb):,}"
        )

    except Exception as exc:
        print(f"Lỗi trong quá trình nạp dữ liệu: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
