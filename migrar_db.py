"""
Ejecutar UNA SOLA VEZ para agregar las columnas nuevas a la base existente.
Correr con: python migrar_db.py
"""
import sqlite3
import os

# Ajustá el path si tu database.db está en otro lugar
DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'database.db')

# Alternativa: si está en la raíz del proyecto
if not os.path.exists(DB_PATH):
    DB_PATH = 'database.db'

print(f"Conectando a: {DB_PATH}")
conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

migraciones = [
    ("observaciones_senia", "ALTER TABLE venta ADD COLUMN observaciones_senia TEXT"),
]

for nombre_col, sql in migraciones:
    try:
        cur.execute(sql)
        print(f"✅ Columna '{nombre_col}' agregada correctamente.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print(f"⚠️  Columna '{nombre_col}' ya existía, se omite.")
        else:
            print(f"❌ Error en '{nombre_col}': {e}")

conn.commit()
conn.close()
print("\nMigración completada.")
