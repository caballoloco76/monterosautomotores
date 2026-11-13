from test_support import reset_test_database

import app as app_module
import sqlite3

from database import (
    migrar_importe_final_lavado, migrar_origen_clientes,
    migrar_unidad_vehiculo_lavadero
)


def setup_test_db():
    reset_test_database()
    with app_module.app.app_context():
        usuario = app_module.Usuario(username='tester', password='x', rol='admin', nombre_completo='Tester')
        cliente = app_module.Cliente(interno=1, nombre='Juan', telefono='123', email='a@a.com', domicilio='Calle 1', localidad='Monteros', provincia='Tucumán', tiene_usado='No', usado_detalle='', interes='Compra', descripcion='')
        vehiculo = app_module.Vehiculo(interno=1, dominio='ABC123', tipo='Auto', marca='Ford', version='Focus', año=2020, km=1000, color='Rojo', chasis='1', motor='1', precio_compra=1000, precio_venta=2000, estado='disponible')
        venta = app_module.Venta(
            numero_operacion='1001',
            cliente_id=1,
            vehiculo_id=1,
            vendedor_id=1,
            tipo_operacion='contado',
            precio=2000,
            entrega=0,
            monto_financiado=0,
            banco='',
            toma_usado=False,
            precio_toma=0,
            fecha=app_module.datetime.now(),
            senia=500,
            estado='reserva'
        )
        app_module.db.session.add_all([usuario, cliente, vehiculo, venta])
        app_module.db.session.commit()

        return usuario, cliente, vehiculo, venta


def test_cancelar_venta_actualiza_estado_y_stock():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/ventas/cancelar/1', follow_redirects=False)

    assert response.status_code == 302
    with app_module.app.app_context():
        venta = app_module.Venta.query.get(1)
        vehiculo = app_module.Vehiculo.query.get(1)
        assert venta.estado == 'cancelada'
        assert vehiculo.estado == 'disponible'


def test_gastos_vehiculo_muestra_ganancia_sin_error_float_decimal():
    setup_test_db()
    with app_module.app.app_context():
        gasto = app_module.GastoVehiculo(
            vehiculo_id=1,
            usuario_id=1,
            tipo='Mecánica',
            monto=app_module.Decimal('50.00')
        )
        app_module.db.session.add(gasto)
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.get('/vehiculos/1/gastos')

    assert response.status_code == 200
    assert b'950.00' in response.data


def test_migracion_lavado_copia_importe_de_esquema_anterior():
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE lavado (
            id INTEGER PRIMARY KEY,
            precio NUMERIC(14, 2) NOT NULL,
            descuento_porcentaje INTEGER NOT NULL DEFAULT 0,
            total_cobrado NUMERIC(14, 2)
        )
    """)
    conn.execute(
        "INSERT INTO lavado (precio, descuento_porcentaje, total_cobrado) VALUES (1000, 50, 500)"
    )

    migrar_importe_final_lavado(conn)
    migrar_importe_final_lavado(conn)

    importe = conn.execute("SELECT importe_final FROM lavado").fetchone()[0]
    assert importe == 500
    unidad = conn.execute("SELECT unidad FROM lavado").fetchone()[0]
    assert unidad == 'Auto'
    control_tiempo = conn.execute("SELECT control_tiempo FROM lavado").fetchone()[0]
    assert control_tiempo == 0
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info('lavado')")}
    assert {'descuento_cobro_porcentaje', 'cobrado_en', 'medio_cobro', 'titular_transferencia',
            'app_transferencia', 'movimiento_caja_id'}.issubset(columnas)
    conn.close()


def test_migracion_agrega_datos_comerciales_a_vehiculo_de_lavadero():
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE lavadero_vehiculo (
            id INTEGER PRIMARY KEY,
            patente VARCHAR(10) NOT NULL
        )
    """)

    migrar_unidad_vehiculo_lavadero(conn)
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info('lavadero_vehiculo')")}
    assert {'marca', 'modelo', 'anio', 'color', 'km'}.issubset(columnas)
    conn.close()


def test_migracion_separa_clientes_crm_ya_vinculados_a_lavadero():
    setup_test_db()
    with app_module.app.app_context():
        cliente_crm = app_module.Cliente.query.get(1)
        cliente_crm.dni = '30123456'
        cliente_crm.origen_cliente = 'Referido'
        vehiculo_lavadero = app_module.LavaderoVehiculo(
            patente='ABC123', cliente=cliente_crm
        )
        app_module.db.session.add(vehiculo_lavadero)
        app_module.db.session.commit()

        migrar_origen_clientes()

        cliente_lavadero = app_module.Cliente.query.filter_by(
            origen_registro='lavadero'
        ).one()
        assert cliente_crm.origen_registro == 'crm'
        assert cliente_crm.dni == '30123456'
        assert cliente_lavadero.dni == cliente_crm.dni
        assert cliente_lavadero.id != cliente_crm.id
        assert cliente_lavadero.lavadero_vehiculos[0].id == vehiculo_lavadero.id
        assert app_module.Venta.query.one().cliente_id == cliente_crm.id


