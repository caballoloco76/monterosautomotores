-- Migración para crear la tabla seguimiento_cliente
CREATE TABLE IF NOT EXISTS seguimiento_cliente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_id INTEGER NOT NULL,
    fecha DATETIME DEFAULT CURRENT_TIMESTAMP,
    descripcion TEXT,
    proxima_gestion VARCHAR(20),
    FOREIGN KEY (cliente_id) REFERENCES cliente(id)
);
