import sqlite3

conn = sqlite3.connect("profice.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS trainers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    trainer_id INTEGER,
    student_name TEXT,
    feedback_text TEXT,
    rating INTEGER,
    FOREIGN KEY (trainer_id) REFERENCES trainers(id)
)
""")

cursor.executemany("INSERT INTO trainers VALUES (?,?,?)", [
    (1, "Akash K", "Full Stack"),
    (2, "Ashwin", "Commerce"),
    (3, "Akash L", "Java")
])

cursor.executemany("INSERT INTO feedback VALUES (?,?,?,?,?)", [
    (1, 1, "Student A", "Excellent explanation of concepts", 5),
    (2, 1, "Student B", "Could improve pacing", 3),
    (3, 2, "Student C", "Very engaging sessions", 5),
    (4, 3, "Student D", "Needs better examples", 2),
])

conn.commit()
conn.close()
print("Database created")