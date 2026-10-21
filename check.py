import pandas as pd
from sqlalchemy import text
from etl.db import get_engine

engine = get_engine()
with engine.connect() as conn:
    df = pd.read_sql("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='cinema_dw' AND table_name='dim_date'", conn)
    print(df.to_string(index=False))