def test_lavadero_reutiliza_patente_aplica_descuento_y_agenda_interes():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    for numero in range(1, 6):
        response = client.post('/lavadero', data={
            'patente': 'ab-c123',
            'unidad': 'Auto',
            'tipo_lavado': 'Premium',
            'precio': '1',
            'nombre': 'Cliente Lavadero',
            'dni': '30123456',
            'telefono': '3815555555',
            'interesado_vender': 'si' if numero == 5 else 'no',
            'proximo_contacto': '2026-10-20' if numero == 5 else '',
        })
        assert response.status_code == 302

    with app_module.app.app_context():
        lavados = app_module.Lavado.query.order_by(app_module.Lavado.numero_lavado).all()
        vehiculo = app_module.LavaderoVehiculo.query.one()
        seguimientos = app_module.Seguimiento.query.all()

        assert len(lavados) == 5
        assert vehiculo.patente == 'ABC123'
        assert len({lavado.vehiculo_id for lavado in lavados}) == 1
        assert lavados[3].descuento_porcentaje == 0
        assert lavados[4].numero_lavado == 5
        assert lavados[4].descuento_porcentaje == 50
        assert lavados[4].precio == app_module.Decimal('25000.00')
        assert lavados[4].importe_final == app_module.Decimal('12500.00')
        assert lavados[4].total_cobrado == app_module.Decimal('12500.00')
        assert len(seguimientos) == 1
        assert seguimientos[0].fecha.isoformat() == '2026-10-20'


def test_clientes_lavadero_y_crm_se_guardan_y_muestran_por_separado():
    setup_test_db()
    with app_module.app.app_context():
        cliente_crm = app_module.Cliente.query.get(1)
        cliente_crm.nombre = 'Cliente CRM'
        cliente_crm.dni = '30123456'
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/lavadero', data={
        'patente': 'LAV123',
        'unidad': 'Auto',
        'tipo_lavado': 'Común',
        'nombre': 'Cliente Lavadero',
        'dni': '30123456',
    })
    assert response.status_code == 302

    with app_module.app.app_context():
        clientes_por_origen = {
            cliente.origen_registro: cliente
            for cliente in app_module.Cliente.query.filter(
                app_module.Cliente.dni == '30123456'
            ).all()
        }
        assert set(clientes_por_origen) == {'crm', 'lavadero'}
        assert clientes_por_origen['crm'].nombre == 'Cliente CRM'
        assert clientes_por_origen['lavadero'].nombre == 'Cliente Lavadero'
        cliente_lavadero_id = clientes_por_origen['lavadero'].id

    listado_crm = client.get('/clientes')
    assert b'Cliente CRM' in listado_crm.data
    assert b'Cliente Lavadero' not in listado_crm.data
    assert client.get(f'/clientes/ver/{cliente_lavadero_id}').status_code == 404

    listado_lavadero = client.get('/lavadero?vista=clientes')
    assert b'Cliente Lavadero' in listado_lavadero.data
    assert b'Cliente CRM' not in listado_lavadero.data


