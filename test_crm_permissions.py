from datetime import date

from test_support import reset_test_database

import app as app_module


def test_vendedor_solo_ve_contactos_que_cargo_y_admin_ve_todos():
    reset_test_database()
    with app_module.app.app_context():
        vendedor = app_module.Usuario(
            username='vendedor_a', password='x', rol='vendedor',
            nombre_completo='Vendedor A'
        )
        otro_vendedor = app_module.Usuario(
            username='vendedor_b', password='x', rol='vendedor',
            nombre_completo='Vendedor B'
        )
        admin = app_module.Usuario(
            username='admin_crm', password='x', rol='admin',
            nombre_completo='Administrador CRM'
        )
        app_module.db.session.add_all([vendedor, otro_vendedor, admin])
        app_module.db.session.flush()
        cliente_propio = app_module.Cliente(
            interno=1, nombre='Contacto Propio', origen_registro='crm',
            vendedor_id=otro_vendedor.id, cargado_por_id=vendedor.id
        )
        cliente_ajeno = app_module.Cliente(
            interno=2, nombre='Contacto Ajeno', origen_registro='crm',
            vendedor_id=vendedor.id, cargado_por_id=otro_vendedor.id
        )
        app_module.db.session.add_all([cliente_propio, cliente_ajeno])
        app_module.db.session.flush()
        app_module.db.session.add_all([
            app_module.Seguimiento(
                cliente_id=cliente_propio.id, vendedor_id=vendedor.id,
                fecha=date.today(), tipo_contacto='Llamada',
                observaciones='Seguimiento propio'
            ),
            app_module.Seguimiento(
                cliente_id=cliente_ajeno.id, vendedor_id=vendedor.id,
                fecha=date.today(), tipo_contacto='Llamada',
                observaciones='Seguimiento ajeno'
            )
        ])
        app_module.db.session.commit()
        cliente_propio_id = cliente_propio.id
        cliente_ajeno_id = cliente_ajeno.id
        vendedor_id = vendedor.id
        admin_id = admin.id

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = vendedor_id
        sess['rol'] = 'vendedor'

    listado = client.get('/clientes')
    assert b'Contacto Propio' in listado.data
    assert b'Contacto Ajeno' not in listado.data

    detalle_propio = client.get(f'/clientes/ver/{cliente_propio_id}')
    assert detalle_propio.status_code == 200
    assert b'Cargado por:' in detalle_propio.data
    assert b'Vendedor A' in detalle_propio.data
    assert client.get(f'/clientes/ver/{cliente_ajeno_id}').status_code == 404
    assert client.get(f'/clientes/editar/{cliente_ajeno_id}').status_code == 404
    assert client.get(f'/clientes/atender/{cliente_ajeno_id}').status_code == 404

    agenda = client.get('/seguimientos')
    assert b'Seguimiento propio' in agenda.data
    assert b'Seguimiento ajeno' not in agenda.data
    respuesta = client.post('/seguimientos', data={
        'cliente_id': cliente_ajeno_id,
        'fecha': date.today().isoformat(),
        'tipo_contacto': 'Llamada'
    })
    assert respuesta.status_code == 302
    with app_module.app.app_context():
        assert app_module.Seguimiento.query.filter_by(
            cliente_id=cliente_ajeno_id
        ).count() == 1

    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['rol'] = 'admin'
    listado_admin = client.get('/clientes')
    assert b'Contacto Propio' in listado_admin.data
    assert b'Contacto Ajeno' in listado_admin.data
    assert client.get(f'/clientes/ver/{cliente_ajeno_id}').status_code == 200


def test_filtro_clientes_conserva_y_separa_casos_cerrados():
    reset_test_database()
    with app_module.app.app_context():
        admin = app_module.Usuario(
            username='admin_filtros', password='x', rol='admin',
            nombre_completo='Administrador'
        )
        app_module.db.session.add(admin)
        app_module.db.session.flush()
        clientes = [
            app_module.Cliente(
                interno=1, nombre='Cliente en gestión', estado='En seguimiento',
                origen_registro='crm', cargado_por_id=admin.id
            ),
            app_module.Cliente(
                interno=2, nombre='Cliente comprador', estado='COMPRA',
                origen_registro='crm', cargado_por_id=admin.id
            ),
            app_module.Cliente(
                interno=3, nombre='Cliente no comprador', estado='NO COMPRA',
                origen_registro='crm', cargado_por_id=admin.id
            )
        ]
        app_module.db.session.add_all(clientes)
        app_module.db.session.commit()
        admin_id = admin.id

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['rol'] = 'admin'

    en_gestion = client.get('/clientes')
    compradores = client.get('/clientes?estado=COMPRA')
    no_compradores = client.get('/clientes?estado=NO%20COMPRA')
    todos = client.get('/clientes?estado=TODOS')

    assert b'Cliente en gesti\xc3\xb3n' in en_gestion.data
    assert b'Cliente comprador' not in en_gestion.data
    assert b'Cliente no comprador' not in en_gestion.data
    assert b'Cliente comprador' in compradores.data
    assert b'Cliente en gesti\xc3\xb3n' not in compradores.data
    assert b'Cliente no comprador' not in compradores.data
    assert b'Cliente no comprador' in no_compradores.data
    assert b'Cliente en gesti\xc3\xb3n' not in no_compradores.data
    assert b'Cliente comprador' not in no_compradores.data
    assert all(
        nombre in todos.data
        for nombre in (
            b'Cliente en gesti\xc3\xb3n',
            b'Cliente comprador',
            b'Cliente no comprador'
        )
    )
    with app_module.app.app_context():
        assert app_module.Cliente.query.filter_by(origen_registro='crm').count() == 3
