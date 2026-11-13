from io import BytesIO
from pathlib import Path

from test_support import reset_test_database

import app as app_module
from models import FotoVehiculo, GastoVehiculo, Usuario, Vehiculo


def _admin_client():
    reset_test_database()
    with app_module.app.app_context():
        admin = Usuario(
            username='admin_fotos', password='x', rol='admin',
            nombre_completo='Administrador'
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
        admin_id = admin.id

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['rol'] = 'admin'
    return client


def _vehicle_form():
    return {
        'dominio': 'ABC123',
        'tipo': 'Auto',
        'marca': 'Ford',
        'version': 'Focus',
        'año': '2020',
        'km': '50000',
        'color': 'Azul',
        'chasis': '',
        'motor': '',
        'compra': '10000',
        'venta': '15000',
    }


def test_alta_de_vehiculo_guarda_y_muestra_fotos(monkeypatch, tmp_path):
    client = _admin_client()
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path))
    imagen_png = b'\x89PNG\r\n\x1a\n' + b'contenido-de-prueba'

    respuesta = client.post('/vehiculos', data={
        **_vehicle_form(),
        'fotos': (BytesIO(imagen_png), 'unidad.png'),
    })

    assert respuesta.status_code == 302
    with app_module.app.app_context():
        vehiculo = Vehiculo.query.one()
        foto = FotoVehiculo.query.one()
        vehiculo_id = vehiculo.id
        assert foto.vehiculo_id == vehiculo_id
        assert (Path(tmp_path) / 'vehiculo_fotos' / foto.nombre_archivo).read_bytes() == imagen_png
        nombre_archivo = foto.nombre_archivo

    galeria = client.get(f'/fotos?vehiculo_id={vehiculo_id}')
    assert galeria.status_code == 200
    assert b'Galer' in galeria.data
    assert nombre_archivo.encode() in galeria.data

    imagen = client.get(f'/vehiculos/{vehiculo_id}/fotos/{nombre_archivo}')
    assert imagen.status_code == 200
    assert imagen.data == imagen_png


def test_listado_abre_detalle_del_vehiculo_con_acciones_y_fotos():
    client = _admin_client()
    with app_module.app.app_context():
        admin = Usuario.query.filter_by(username='admin_fotos').one()
        vehiculo = Vehiculo(
            interno=1, dominio='ABC123', tipo='Auto', marca='Ford',
            version='Focus', año=2020, km=50000, color='Azul',
            chasis='CH123', motor='M456', precio_compra=1000,
            precio_venta=2000, estado='disponible'
        )
        app_module.db.session.add(vehiculo)
        app_module.db.session.flush()
        app_module.db.session.add_all([
            GastoVehiculo(
                vehiculo_id=vehiculo.id, usuario_id=admin.id,
                tipo='Mantenimiento', monto=500
            ),
            FotoVehiculo(
                vehiculo_id=vehiculo.id, nombre_archivo='unidad.png'
            )
        ])
        app_module.db.session.commit()
        vehiculo_id = vehiculo.id

    respuesta = client.get('/vehiculos')

    assert respuesta.status_code == 200
    assert f'data-vehicle-detail="detalle-vehiculo-{vehiculo_id}"'.encode() in respuesta.data
    assert b'Ver detalle de Ford Focus ABC123' in respuesta.data
    assert b'Editar / agregar fotos' in respuesta.data
    assert b'Administrar fotos' in respuesta.data
    assert b'Eliminar veh\xc3\xadculo' in respuesta.data
    assert b'Ford Focus' in respuesta.data
    assert b'CH123' in respuesta.data
    assert b'$ 500' in respuesta.data
    assert b'unidad.png' in respuesta.data


def test_alta_rechaza_archivos_que_no_son_imagenes(monkeypatch, tmp_path):
    client = _admin_client()
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path))

    respuesta = client.post('/vehiculos', data={
        **_vehicle_form(),
        'fotos': (BytesIO(b'no es una imagen'), 'unidad.jpg'),
    }, follow_redirects=True)

    assert respuesta.status_code == 200
    assert b'Us' in respuesta.data
    with app_module.app.app_context():
        assert Vehiculo.query.count() == 0
        assert FotoVehiculo.query.count() == 0


