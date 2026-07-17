import sqlite3

conn = sqlite3.connect("profice.db")
cursor = conn.cursor()

# Drop existing tables to refresh database clean on re-run
cursor.execute("DROP TABLE IF EXISTS feedback")
cursor.execute("DROP TABLE IF EXISTS trainers")

cursor.execute("""
CREATE TABLE trainers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    attendance INTEGER
)
""")

cursor.execute("""
CREATE TABLE feedback (
    id INTEGER PRIMARY KEY,
    trainer_id INTEGER,
    student_name TEXT,
    feedback_text TEXT,
    rating INTEGER,
    created_at TEXT,
    FOREIGN KEY (trainer_id) REFERENCES trainers(id)
)
""")

cursor.executemany("INSERT INTO trainers VALUES (?,?,?,?)", [
    (1, "Akash K", "Full Stack", 95),
    (2, "Ashwin", "Commerce", 98),
    (3, "Akash L", "Java", 88),
    (4, "Priya S", "Python", 92),
    (5, "Vikram R", "Data Science", 90)
])

cursor.executemany("INSERT INTO feedback VALUES (?,?,?,?,?,?)", [
    (1, 1, "Student A", "Excellent explanation of concepts", 5, "2026-07-15"),
    (2, 1, "Student B", "Could improve pacing", 3, "2026-06-10"),
    (3, 2, "Student C", "Very engaging sessions", 5, "2026-04-20"),
    (4, 3, "Student D", "Needs better examples", 2, "2025-12-15"),
    (5, 4, "Student E", "Very helpful explanations", 5, "2026-07-10"),
    (6, 4, "Student F", "A bit fast paced", 4, "2026-06-05"),
    (7, 5, "Student G", "Outstanding mentorship!", 5, "2026-07-16"),
    (8, 2, "Student H", "Excellent Commerce content", 5, "2026-01-10"),
    (9, 1, "Student I", "Great Full Stack support", 5, "2026-07-02"),
    (10, 1, "Student J", "Very poor organization, class started late and slides were messy.", 1, "2026-07-14"),
    (11, 4, "Student K", "The trainer was not prepared and could not answer basic questions about loops.", 2, "2026-07-08"),
    (12, 2, "Student L", "Ashwin was completely unhelpful and ignored student chat queries.", 1, "2026-06-25"),
    (13, 5, "Student M", "Explanation was confusing and structured poorly.", 2, "2026-01-20")
])

conn.commit()
conn.close()
print("Database created with updated schema and complete dataset.")