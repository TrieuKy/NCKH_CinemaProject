import sys
import argparse
import bcrypt
from sqlalchemy import text
from etl.db import get_engine

def create_user(username, password, role, cinema_key=None):
    engine = get_engine()
    
    # Hash password using bcrypt
    # bcrypt.hashpw returns bytes, we decode to string for db
    salt = bcrypt.gensalt()
    password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO cinema_dw.app_user (username, password_hash, role, cinema_key)
            VALUES (:username, :password_hash, :role, :cinema_key)
            ON CONFLICT (username) DO UPDATE SET
                password_hash = EXCLUDED.password_hash,
                role = EXCLUDED.role,
                cinema_key = EXCLUDED.cinema_key,
                is_active = TRUE;
        """), {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "cinema_key": cinema_key
        })
        
    print(f"User '{username}' ({role}) created/updated successfully.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create an app user")
    parser.add_argument("username", help="Username")
    parser.add_argument("password", help="Password")
    parser.add_argument("--role", choices=["STAFF", "MANAGER"], default="STAFF", help="User role")
    parser.add_argument("--cinema_key", type=int, default=None, help="Cinema Key (NULL for MANAGER)")
    
    args = parser.parse_args()
    create_user(args.username, args.password, args.role, args.cinema_key)