def test_lavadero_registra_ingreso_y_hora_de_terminado():
    setup_test_db()
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    formulario = client.get('/lavadero')
    assert b'name="marca_vehiculo"' in formulario.data
    assert b'name="modelo_vehiculo"' in formulario.data
    assert b'name="anio_vehiculo"' in formulario.data
    assert b'name="color_vehiculo"' in formulario.data
    assert b'name="km_vehiculo"' in formulario.data
    assert b'\xc2\xbfTiene usado?' not in formulario.data

    response = client.post('/lavadero', data={
        'patente': 'LAV123',
        'unidad': 'Auto',
        'tipo_lavado': 'Común',
        'nombre': 'Cliente Lavadero',
        'marca_vehiculo': 'Ford',
        'modelo_vehiculo': 'Focus',
        'anio_vehiculo': '2020',
        'color_vehiculo': 'Azul',
        'km_vehiculo': '45000',
        'interesado_vender': 'no',
    }, follow_redirects=False)
    assert response.status_code == 302
    vehiculos_ingresados = client.get('/lavadero')
    assert b'Veh\xc3\xadculos ingresados' in vehiculos_ingresados.data
    assert b'LAV123' in vehiculos_ingresados.data

    with app_module.app.app_context():
        lavado = app_module.Lavado.query.one()
        assert lavado.fecha is not None
        assert lavado.control_tiempo is True
        assert lavado.terminado_en is None
        assert lavado.vehiculo.marca == 'Ford'
        assert lavado.vehiculo.modelo == 'Focus'
        assert lavado.vehiculo.anio == 2020
        assert lavado.vehiculo.color == 'Azul'
        assert lavado.vehiculo.km == 45000
        lavado_id = lavado.id

    ficha_vehiculo = client.get('/lavadero/buscar-patente?patente=LAV123')
    assert ficha_vehiculo.status_code == 200
    assert ficha_vehiculo.json['datos_vehiculo']['modelo'] == 'Focus'
    assert ficha_vehiculo.json['datos_vehiculo']['km'] == 45000

    historial = client.get('/lavadero?vista=historial')
    assert b'Hora de ingreso' in historial.data
    assert b'Duraci\xc3\xb3n:' in historial.data
    assert b'Veh\xc3\xadculo terminado' in historial.data

    response = client.post(f'/lavadero/{lavado_id}/terminar', follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/lavadero?vista=historial')
    with app_module.app.app_context():
        lavado = app_module.Lavado.query.get(lavado_id)
        assert lavado.terminado_en is not None
        assert lavado.terminado_en >= lavado.fecha
    historial_terminado = client.get('/lavadero?vista=historial')
    assert b'Veh\xc3\xadculo terminado' not in historial_terminado.data
    assert b'Terminado' in historial_terminado.data


def test_cobro_lavadero_es_independiente_de_caja_general():
    setup_test_db()
    with app_module.app.app_context():
        caja = app_module.Caja(nombre='Caja principal', estado='cerrada')
        vehiculo = app_module.LavaderoVehiculo(
            patente='COB123', cliente_id=1, unidad='Auto'
        )
        app_module.db.session.add_all([caja, vehiculo])
        app_module.db.session.flush()
        lavados = [
            app_module.Lavado(
                vehiculo=vehiculo, usuario_id=1, numero_lavado=i,
                unidad='Auto', tipo='Común',
                precio=app_module.Decimal('15000.00'),
                importe_final=app_module.Decimal('15000.00'),
                total_cobrado=app_module.Decimal('15000.00'),
            )
            for i in (1, 2, 3)
        ]
        app_module.db.session.add_all(lavados)
        app_module.db.session.commit()
        ids_lavados = [lavado.id for lavado in lavados]

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post(f'/lavadero/{ids_lavados[0]}/cobrar', data={
        'medio_cobro': 'Efectivo',
        'descuento_cobro_porcentaje': '10',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/lavadero')
    with app_module.app.app_context():
        lavado = app_module.db.session.get(app_module.Lavado, ids_lavados[0])
        assert lavado.cobrado_en is not None
        assert lavado.medio_cobro == 'Efectivo'
        assert lavado.descuento_cobro_porcentaje == 10
        assert lavado.importe_final == app_module.Decimal('13500.00')
        assert lavado.total_cobrado == app_module.Decimal('13500.00')
        assert lavado.movimiento_caja_id is None
        assert app_module.MovimientoCaja.query.count() == 0
        assert app_module.Caja.query.one().estado == 'cerrada'

    response = client.post(f'/lavadero/{ids_lavados[1]}/cobrar', data={
        'medio_cobro': 'Transferencia',
        'titular_transferencia': 'María Pérez',
        'app_transferencia': 'Mercado Pago',
    }, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/lavadero')
    with app_module.app.app_context():
        lavado_transferencia = app_module.db.session.get(app_module.Lavado, ids_lavados[1])
        assert lavado_transferencia.cobrado_en is not None
        assert lavado_transferencia.titular_transferencia == 'María Pérez'
        assert lavado_transferencia.app_transferencia == 'Mercado Pago'
        assert lavado_transferencia.descuento_cobro_porcentaje == 0
        assert lavado_transferencia.importe_final == app_module.Decimal('15000.00')

    response = client.post(f'/lavadero/{ids_lavados[2]}/cobrar', data={
        'medio_cobro': 'Efectivo',
        'descuento_cobro_porcentaje': '100',
    }, follow_redirects=False)
    assert response.status_code == 302
    with app_module.app.app_context():
        lavado_sin_cargo = app_module.db.session.get(app_module.Lavado, ids_lavados[2])
        assert lavado_sin_cargo.descuento_cobro_porcentaje == 100
        assert lavado_sin_cargo.importe_final == app_module.Decimal('0.00')

    listado_diario = client.get('/lavadero?vista=ingresos')
    assert b'Control diario de ingresos' in listado_diario.data
    assert b'no afecta la Caja general' in listado_diario.data
    assert b'Mar\xc3\xada P\xc3\xa9rez' in listado_diario.data
    assert b'Mercado Pago' in listado_diario.data
    assert b'COB123' in listado_diario.data
    client.post(f'/lavadero/{ids_lavados[0]}/cobrar', data={
        'medio_cobro': 'Efectivo',
    }, follow_redirects=False)
    with app_module.app.app_context():
        assert app_module.MovimientoCaja.query.count() == 0
        assert app_module.Lavado.query.filter(
            app_module.Lavado.cobrado_en.isnot(None)
        ).count() == 2


def test_reportes_en_pantalla_ingresos_lavadero_respetan_periodos():
    setup_test_db()
    with app_module.app.app_context():
        fechas = [
            ('ANTES', app_module.datetime(2026, 10, 11, 12)),
            ('INICIO', app_module.datetime(2026, 10, 12, 9)),
            ('FIN', app_module.datetime(2026, 10, 18, 17)),
            ('MES', app_module.datetime(2026, 10, 31, 18)),
            ('NOV', app_module.datetime(2026, 11, 1, 8)),
        ]
        for indice, (patente, fecha_cobro) in enumerate(fechas, start=1):
            vehiculo = app_module.LavaderoVehiculo(
                patente=patente, cliente_id=1, unidad='Auto'
            )
            app_module.db.session.add(vehiculo)
            app_module.db.session.flush()
            app_module.db.session.add(app_module.Lavado(
                vehiculo=vehiculo,
                usuario_id=1,
                numero_lavado=1,
                unidad='Auto',
                tipo='Común',
                precio=app_module.Decimal('1000.00'),
                importe_final=app_module.Decimal('1000.00'),
                total_cobrado=app_module.Decimal('1000.00'),
                cobrado_en=fecha_cobro,
                medio_cobro='Efectivo' if indice % 2 else 'Transferencia',
                titular_transferencia='Titular' if indice % 2 == 0 else None,
                app_transferencia='Banco' if indice % 2 == 0 else None,
            ))
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    for periodo, fecha, incluidos, excluidos in (
        ('dia', '2026-10-18', ('FIN',), ('INICIO', 'ANTES', 'NOV')),
        ('semana', '2026-10-15', ('INICIO', 'FIN'), ('ANTES', 'MES', 'NOV')),
        ('mes', '2026-10-15', ('ANTES', 'INICIO', 'FIN', 'MES'), ('NOV',)),
    ):
        response = client.get(
            f'/lavadero/reporte-ingresos?periodo={periodo}&fecha={fecha}'
        )
        assert response.status_code == 200
        assert response.mimetype == 'text/html'
        html = response.get_data(as_text=True)
        assert 'Imprimir' in html
        assert 'window.print()' in html
        for patente in incluidos:
            assert patente in html
        for patente in excluidos:
            assert patente not in html
        assert 'Total cobrado:' in html

def test_lavadero_cliente_existente_registra_sin_repetir_datos():
    setup_test_db()
    with app_module.app.app_context():
        vehiculo_lavadero = app_module.LavaderoVehiculo(
            patente='ABC123', cliente_id=1
        )
        app_module.db.session.add(vehiculo_lavadero)
        app_module.db.session.flush()
        app_module.db.session.add(app_module.Lavado(
            vehiculo=vehiculo_lavadero,
            usuario_id=1,
            numero_lavado=1,
            tipo='Común',
            precio=app_module.Decimal('15000.00'),
            descuento_porcentaje=0,
            total_cobrado=app_module.Decimal('15000.00'),
            importe_final=app_module.Decimal('15000.00'),
        ))
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/lavadero', data={
        'patente': 'abc-123',
        'unidad': 'Auto',
        'tipo_lavado': 'Intermedio',
        'interesado_vender': 'no',
    })

    assert response.status_code == 302
    with app_module.app.app_context():
        assert app_module.Cliente.query.count() == 1
        lavados = app_module.Lavado.query.order_by(app_module.Lavado.numero_lavado).all()
        assert len(lavados) == 2
        assert lavados[1].numero_lavado == 2
        assert lavados[1].precio == app_module.Decimal('18000.00')
        assert lavados[1].descuento_porcentaje == 0


def test_busqueda_patente_lavadero_devuelve_cliente_y_proxima_promocion():
    setup_test_db()
    with app_module.app.app_context():
        vehiculo_lavadero = app_module.LavaderoVehiculo(
            patente='ABC123', cliente_id=1
        )
        app_module.db.session.add(vehiculo_lavadero)
        app_module.db.session.flush()
        for numero in range(1, 5):
            app_module.db.session.add(app_module.Lavado(
                vehiculo_id=vehiculo_lavadero.id,
                usuario_id=1,
                numero_lavado=numero,
                tipo='Común',
                precio=app_module.Decimal('15000.00'),
                descuento_porcentaje=0,
                total_cobrado=app_module.Decimal('15000.00'),
                importe_final=app_module.Decimal('15000.00'),
            ))
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.get('/lavadero/buscar-patente?patente=ab-c123')

    assert response.status_code == 200
    data = response.get_json()
    assert data['existe'] is True
    assert data['cliente']['nombre'] == 'Juan'
    assert data['unidad'] == 'Auto'
    assert data['cantidad_lavados'] == 4
    assert data['proximo_lavado'] == 5
    assert data['aplica_descuento'] is True


def test_promocion_especial_se_registra_y_no_suma_al_contador():
    setup_test_db()
    with app_module.app.app_context():
        vehiculo_lavadero = app_module.LavaderoVehiculo(
            patente='ABC123', cliente_id=1
        )
        promocion = app_module.PromocionLavadero(
            cliente_id=1,
            usuario_id=1,
            porcentaje=20,
            vence_en=app_module.date.today() + app_module.timedelta(days=7),
            estado='preparada',
        )
        app_module.db.session.add_all([vehiculo_lavadero, promocion])
        app_module.db.session.commit()
        promocion_id = promocion.id

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/lavadero', data={
        'patente': 'ABC123',
        'unidad': 'Auto',
        'tipo_lavado': 'Común',
        'promocion_id': str(promocion_id),
        'interesado_vender': 'no',
    })

    assert response.status_code == 302
    with app_module.app.app_context():
        lavado = app_module.Lavado.query.one()
        promocion = app_module.PromocionLavadero.query.get(promocion_id)
        assert lavado.descuento_porcentaje == 20
        assert lavado.importe_final == app_module.Decimal('12000.00')
        assert lavado.cuenta_para_promocion is False
        assert promocion.estado == 'usada'
        assert promocion.lavado_id == lavado.id

    response = client.get('/lavadero/buscar-patente?patente=ABC123')
    assert response.get_json()['cantidad_lavados'] == 0