def test_editar_vehiculo_agrega_fotos_sin_eliminar_las_existentes(monkeypatch, tmp_path):
    client = _admin_client()
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path))
    imagen_original = b'\x89PNG\r\n\x1a\nfoto-original'
    imagen_nueva = b'\x89PNG\r\n\x1a\nfoto-nueva'

    client.post('/vehiculos', data={
        **_vehicle_form(),
        'fotos': (BytesIO(imagen_original), 'original.png'),
    })

    with app_module.app.app_context():
        vehiculo = Vehiculo.query.one()
        vehiculo_id = vehiculo.id
        foto_original = FotoVehiculo.query.one()
        nombre_original = foto_original.nombre_archivo
        ruta_original = Path(tmp_path) / 'vehiculo_fotos' / nombre_original

    formulario = client.get(f'/vehiculos/editar/{vehiculo_id}')
    assert formulario.status_code == 200
    assert b'enctype="multipart/form-data"' in formulario.data
    assert nombre_original.encode() in formulario.data

    respuesta = client.post(f'/vehiculos/editar/{vehiculo_id}', data={
        **_vehicle_form(),
        'fotos': (BytesIO(imagen_nueva), 'nueva.png'),
    })

    assert respuesta.status_code == 302
    assert ruta_original.read_bytes() == imagen_original
    with app_module.app.app_context():
        fotos = FotoVehiculo.query.filter_by(vehiculo_id=vehiculo_id).all()
        assert len(fotos) == 2
        nueva = next(foto for foto in fotos if foto.nombre_archivo != nombre_original)
        assert (Path(tmp_path) / 'vehiculo_fotos' / nueva.nombre_archivo).read_bytes() == imagen_nueva


def test_galeria_muestra_boton_para_copiar_enlace_publico(monkeypatch):
    client = _admin_client()
    monkeypatch.setattr(
        app_module, 'url_catalogo_para_compartir',
        lambda: 'https://catalogo.example/catalogo'
    )

    galeria = client.get('/fotos')

    assert galeria.status_code == 200
    assert b'Copiar link' in galeria.data
    assert b'data-catalog-url="https://catalogo.example/catalogo"' in galeria.data
    assert b'id="catalogLinkFallback"' in galeria.data
    assert b'Enviar por WhatsApp' in galeria.data


def test_vendedor_puede_ver_galeria_y_fotos_pero_no_eliminarlas(monkeypatch, tmp_path):
    reset_test_database()
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path))
    directorio_fotos = Path(tmp_path) / 'vehiculo_fotos'
    directorio_fotos.mkdir()
    imagen_png = b'\x89PNG\r\n\x1a\ncontenido-de-prueba'
    (directorio_fotos / 'unidad.png').write_bytes(imagen_png)

    with app_module.app.app_context():
        vendedor = Usuario(
            username='vendedor_fotos', password='x', rol='vendedor',
            nombre_completo='Vendedor Fotos'
        )
        vehiculo = Vehiculo(
            interno=1, dominio='ABC123', tipo='Auto', marca='Ford',
            version='Focus', año=2020, km=50000, color='Azul',
            precio_compra=10000, precio_venta=15000, estado='disponible'
        )
        app_module.db.session.add_all([vendedor, vehiculo])
        app_module.db.session.flush()
        foto = FotoVehiculo(vehiculo_id=vehiculo.id, nombre_archivo='unidad.png')
        app_module.db.session.add(foto)
        app_module.db.session.commit()
        vendedor_id = vendedor.id
        vehiculo_id = vehiculo.id
        foto_id = foto.id

    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = vendedor_id
        sess['rol'] = 'vendedor'

    galeria = client.get(f'/fotos?vehiculo_id={vehiculo_id}')
    assert galeria.status_code == 200
    assert b'href="/fotos"' in galeria.data
    assert b'Eliminar foto' not in galeria.data

    respuesta_foto = client.get(f'/vehiculos/{vehiculo_id}/fotos/unidad.png')
    assert respuesta_foto.status_code == 200
    assert respuesta_foto.data == imagen_png

    respuesta_eliminar = client.post(
        f'/vehiculos/{vehiculo_id}/fotos/{foto_id}/eliminar'
    )
    assert respuesta_eliminar.status_code == 302
    assert respuesta_eliminar.location.endswith('/dashboard')
    with app_module.app.app_context():
        assert FotoVehiculo.query.filter_by(id=foto_id).count() == 1


