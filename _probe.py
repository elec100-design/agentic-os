import sqlite3

from app import config, orchestrator

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("select id,title,status from projects order by id desc limit 5").fetchall()
for r in rows:
    print(dict(r))
pid = rows[0]["id"]
tasks = conn.execute("select * from tasks where project_id=? order by seq", (pid,)).fetchall()
print("tasks", len(tasks))
g = orchestrator.layout_graph(tasks, orientation="tb")
print("canvas", g["width"], g["height"])
for n in g["nodes"]:
    print(n["seq"], n["x"], n["y"], n["lines"], n["depends_on"])
for e in g["edges"]:
    print(e["from"], "->", e["to"], e["path"][:60])
