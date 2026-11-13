import sqlite3
import os
from pathlib import Path

from models import (
    db, Cliente, LavaderoVehiculo, NotaCliente, PromocionLavadero,
    Seguimiento, Usuario
)
from datetime import datetime


def migrar_importe_final_lavado(conn):
    tablas = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if 'lavado' not in tablas:
        return

    columnas = {
        row[1] for row in conn.execute("PRAGMA table_info('lavado')").fetchall()
    }
    tenia_importe_final = 'importe_final' in columnas
    tenia_total_cobrado = 'total_cobrado' in columnas
    if 'importe_final' not in columnas:
        conn.execute(
            "ALTER TABLE lavado ADD COLUMN importe_final NUMERIC(14, 2) NOT NULL DEFAULT 0"
        )
        columnas.add('importe_final')

    if 'total_cobrado' not in columnas:
        conn.execute(
            "ALTER TABLE lavado ADD COLUMN total_cobrado NUMERIC(14, 2) NOT NULL DEFAULT 0"
        )
        columnas.add('total_cobrado')

    if not tenia_importe_final and tenia_total_cobrado:
        conn.execute(
            "UPDATE lavado SET importe_final = COALESCE(total_cobrado, 0)"
        )
    elif not tenia_importe_final and {'precio', 'descuento_porcentaje'}.issubset(columnas):
        conn.execute("""
            UPDATE lavado
            SET importe_final = COALESCE(
                precio * (100 - COALESCE(descuento_porcentaje, 0)) / 100,
                0
            )
        """)
    if not tenia_total_cobrado:
        conn.execute(
            "UPDATE lavado SET total_cobrado = COALESCE(importe_final, 0)"
        )

    if 'cuenta_para_promocion' not in columnas:
        conn.execute(
            "ALTER TABLE lavado ADD COLUMN cuenta_para_promocion BOOLEAN NOT NULL DEFAULT 1"
        )
    if 'promocion_especial_id' not in columnas:
        conn.execute(
            "ALTER TABLE lavado ADD COLUMN promocion_especial_id INTEGER"
        )
    if 'unidad' not in columnas:
        conn.execute(
            "ALTER TABLE lavado ADD COLUMN unidad VARCHAR(20) NOT NULL DEFAULT 'Auto'"
        )
        conn.execute("UPDATE lavado SET unidad = 'Auto' WHERE unidad IS NULL OR unidad = ''")
    if 'terminado_en' not in columnas:
        conn.execute("ALTER TABLE lavado ADD COLUMN terminado_en DATETIME")
    if 'control_tiempo' not in columnas:
        conn.execute(
            "ALTER TABLE lavado ADD COLUMN control_tiempo BOOLEAN NOT NULL DEFAULT 0"
        )
    columnas_cobro = {
        'descuento_cobro_porcentaje': 'INTEGER NOT NULL DEFAULT 0',
        'cobrado_en': 'DATETIME',
        'medio_cobro': 'VARCHAR(30)',
        'titular_transferencia': 'VARCHAR(120)',
        'app_transferencia': 'VARCHAR(80)',
        'movimiento_caja_id': 'INTEGER',
    }
    for nombre, tipo in columnas_cobro.items():
        if nombre not in columnas:
            conn.execute(f"ALTER TABLE lavado ADD COLUMN {nombre} {tipo}")