def test_lavadero_calcula_precio_segun_unidad_y_categoria():
    setup_test_db()
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    casos = [
        ('AAA111', 'Auto', 'Común', None, '15000.00'),
        ('BBB222', 'Auto', 'Intermedio', None, '18000.00'),
        ('CCC333', 'Auto', 'Premium', None, '25000.00'),
        ('DDD444', 'Camioneta', 'Común', None, '20000.00'),
        ('EEE555', 'Camioneta', 'Intermedio', None, '25000.00'),
        ('FFF666', 'Camioneta', 'Premium', None, '30000.00'),
        ('GGG777', 'Moto', None, 'Chica 110 cc', '7000.00'),
        ('HHH888', 'Moto', None, 'Mediana hasta 500 cc', '14000.00'),
        ('JJJ999', 'Moto', None, 'Grande más de 500 cc', '18000.00'),
    ]

    for indice, (patente, unidad, tipo, tipo_moto, _) in enumerate(casos):
        response = client.post('/lavadero', data={
            'patente': patente,
            'unidad': unidad,
            'tipo_lavado': tipo or '',
            'tipo_moto': tipo_moto or '',
            'nombre': f'Cliente {indice}',
            'interesado_vender': 'no',
        })
        assert response.status_code == 302

    with app_module.app.app_context():
        lavados = app_module.Lavado.query.order_by(app_module.Lavado.id).all()
        assert len(lavados) == len(casos)
        for lavado, (patente, unidad, tipo, tipo_moto, precio) in zip(lavados, casos):
            assert lavado.unidad == unidad
            assert lavado.tipo == (tipo or tipo_moto)
            assert lavado.precio == app_module.Decimal(precio)
            vehiculo = app_module.LavaderoVehiculo.query.filter_by(patente=patente).one()
            assert vehiculo.unidad == unidad
            if unidad == 'Moto':
                assert vehiculo.categoria_moto == tipo_moto