def test_catalogo_publico_solo_muestra_disponibles_y_sus_fotos(monkeypatch, tmp_path):
    reset_test_database()
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path))
    directorio_fotos = Path(tmp_path) / 'vehiculo_fotos'
    directorio_fotos.mkdir()

    with app_module.app.app_context():
        disponible = Vehiculo(
            interno=1, dominio='ABC123', tipo='Auto', marca='Ford',
            version='Focus', año=2020, km='50000', color='Azul',
            precio_compra=10000, precio_venta=15000, chasis='CHASIS-PRIVADO',
            estado='disponible'
        )
        vendido = Vehiculo(
            interno=2, dominio='XYZ789', tipo='Auto', marca='Fiat',
            version='Cronos', año=2022, km=20000, color='Gris',
            precio_compra=12000, precio_venta=18000, estado='vendido'
        )
        app_module.db.session.add_all([disponible, vendido])
        app_module.db.session.flush()
        foto_disponible = FotoVehiculo(
            vehiculo_id=disponible.id, nombre_archivo='disponible.png'
        )
        foto_vendida = FotoVehiculo(
            vehiculo_id=vendido.id, nombre_archivo='vendida.png'
        )
        app_module.db.session.add_all([foto_disponible, foto_vendida])
        app_module.db.session.commit()
        disponible_id = disponible.id
        vendido_id = vendido.id

    imagen_png = b'\x89PNG\r\n\x1a\ncontenido-de-prueba'
    (directorio_fotos / 'disponible.png').write_bytes(imagen_png)
    (directorio_fotos / 'vendida.png').write_bytes(imagen_png)
    client = app_module.app.test_client()

    catalogo = client.get('/catalogo')
    assert catalogo.status_code == 200
    assert b'Ford Focus' in catalogo.data
    assert b'$15.000' in catalogo.data
    assert b'50.000 km' in catalogo.data
    assert b'Azul' in catalogo.data
    assert b'Fiat Cronos' not in catalogo.data
    assert b'CHASIS-PRIVADO' not in catalogo.data

    foto_publica = client.get(
        f'/catalogo/unidades/{disponible_id}/fotos/disponible.png'
    )
    assert foto_publica.status_code == 200
    assert foto_publica.data == imagen_png
    foto_no_disponible = client.get(
        f'/catalogo/unidades/{vendido_id}/fotos/vendida.png'
    )
    assert foto_no_disponible.status_code == 404
    assert client.get('/fotos').status_code == 302


def test_eliminar_foto_no_elimina_datos_del_vehiculo_ni_otras_fotos(monkeypatch, tmp_path):
    client = _admin_client()
    monkeypatch.setattr(app_module.app, 'instance_path', str(tmp_path))
    imagen_png = b'\x89PNG\r\n\x1a\n' + b'contenido-de-prueba'

    client.post('/vehiculos', data={
        **_vehicle_form(),
        'fotos': [
            (BytesIO(imagen_png), 'unidad-1.png'),
            (BytesIO(imagen_png + b'-segunda'), 'unidad-2.png'),
        ],
    })

    with app_module.app.app_context():
        vehiculo = Vehiculo.query.one()
        fotos = FotoVehiculo.query.order_by(FotoVehiculo.id).all()
        vehiculo_id = vehiculo.id
        foto_eliminar_id = fotos[0].id
        foto_eliminar_ruta = Path(tmp_path) / 'vehiculo_fotos' / fotos[0].nombre_archivo
        foto_restante_ruta = Path(tmp_path) / 'vehiculo_fotos' / fotos[1].nombre_archivo

    respuesta = client.post(
        f'/vehiculos/{vehiculo_id}/fotos/{foto_eliminar_id}/eliminar',
        follow_redirects=True,
    )

    assert respuesta.status_code == 200
    assert b'La foto se elimin' in respuesta.data
    with app_module.app.app_context():
        vehiculo = Vehiculo.query.get(vehiculo_id)
        assert vehiculo is not None
        assert vehiculo.dominio == 'ABC123'
        assert FotoVehiculo.query.filter_by(vehiculo_id=vehiculo_id).count() == 1
        assert FotoVehiculo.query.filter_by(vehiculo_id=vehiculo_id).one().nombre_archivo == foto_restante_ruta.name
    assert not foto_eliminar_ruta.exists()
    assert foto_restante_ruta.exists()
