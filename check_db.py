import sqlite3
import os

db_path = "/app/data/demo.db"
print(f"Checking database at {db_path}")

if not os.path.exists(db_path):
    print("Database file does not exist!")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("\nTables:")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
for table in tables:
    print(f"- {table[0]}")

if ('node_states',) in tables:
    print("\nContent of node_states:")
    cursor.execute("SELECT * FROM node_states")
    rows = cursor.fetchall()
    for row in rows:
        print(row)
else:
    print("\nTable node_states NOT FOUND!")

conn.close()