def test_vehiculo_existente_usa_unidad_guardada_ignorando_unidad_del_formulario():
    setup_test_db()
    with app_module.app.app_context():
        vehiculo_lavadero = app_module.LavaderoVehiculo(
            patente='ABC123', cliente_id=1, unidad='Camioneta'
        )
        app_module.db.session.add(vehiculo_lavadero)
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/lavadero', data={
        'patente': 'ABC123',
        'unidad': 'Auto',
        'tipo_lavado': 'Común',
        'interesado_vender': 'no',
    })

    assert response.status_code == 302
    with app_module.app.app_context():
        lavado = app_module.Lavado.query.one()
        assert lavado.unidad == 'Camioneta'
        assert lavado.precio == app_module.Decimal('20000.00')


def test_crear_promocion_lavadero_guarda_y_prepara_whatsapp():
    setup_test_db()
    with app_module.app.app_context():
        cliente = app_module.Cliente.query.get(1)
        cliente.telefono = '3815555555'
        app_module.db.session.add(app_module.LavaderoVehiculo(
            patente='ABC123', cliente_id=cliente.id
        ))
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/lavadero/clientes/1/promocion', data={
        'porcentaje': '25',
        'vence_en': (app_module.date.today() + app_module.timedelta(days=7)).isoformat(),
    }, follow_redirects=False)

    assert response.status_code == 302
    assert response.location.startswith('https://wa.me/543815555555')
    with app_module.app.app_context():
        promocion = app_module.PromocionLavadero.query.one()
        assert promocion.porcentaje == 25
        assert promocion.estado == 'preparada'


def test_eliminar_venta_quita_operacion_y_devuelve_stock():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/ventas/eliminar/1', follow_redirects=False)

    assert response.status_code == 302
    with app_module.app.app_context():
        assert app_module.Venta.query.get(1) is None
        vehiculo = app_module.Vehiculo.query.get(1)
        assert vehiculo.estado == 'disponible'


def test_ventas_muestran_proximo_numero_correlativo():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.get('/ventas')

    assert response.status_code == 200
    assert b'1002' in response.data
    assert b'1001' in response.data


def test_clientes_puede_crearse_desde_ventas_con_dni_y_redirect():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/clientes', data={
        'nombre': 'Ana García',
        'dni': '40111222',
        'telefono': '3815551234',
        'email': 'ana@test.com',
        'domicilio': 'San Martín 100',
        'localidad': 'Monteros',
        'provincia': 'Tucumán',
        'usado': 'No',
        'detalle_usado': '',
        'interes': 'Auto',
        'descripcion': 'Cliente nuevo',
        'proximo': '',
        '_redirect': '/ventas'
    }, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'] == '/ventas'

    with app_module.app.app_context():
        nuevo = app_module.Cliente.query.filter_by(dni='40111222').first()
        assert nuevo is not None
        assert nuevo.nombre == 'Ana García'