def migrar_unidad_vehiculo_lavadero(conn):
    tablas = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if 'lavadero_vehiculo' not in tablas:
        return

    columnas = {
        row[1] for row in conn.execute(
            "PRAGMA table_info('lavadero_vehiculo')"
        ).fetchall()
    }
    if 'unidad' not in columnas:
        conn.execute("ALTER TABLE lavadero_vehiculo ADD COLUMN unidad VARCHAR(20)")
    if 'categoria_moto' not in columnas:
        conn.execute("ALTER TABLE lavadero_vehiculo ADD COLUMN categoria_moto VARCHAR(40)")
    campos_vehiculo = {
        'marca': 'VARCHAR(50)',
        'modelo': 'VARCHAR(50)',
        'anio': 'INTEGER',
        'color': 'VARCHAR(50)',
        'km': 'INTEGER',
    }
    for nombre, tipo in campos_vehiculo.items():
        if nombre not in columnas:
            conn.execute(f"ALTER TABLE lavadero_vehiculo ADD COLUMN {nombre} {tipo}")

    if 'lavado' in tablas:
        lavado_columnas = {
            row[1] for row in conn.execute("PRAGMA table_info('lavado')").fetchall()
        }
        if {'unidad', 'vehiculo_id'}.issubset(lavado_columnas):
            conn.execute("""
                UPDATE lavadero_vehiculo
                SET unidad = (
                    SELECT lavado.unidad
                    FROM lavado
                    WHERE lavado.vehiculo_id = lavadero_vehiculo.id
                    ORDER BY lavado.id DESC
                    LIMIT 1
                )
                WHERE unidad IS NULL OR unidad = ''
            """)
            if 'tipo' in lavado_columnas:
                conn.execute("""
                    UPDATE lavadero_vehiculo
                    SET categoria_moto = (
                        SELECT lavado.tipo
                        FROM lavado
                        WHERE lavado.vehiculo_id = lavadero_vehiculo.id
                        ORDER BY lavado.id DESC
                        LIMIT 1
                    )
                    WHERE unidad = 'Moto' AND (categoria_moto IS NULL OR categoria_moto = '')
                """)
    conn.execute("""
        UPDATE lavadero_vehiculo
        SET unidad = 'Auto'
        WHERE unidad IS NULL OR unidad = ''
    """)


def migrar_origen_clientes():
    clientes_lavadero = Cliente.query.join(LavaderoVehiculo).distinct().all()
    ultimo_cliente = Cliente.query.order_by(Cliente.interno.desc()).first()
    ultimo_interno = (ultimo_cliente.interno or 0) if ultimo_cliente else 0
    hubo_cambios = False

    for cliente in clientes_lavadero:
        if cliente.origen_cliente == 'Lavadero':
            cliente.origen_registro = 'lavadero'
            continue

        vehiculos_lavadero = list(cliente.lavadero_vehiculos)
        if not vehiculos_lavadero:
            continue

        ultimo_interno += 1
        cliente_lavadero = Cliente(
            interno=ultimo_interno,
            dni=cliente.dni,
            nombre=cliente.nombre,
            telefono=cliente.telefono,
            email=cliente.email,
            domicilio=cliente.domicilio,
            localidad=cliente.localidad,
            provincia=cliente.provincia,
            whatsapp=cliente.whatsapp,
            origen_cliente='Lavadero',
            origen_registro='lavadero',
            estado='Nuevo',
            vendedor_id=cliente.vendedor_id,
            cargado_por_id=cliente.cargado_por_id,
        )
        db.session.add(cliente_lavadero)
        db.session.flush()

        for vehiculo in vehiculos_lavadero:
            vehiculo.cliente = cliente_lavadero

        PromocionLavadero.query.filter_by(cliente_id=cliente.id).update(
            {'cliente_id': cliente_lavadero.id},
            synchronize_session=False
        )
        NotaCliente.query.filter(
            NotaCliente.cliente_id == cliente.id,
            NotaCliente.contenido.startswith('Ingreso al lavadero ·')
        ).update(
            {'cliente_id': cliente_lavadero.id},
            synchronize_session=False
        )
        Seguimiento.query.filter(
            Seguimiento.cliente_id == cliente.id,
            Seguimiento.resultado == 'Interesado en vender vehículo',
            Seguimiento.observaciones.startswith('Interés manifestado durante el lavado de la patente')
        ).update(
            {'cliente_id': cliente_lavadero.id},
            synchronize_session=False
        )
        cliente.origen_registro = 'crm'
        hubo_cambios = True

    if hubo_cambios or clientes_lavadero:
        db.session.commit()


def init_db(app):
    db_path = Path(os.environ.get(
        'MONTEROS_DATABASE_PATH',
        Path(__file__).resolve().parent / 'instance' / 'database.db'
    )).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path.as_posix()}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    with app.app_context():
        db.create_all()

        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            try:
                vehiculo_columns = [row[1] for row in conn.execute("PRAGMA table_info('vehiculo')").fetchall()]
                vehiculo_missing = {
                    'origen': "VARCHAR(20) DEFAULT 'propio'",
                    'titular_nombre_apellido': "VARCHAR(120)",
                    'titular_telefono': "VARCHAR(30)",
                    'titular_localidad': "VARCHAR(100)",
                    'titular_direccion': "VARCHAR(200)",
                }
                for column_name, definition in vehiculo_missing.items():
                    if column_name not in vehiculo_columns:
                        conn.execute(f"ALTER TABLE vehiculo ADD COLUMN {column_name} {definition}")

                cliente_columns = [row[1] for row in conn.execute("PRAGMA table_info('cliente')").fetchall()]
                cliente_missing = {
                    'dni': 'VARCHAR(20)',
                    'apellido': 'VARCHAR(100)',
                    'fecha_nacimiento': 'DATE',
                    'whatsapp': 'VARCHAR(30)',
                    'codigo_postal': 'VARCHAR(12)',
                    'ocupacion': 'VARCHAR(100)',
                    'estado_civil': 'VARCHAR(30)',
                    'vendedor_id': 'INTEGER',
                    'cargado_por_id': 'INTEGER',
                    'origen_cliente': "VARCHAR(40) DEFAULT 'Otro'",
                    'origen_registro': "VARCHAR(20) NOT NULL DEFAULT 'crm'",
                    'vehiculo_interes_id': 'INTEGER',
                    'vehiculo_comprado_id': 'INTEGER',
                    'motivo_no_compra': 'TEXT',
                }
                for column_name, definition in cliente_missing.items():
                    if column_name not in cliente_columns:
                        conn.execute(f"ALTER TABLE cliente ADD COLUMN {column_name} {definition}")

                conn.execute("""
                    UPDATE cliente
                    SET cargado_por_id = vendedor_id
                    WHERE cargado_por_id IS NULL AND vendedor_id IS NOT NULL
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_cliente_cargado_por_id "
                    "ON cliente (cargado_por_id)"
                )
                conn.execute("""
                    UPDATE cliente
                    SET origen_registro = 'crm'
                    WHERE origen_registro IS NULL OR origen_registro = ''
                """)
                usuario_columns = [row[1] for row in conn.execute("PRAGMA table_info('usuario')").fetchall()]
                if 'telefono' not in usuario_columns:
                    conn.execute("ALTER TABLE usuario ADD COLUMN telefono VARCHAR(30)")

                venta_columns = [row[1] for row in conn.execute("PRAGMA table_info('venta')").fetchall()]
                venta_missing = {
                    'precio_vehiculo': 'FLOAT DEFAULT 0',
                    'bonificaciones': 'FLOAT DEFAULT 0',
                    'total_vehiculo': 'FLOAT DEFAULT 0',
                    'gestoria': 'FLOAT DEFAULT 0',
                    'total_general': 'FLOAT DEFAULT 0',
                    'detalle_toma_usado': 'VARCHAR(250)',
                    'total_pago': 'FLOAT DEFAULT 0',
                    'fecha_finalizacion': 'DATETIME',
                    'observaciones_senia': 'TEXT',
                }
                for column_name, definition in venta_missing.items():
                    if column_name not in venta_columns:
                        conn.execute(f"ALTER TABLE venta ADD COLUMN {column_name} {definition}")

                # Repara el estado del inventario para operaciones existentes.
                conn.execute("""
                    UPDATE vehiculo
                    SET estado = 'reservado'
                    WHERE id IN (
                        SELECT vehiculo_id FROM venta WHERE estado = 'reserva'
                    ) AND estado = 'disponible'
                """)
                conn.execute("""
                    UPDATE vehiculo
                    SET estado = 'vendido'
                    WHERE id IN (
                        SELECT vehiculo_id FROM venta WHERE estado = 'finalizada'
                    ) AND estado IN ('disponible', 'reservado')
                """)

                conn.commit()
            finally:
                conn.close()

        # Crea tablas agregadas por nuevas versiones del sistema en bases existentes.
        # create_all() no modifica tablas ya creadas, pero sí incorpora tablas faltantes.
        db.create_all()

        with sqlite3.connect(str(db_path)) as conn:
            migrar_importe_final_lavado(conn)
            migrar_unidad_vehiculo_lavadero(conn)

        migrar_origen_clientes()

        # Migra las notas del flujo legado al historial fechado e inmutable.
        clientes_con_nota_legada = Cliente.query.filter(Cliente.descripcion.isnot(None), Cliente.descripcion != '').all()
        usuario_migracion = Usuario.query.order_by(Usuario.id.asc()).first()
        for cliente in clientes_con_nota_legada:
            db.session.add(NotaCliente(
                cliente_id=cliente.id,
                usuario_id=cliente.vendedor_id or (usuario_migracion.id if usuario_migracion else None),
                contenido=cliente.descripcion,
                creado_en=cliente.fecha_alta or datetime.utcnow()
            ))
            cliente.descripcion = None
        if clientes_con_nota_legada:
            db.session.commit()