def test_registrar_boleto_guarda_totales_y_forma_de_pago():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    client.post('/caja/apertura', data={'saldo_inicial': '0'})

    response = client.post('/ventas', data={
        'cliente': '1',
        'vehiculo': '1',
        'precio_vehiculo': '2000',
        'bonificaciones': '100',
        'gestoria': '50',
        'senia': '500',
        'contado': '300',
        'toma_usado': 'on',
        'precio_toma': '200',
        'detalle_toma_usado': 'Ford Ka, 2018, ABC123',
        'financiado': '400',
        'banco': 'Banco Nación',
    }, follow_redirects=False)

    assert response.status_code == 302
    with app_module.app.app_context():
        venta = app_module.Venta.query.filter_by(numero_operacion='1002').one()
        assert venta.total_vehiculo == 1900
        assert venta.total_general == 1950
        assert venta.total_pago == 1400
        assert venta.detalle_toma_usado == 'Ford Ka, 2018, ABC123'
        assert venta.estado == 'reserva'
        pagos = app_module.PagoVenta.query.filter_by(venta_id=venta.id).all()
        assert len(pagos) == 2
        assert sum(pago.monto for pago in pagos) == 800
        movimientos = app_module.MovimientoCaja.query.filter_by(venta_id=venta.id).all()
        assert len(movimientos) == 2
        assert sum(movimiento.monto for movimiento in movimientos) == 800
        assert app_module.total_cobrado(venta) == 800


        vehiculo = app_module.Vehiculo.query.get(1)
        assert vehiculo.estado == 'reservado'

    assert b'ABC123' in client.get('/ventas').data
    for ruta in ('/precios', '/vehiculos'):
        response = client.get(ruta)
        assert b'ABC123' not in response.data


def test_listado_clientes_lavadero_es_pantalla_separada_y_filtra_por_unidad():
    setup_test_db()
    with app_module.app.app_context():
        app_module.Cliente.query.get(1).origen_registro = 'lavadero'
        moto = app_module.LavaderoVehiculo(
            patente='MOTO123', cliente_id=1, unidad='Moto',
            categoria_moto='Chica 110 cc'
        )
        app_module.db.session.add_all([
            app_module.LavaderoVehiculo(patente='AUTO123', cliente_id=1, unidad='Auto'),
            moto,
        ])
        app_module.db.session.flush()
        app_module.db.session.add(app_module.Lavado(
            vehiculo_id=moto.id, usuario_id=1, numero_lavado=1, unidad='Moto',
            tipo='Chica 110 cc', precio=app_module.Decimal('7000.00'),
            importe_final=app_module.Decimal('7000.00'),
            total_cobrado=app_module.Decimal('7000.00'),
            cobrado_en=app_module.datetime.now(), medio_cobro='Efectivo'
        ))
        fecha_anterior = app_module.datetime.now() - app_module.timedelta(days=1)
        app_module.db.session.add(app_module.Lavado(
            vehiculo_id=moto.id, usuario_id=1, numero_lavado=2, unidad='Moto',
            tipo='Chica 110 cc', precio=app_module.Decimal('7000.00'),
            importe_final=app_module.Decimal('7000.00'),
            total_cobrado=app_module.Decimal('7000.00'), fecha=fecha_anterior
        ))
        app_module.db.session.commit()

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    inicio = client.get('/lavadero')
    assert inicio.status_code == 200
    assert b'Mostrar listado de clientes' in inicio.data
    assert b'Control diario de ingresos' in inicio.data
    assert b'id="formReporteIngresos"' in inicio.data
    assert b'Generar reporte' in inicio.data
    reporte_formulario = inicio.data.split(b'id="formReporteIngresos"', 1)[1].split(b'</form>', 1)[0]
    assert b'target="_blank"' not in reporte_formulario
    assert b'Total recaudado' in inicio.data
    assert b'$ 7,000.00' in inicio.data
    assert b'Veh\xc3\xadculos ingresados' in inicio.data
    assert str(fecha_anterior.strftime('%d/%m/%Y %H:%M')).encode() not in inicio.data
    assert b'Historial de lavados' in inicio.data
    assert b'Clientes del lavadero' not in inicio.data
    assert b'id="formulario-nuevo-lavado"' in inicio.data
    assert b'id="control-diario-ingresos"' not in inicio.data
    assert b'id="historial-lavados"' in inicio.data

    listado = client.get('/lavadero?vista=clientes&unidad=Moto')
    assert listado.status_code == 200
    assert b'Nuevo lavado' in listado.data
    assert b'MOTO123' in listado.data
    assert b'AUTO123' not in listado.data
    assert b'id="formulario-nuevo-lavado"' not in listado.data
    assert b'id="control-diario-ingresos"' not in listado.data
    assert b'id="historial-lavados"' not in listado.data

    listado_total = client.get('/lavadero?vista=clientes&unidad=Total')
    assert b'MOTO123' in listado_total.data
    assert b'AUTO123' not in listado_total.data

    ingresos = client.get('/lavadero?vista=ingresos')
    assert b'id="control-diario-ingresos"' in ingresos.data
    assert b'id="formReporteIngresos"' in ingresos.data
    assert b'id="formulario-nuevo-lavado"' not in ingresos.data
    assert b'id="historial-lavados"' not in ingresos.data

    historial = client.get('/lavadero?vista=historial')
    assert b'id="historial-lavados"' in historial.data
    assert fecha_anterior.strftime('%d/%m/%Y %H:%M').encode() in historial.data
    assert b'id="formulario-nuevo-lavado"' not in historial.data
    assert b'id="control-diario-ingresos"' not in historial.data

def test_registrar_venta_con_senia_requiere_caja_abierta():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/ventas', data={
        'cliente': '1',
        'vehiculo': '1',
        'precio_vehiculo': '2000',
        'senia': '500',
    })

    assert response.status_code == 200
    assert 'Debe abrir la caja'.encode('utf-8') in response.data
    with app_module.app.app_context():
        assert app_module.Venta.query.count() == 1
        assert app_module.PagoVenta.query.count() == 0
        assert app_module.MovimientoCaja.query.count() == 0
        assert app_module.Vehiculo.query.get(1).estado == 'disponible'


def test_no_permite_reservar_un_vehiculo_ya_reservado():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    with app_module.app.app_context():
        app_module.Vehiculo.query.get(1).estado = 'reservado'
        app_module.db.session.commit()

    response = client.post('/ventas', data={
        'cliente': '1',
        'vehiculo': '1',
        'precio_vehiculo': '2000',
    })

    assert response.status_code == 200
    assert 'ya no está disponible'.encode('utf-8') in response.data


def test_finalizar_venta_exige_igualdad_con_total_general():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    with app_module.app.app_context():
        venta = app_module.Venta.query.get(1)
        venta.total_general = 2000
        venta.total_pago = 500
        app_module.db.session.commit()

    response = client.post('/ventas/finalizar/1', data={
        'efectivo': '1000',
        'toma_usado': '',
        'precio_toma': '0',
        'financiado': '0',
    }, follow_redirects=False)

    assert response.status_code == 200
    with app_module.app.app_context():
        assert app_module.Venta.query.get(1).estado == 'reserva'


def test_finalizar_venta_registra_en_caja_senia_inicial_legacy():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    client.post('/caja/apertura', data={'saldo_inicial': '0'})
    response = client.post('/ventas/finalizar/1', data={
        'efectivo': '1500',
        'toma_usado': '',
        'precio_toma': '0',
        'financiado': '0',
    }, follow_redirects=False)

    assert response.status_code == 302
    with app_module.app.app_context():
        venta = app_module.Venta.query.get(1)
        movimientos = app_module.MovimientoCaja.query.filter_by(venta_id=venta.id).all()
        assert venta.estado == 'finalizada'
        assert venta.fecha_finalizacion is not None
        assert len(movimientos) == 2
        assert sum(movimiento.monto for movimiento in movimientos) == 2000
        assert {movimiento.categoria for movimiento in movimientos} == {
            'Seña de vehículo', 'Venta de vehículo'
        }
        assert app_module.total_cobrado(venta) == 2000
        assert app_module.saldo_venta(venta) == 0

    informe = client.get('/caja')
    assert informe.status_code == 200
    assert b'Operaciones finalizadas' in informe.data
    assert b'Facturaci\xc3\xb3n del per\xc3\xadodo' in informe.data
    assert b'Ganancia estimada del per\xc3\xadodo' in informe.data
    assert b'2,000.00' in informe.data
    assert b'1,000.00' in informe.data


def test_finalizar_venta_registra_financiacion_inicial_en_caja():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    with app_module.app.app_context():
        venta = app_module.Venta.query.get(1)
        venta.total_general = 2000
        venta.monto_financiado = 500
        app_module.db.session.commit()

    client.post('/caja/apertura', data={'saldo_inicial': '0'})
    response = client.post('/ventas/finalizar/1', data={
        'efectivo': '1000',
        'toma_usado': '',
        'precio_toma': '0',
        'financiado': '0',
    }, follow_redirects=False)

    assert response.status_code == 302
    with app_module.app.app_context():
        venta = app_module.Venta.query.get(1)
        movimientos = app_module.MovimientoCaja.query.filter_by(venta_id=venta.id).all()
        assert venta.estado == 'finalizada'
        assert len(movimientos) == 3
        assert sum(movimiento.monto for movimiento in movimientos) == 2000
        assert sum(
            movimiento.monto for movimiento in movimientos
            if movimiento.categoria == 'Cobro de financiación'
        ) == 500
        assert app_module.saldo_venta(venta) == 0


def test_caja_regulariza_ingresos_historicos_sin_duplicar():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    with app_module.app.app_context():
        venta = app_module.Venta.query.get(1)
        venta.total_general = 2000
        venta.estado = 'finalizada'
        app_module.db.session.commit()

    assert client.get('/caja').status_code == 200
    with app_module.app.app_context():
        movimientos = app_module.MovimientoCaja.query.filter_by(venta_id=1).all()
        pagos = app_module.PagoVenta.query.filter_by(venta_id=1).all()
        assert len(movimientos) == 1
        assert movimientos[0].monto == 500
        assert movimientos[0].categoria == 'Seña de vehículo'
        assert len(pagos) == 1
        assert pagos[0].concepto == 'Seña inicial'

    assert client.get('/caja').status_code == 200
    with app_module.app.app_context():
        assert app_module.MovimientoCaja.query.filter_by(venta_id=1).count() == 1
        assert app_module.PagoVenta.query.filter_by(venta_id=1).count() == 1


def test_cerrar_cliente_como_compra_lo_archiva_y_guarda_vehiculo():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/clientes/atender/1', data={
        'resultado_caso': 'compra',
        'vehiculo_comprado_id': '1',
        'comentario': 'Compra confirmada',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/clientes/ver/1')
    with app_module.app.app_context():
        cliente = app_module.Cliente.query.get(1)
        assert cliente.estado == 'COMPRA'
        assert cliente.vehiculo_comprado_id == 1
        assert cliente.proximo_contacto is None

    gestion = client.get('/clientes')
    assert gestion.status_code == 200
    assert b'Juan' not in gestion.data
    archivados = client.get('/clientes?estado=COMPRA')
    assert archivados.status_code == 200
    assert 'Juan'.encode('utf-8') in archivados.data
    assert b'Ford Focus' in archivados.data
    assert client.get('/clientes/atender/1').headers['Location'].endswith('/clientes/ver/1')


def test_cerrar_cliente_como_compra_exige_vehiculo():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.post('/clientes/atender/1', data={
        'resultado_caso': 'compra',
        'vehiculo_comprado_id': '',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert 'error=vehiculo_requerido' in response.headers['Location']
    with app_module.app.app_context():
        cliente = app_module.Cliente.query.get(1)
        assert cliente.estado != 'COMPRA'
        assert cliente.vehiculo_comprado_id is None


def test_operacion_en_reserva_puede_abrir_modificacion():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.get('/ventas/editar/1')

    assert response.status_code == 200
    assert b'Modificar Operaci' in response.data


def test_agregar_pago_registra_historial_y_recibo():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    client.post('/caja/apertura', data={'saldo_inicial': '0'})

    response = client.post('/ventas/agregar_anticipo/1', data={
        'dinero_adicional': '250',
        'medio': 'transferencia',
    }, follow_redirects=False)

    assert response.status_code == 302
    with app_module.app.app_context():
        pagos = app_module.PagoVenta.query.filter_by(venta_id=1).order_by(app_module.PagoVenta.id).all()
        assert len(pagos) == 1
        assert pagos[-1].monto == 250
        assert pagos[-1].medio == 'transferencia'
        pago_id = pagos[-1].id
        movimiento = app_module.MovimientoCaja.query.filter_by(venta_id=1).one()
        assert movimiento.tipo == 'ingreso'
        assert movimiento.monto == 250

    recibo = client.get(f'/ventas/pago/1/recibo/{pago_id}')
    assert recibo.status_code == 200
    assert b'Recibo de ingreso de dinero' in recibo.data


def test_login_acepta_usuario_espaciado_y_migra_password_plano():
    setup_test_db()
    client = app_module.app.test_client()

    response = client.post('/', data={
        'username': ' tester ',
        'password': 'x',
    }, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')
    with app_module.app.app_context():
        usuario = app_module.Usuario.query.filter_by(username='tester').one()
        assert usuario.password != 'x'
        assert app_module.check_password_hash(usuario.password, 'x')


def test_caja_registra_movimientos_y_cierra_con_diferencia():
    setup_test_db()
    client = app_module.app.test_client()

    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'

    response = client.get('/caja')
    assert response.status_code == 200

    response = client.post('/caja/apertura', data={'saldo_inicial': '1000'})
    assert response.status_code == 302
    response = client.post('/caja/movimiento', data={
        'tipo': 'ingreso', 'categoria': 'Seña de vehículo',
        'concepto': 'Seña operación 1001', 'monto': '500',
        'medio_pago': 'Efectivo', 'cuenta': 'Caja'
    })
    assert response.status_code == 302
    response = client.post('/caja/movimiento', data={
        'tipo': 'egreso', 'categoria': 'Combustible',
        'concepto': 'Carga de combustible', 'monto': '100',
        'medio_pago': 'Efectivo', 'cuenta': 'Caja'
    })
    assert response.status_code == 302

    with app_module.app.app_context():
        caja = app_module.Caja.query.one()
        assert caja.estado == 'abierta'
        assert app_module.MovimientoCaja.query.count() == 2
        assert app_module.resumen_caja(caja)[2] == 1400

    response = client.post('/caja/cierre', data={
        'saldo_real': '1390', 'observaciones': 'Diferencia detectada'
    })
    assert response.status_code == 302
    with app_module.app.app_context():
        assert app_module.Caja.query.one().estado == 'cerrada'
        cierre = app_module.CierreCaja.query.one()
        assert cierre.diferencia == -10


def test_caja_transferencia_no_altera_saldo_general():
    setup_test_db()
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['rol'] = 'admin'
    client.post('/caja/apertura', data={'saldo_inicial': '1000'})
    response = client.post('/caja/transferir', data={
        'origen': 'Caja', 'destino': 'Banco', 'monto': '400',
        'concepto': 'Depósito bancario'
    })
    assert response.status_code == 302
    with app_module.app.app_context():
        movimientos = app_module.MovimientoCaja.query.all()
        assert len(movimientos) == 2
        assert {movimiento.tipo for movimiento in movimientos} == {'ingreso', 'egreso'}
        assert len({movimiento.transferencia_id for movimiento in movimientos}) == 1
        assert app_module.resumen_caja(app_module.Caja.query.one())[2] == 1000
