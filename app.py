from flask import Flask, abort, render_template, request, redirect, session, jsonify, make_response, url_for, flash, send_from_directory
from database import init_db
from models import (
    db, Usuario, Vehiculo, FotoVehiculo, Cliente, Venta, PagoVenta,
    Caja, MovimientoCaja, CierreCaja, GastoVehiculo, Seguimiento, NotaCliente,
    Auditoria, LavaderoVehiculo, Lavado, PromocionLavadero
)
try:
    from flask_weasyprint import HTML, render_pdf
except ImportError:
    HTML = None
    render_pdf = None
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from urllib.parse import quote, urlparse
from urllib.request import urlopen
import json
import os
import secrets
import unicodedata
from uuid import uuid4
from pathlib import Path

# ---------------- APP ----------------
app = Flask(__name__)
app.secret_key = os.environ.get('MONTEROS_SECRET_KEY') or secrets.token_hex(32)
if not os.environ.get('MONTEROS_SECRET_KEY'):
    app.logger.warning(
        "MONTEROS_SECRET_KEY is unset; sessions will expire when the app restarts."
    )
app.config['MAX_CONTENT_LENGTH'] = 85 * 1024 * 1024

# ---------------- DB ----------------
init_db(app)

# ---------------- USUARIO ADMIN ----------------
with app.app_context():
    if not Usuario.query.filter_by(username="admin").first():
        admin_password = os.environ.get('MONTEROS_ADMIN_PASSWORD')
        if not admin_password:
            admin_password = secrets.token_urlsafe(24)
            print(f"Contraseña inicial generada para admin: {admin_password}")
        admin = Usuario(
            username="admin",
            password=generate_password_hash(admin_password),
            rol="admin",
            nombre_completo="Administrador"
        )
        db.session.add(admin)
        db.session.commit()

# ---------------- HELPERS ----------------
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/')
        if session.get('rol') != 'admin':
            return redirect('/dashboard')
        return f(*args, **kwargs)
    return decorated


def fotos_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/')
        if session.get('rol') not in ('admin', 'vendedor'):
            return redirect('/dashboard')
        return f(*args, **kwargs)
    return decorated


def caja_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/')
        if session.get('rol') not in ('admin', 'gerente', 'cajero'):
            return redirect('/dashboard')
        return f(*args, **kwargs)
    return decorated


def decimal_form_value(value, default=None):
    try:
        parsed = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        return default
    return parsed.quantize(Decimal('0.01'))


def clientes_crm_visibles():
    consulta = Cliente.query.filter_by(origen_registro='crm')
    if session.get('rol') != 'admin':
        consulta = consulta.filter_by(cargado_por_id=session['user_id'])
    return consulta


def normalizar_patente(value):
    return ''.join(caracter for caracter in (value or '').upper() if caracter.isalnum())


def caja_principal():
    caja = Caja.query.order_by(Caja.id.asc()).first()
    if not caja:
        caja = Caja(nombre='Caja principal')
        db.session.add(caja)
        db.session.commit()
    return caja


def resumen_caja(caja, inicio=None):
    query = MovimientoCaja.query.filter_by(caja_id=caja.id, estado='activo')
    if inicio:
        query = query.filter(MovimientoCaja.fecha >= inicio)
    movimientos = query.all()
    ingresos = sum((m.monto for m in movimientos if m.tipo == 'ingreso'), Decimal('0'))
    egresos = sum((m.monto for m in movimientos if m.tipo == 'egreso'), Decimal('0'))
    return ingresos, egresos, caja.saldo_inicial + ingresos - egresos


def pagos_de_venta(venta):
    """Returns the payment history ordered as it appears on the operation."""
    return venta.pagos.order_by(PagoVenta.fecha.asc(), PagoVenta.id.asc()).all()


def anticipos_iniciales_pendientes(venta):
    pagos = pagos_de_venta(venta)
    anticipos = (
        ('senia', 'Seña inicial', 'Seña de vehículo'),
        ('entrega', 'Pago inicial al contado', 'Venta de vehículo'),
    )
    pendientes = []
    for campo, concepto, categoria in anticipos:
        importe = round(getattr(venta, campo) or 0, 2)
        registrado = round(sum(
            pago.monto or 0 for pago in pagos if pago.concepto == concepto
        ), 2)
        faltante = round(max(importe - registrado, 0), 2)
        if faltante > 0:
            pendientes.append((concepto, categoria, faltante))
    return pendientes


def financiacion_inicial_pendiente(venta):
    pagos = pagos_de_venta(venta)
    financiacion_registrada = sum(
        pago.monto or 0 for pago in pagos
        if pago.concepto in ('Financiación inicial', 'Financiación aprobada')
    )
    return round(max((venta.monto_financiado or 0) - financiacion_registrada, 0), 2)


def total_cobrado(venta):
    pagos = pagos_de_venta(venta)
    pendientes = anticipos_iniciales_pendientes(venta)
    return sum(pago.monto or 0 for pago in pagos) + sum(
        importe for _, _, importe in pendientes
    )


def saldo_venta(venta):
    total = venta.total_general or venta.precio or 0
    creditos = (venta.precio_toma or 0) + financiacion_inicial_pendiente(venta)
    return round(total - total_cobrado(venta) - creditos, 2)


def registrar_auditoria(accion, entidad, entidad_id=None, detalle=None):
    db.session.add(Auditoria(
        usuario_id=session.get('user_id'), accion=accion, entidad=entidad,
        entidad_id=entidad_id, detalle=detalle
    ))


def texto_busqueda(valor):
    texto = unicodedata.normalize('NFKD', valor or '')
    return ''.join(caracter for caracter in texto if not unicodedata.combining(caracter)).lower()

# ---------------- LOGIN ----------------
@app.route('/', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = Usuario.query.filter(
            func.lower(func.trim(Usuario.username)) == username.lower()
        ).first()
        password_valid = False
        if user and user.password:
            try:
                password_valid = check_password_hash(user.password, password)
            except (ValueError, TypeError):
                password_valid = user.password == password
            if not password_valid and user.password == password:
                password_valid = True
                user.password = generate_password_hash(password)
                db.session.commit()
        if user and password_valid:
            session['user_id'] = user.id
            session['rol'] = user.rol
            session['username'] = user.username
            session['nombre'] = user.nombre_completo or user.username
            return redirect('/dashboard')
        error = "Usuario o contraseña incorrectos"
    return render_template('login.html', error=error)

# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
@login_required
def dashboard():
    es_admin = session.get('rol') == 'admin'
    ventas_query = Venta.query if es_admin else Venta.query.filter_by(vendedor_id=session['user_id'])
    clientes_query = clientes_crm_visibles()

    total_vehiculos = Vehiculo.query.count() if es_admin else 0
    disponibles = Vehiculo.query.filter_by(estado="disponible").count() if es_admin else 0
    vendidos = Vehiculo.query.filter_by(estado="vendido").count() if es_admin else ventas_query.filter_by(estado='finalizada').count()

    total_clientes = clientes_query.count()
    total_ventas = ventas_query.count()

    total_facturacion = ventas_query.with_entities(func.sum(Venta.precio)).scalar() or 0
    ticket_promedio = total_facturacion / total_ventas if total_ventas > 0 else 0

    ventas_por_vendedor = db.session.query(
        Usuario.username,
        func.count(Venta.id)
    ).join(Venta, Venta.vendedor_id == Usuario.id)\
     .group_by(Usuario.username).all()
    if not es_admin:
        ventas_por_vendedor = [(session.get('username'), total_ventas)]

    vendedores = [v[0] for v in ventas_por_vendedor]
    ventas_vendedor = [v[1] for v in ventas_por_vendedor]

    # Ventas por mes (últimos 6 meses)
    ventas_mes = ventas_query.with_entities(
        func.strftime('%m/%Y', Venta.fecha),
        func.count(Venta.id),
        func.sum(Venta.precio)
    ).group_by(func.strftime('%m/%Y', Venta.fecha))\
     .order_by(Venta.fecha.desc()).limit(6).all()

    meses = [v[0] for v in reversed(ventas_mes)]
    cant_ventas_mes = [v[1] for v in reversed(ventas_mes)]
    facturacion_mes = [v[2] or 0 for v in reversed(ventas_mes)]

    hoy = str(date.today())
    clientes = clientes_query.all()
    alertas = [c for c in clientes if c.proximo_contacto == hoy]

    # Últimas 5 ventas
    ultimas_ventas = ventas_query.with_entities(Venta, Cliente, Vehiculo, Usuario)\
        .join(Cliente, Venta.cliente_id == Cliente.id)\
        .join(Vehiculo, Venta.vehiculo_id == Vehiculo.id)\
        .join(Usuario, Venta.vendedor_id == Usuario.id)\
        .order_by(Venta.fecha.desc()).limit(5).all()

    return render_template(
        'dashboard.html',
        es_admin=es_admin,
        total_vehiculos=total_vehiculos,
        disponibles=disponibles,
        vendidos=vendidos,
        total_clientes=total_clientes,
        total_ventas=total_ventas,
        total_facturacion=total_facturacion,
        ticket_promedio=ticket_promedio,
        vendedores=vendedores,
        ventas_vendedor=ventas_vendedor,
        meses=meses,
        cant_ventas_mes=cant_ventas_mes,
        facturacion_mes=facturacion_mes,
        alertas=alertas,
        ultimas_ventas=ultimas_ventas
    )

# ---------------- VEHICULOS ----------------
def url_catalogo_para_compartir():
    try:
        with urlopen('http://127.0.0.1:4040/api/tunnels', timeout=0.5) as respuesta:
            tuneles = json.load(respuesta).get('tunnels', [])
    except (OSError, ValueError, json.JSONDecodeError):
        tuneles = []

    for tunel in tuneles:
        destino = urlparse(tunel.get('config', {}).get('addr', ''))
        publico = urlparse(tunel.get('public_url', ''))
        if (
            destino.hostname in ('127.0.0.1', 'localhost', '::1')
            and destino.port == 5000
            and publico.scheme == 'https'
            and publico.netloc
        ):
            return f'{publico.geturl().rstrip("/")}/catalogo'

    host = (urlparse(f'//{request.host}').hostname or '').lower()
    if host not in ('localhost', '127.0.0.1', '::1'):
        return f'https://{request.host}/catalogo'
    return None


@app.route('/catalogo')
def catalogo_publico():
    vehiculos_disponibles = Vehiculo.query.filter_by(estado='disponible')\
        .order_by(Vehiculo.marca, Vehiculo.version, Vehiculo.interno).all()
    return render_template('catalogo_publico.html', vehiculos=vehiculos_disponibles)


@app.route('/catalogo/unidades/<int:vehiculo_id>/fotos/<nombre_archivo>')
def foto_catalogo_publico(vehiculo_id, nombre_archivo):
    foto = FotoVehiculo.query.join(Vehiculo).filter(
        FotoVehiculo.vehiculo_id == vehiculo_id,
        FotoVehiculo.nombre_archivo == nombre_archivo,
        Vehiculo.estado == 'disponible'
    ).first_or_404()
    directorio_fotos = Path(app.instance_path) / 'vehiculo_fotos'
    return send_from_directory(directorio_fotos, foto.nombre_archivo)


@app.route('/vehiculos', methods=['GET', 'POST'])
@admin_required
def vehiculos():
    if request.method == 'POST':
        archivos = [archivo for archivo in request.files.getlist('fotos') if archivo.filename]
        if len(archivos) > 10:
            flash('Podés cargar hasta 10 fotos por vehículo.', 'error')
            return redirect('/vehiculos?formulario=1')

        fotos_validadas = []
        for archivo in archivos:
            contenido = archivo.stream.read(8 * 1024 * 1024 + 1)
            if len(contenido) > 8 * 1024 * 1024:
                flash('Cada foto debe pesar como máximo 8 MB.', 'error')
                return redirect('/vehiculos?formulario=1')
            if contenido.startswith(b'\xff\xd8\xff'):
                extension = 'jpg'
            elif contenido.startswith(b'\x89PNG\r\n\x1a\n'):
                extension = 'png'
            elif contenido[:4] == b'RIFF' and contenido[8:12] == b'WEBP':
                extension = 'webp'
            else:
                flash('Usá imágenes JPG, PNG o WebP para las fotos del vehículo.', 'error')
                return redirect('/vehiculos?formulario=1')
            fotos_validadas.append((extension, contenido))

        ultimo = Vehiculo.query.order_by(Vehiculo.interno.desc()).first()
        nuevo_interno = 1 if not ultimo else ultimo.interno + 1

        v = Vehiculo(
            interno=nuevo_interno,
            dominio=request.form['dominio'].upper(),
            tipo=request.form['tipo'],
            marca=request.form['marca'].upper(),
            version=request.form['version'],
            año=request.form['año'],
            km=request.form['km'],
            color=request.form['color'],
            chasis=request.form['chasis'],
            motor=request.form['motor'],
            precio_compra=float(request.form['compra'] or 0),
            precio_venta=float(request.form['venta'] or 0),
            origen=request.form.get('origen', 'propio'),
            titular_nombre_apellido=request.form.get('titular_nombre_apellido'),
            titular_telefono=request.form.get('titular_telefono'),
            titular_localidad=request.form.get('titular_localidad'),
            titular_direccion=request.form.get('titular_direccion')
        )
        db.session.add(v)
        directorio_fotos = Path(app.instance_path) / 'vehiculo_fotos'
        directorio_fotos.mkdir(parents=True, exist_ok=True)
        archivos_guardados = []
        try:
            db.session.flush()
            for extension, contenido in fotos_validadas:
                nombre_archivo = f'{uuid4().hex}.{extension}'
                ruta_archivo = directorio_fotos / nombre_archivo
                ruta_archivo.write_bytes(contenido)
                archivos_guardados.append(ruta_archivo)
                db.session.add(FotoVehiculo(vehiculo_id=v.id, nombre_archivo=nombre_archivo))
            db.session.commit()
        except OSError:
            db.session.rollback()
            for ruta_archivo in archivos_guardados:
                if ruta_archivo.exists():
                    ruta_archivo.unlink()
            flash('No se pudieron guardar las fotos. Revisá el almacenamiento e intentá nuevamente.', 'error')
            return redirect('/vehiculos?formulario=1')
        return redirect('/vehiculos')

    filtro = request.args.get('filtro', 'disponible')
    if filtro == 'todos':
        lista = Vehiculo.query.order_by(Vehiculo.interno.desc()).all()
    else:
        lista = Vehiculo.query.filter_by(estado=filtro).order_by(Vehiculo.interno.desc()).all()

    ids_vehiculos = [vehiculo.id for vehiculo in lista]
    gastos_por_vehiculo = dict(db.session.query(
        GastoVehiculo.vehiculo_id,
        func.coalesce(func.sum(GastoVehiculo.monto), 0)
    ).filter(
        GastoVehiculo.vehiculo_id.in_(ids_vehiculos)
    ).group_by(GastoVehiculo.vehiculo_id).all())
    ganancia_por_vehiculo = {
        vehiculo.id: (
            Decimal(str(vehiculo.precio_venta or 0))
            - Decimal(str(vehiculo.precio_compra or 0))
            - Decimal(str(gastos_por_vehiculo.get(vehiculo.id, 0)))
        )
        for vehiculo in lista
    }

    return render_template(
        'vehiculos.html', vehiculos=lista, filtro=filtro,
        gastos_por_vehiculo=gastos_por_vehiculo,
        ganancia_por_vehiculo=ganancia_por_vehiculo
    )


@app.route('/fotos')
@fotos_required
def fotos_vehiculos():
    vehiculos = Vehiculo.query.order_by(Vehiculo.marca, Vehiculo.version, Vehiculo.interno).all()
    vehiculo_id = request.args.get('vehiculo_id', type=int)
    vehiculo = Vehiculo.query.get_or_404(vehiculo_id) if vehiculo_id else None
    fotos = vehiculo.fotos if vehiculo else []
    return render_template(
        'fotos.html', vehiculos=vehiculos, vehiculo=vehiculo, fotos=fotos,
        catalogo_url=url_catalogo_para_compartir()
    )


@app.route('/vehiculos/<int:vehiculo_id>/fotos/<nombre_archivo>')
@fotos_required
def ver_foto_vehiculo(vehiculo_id, nombre_archivo):
    foto = FotoVehiculo.query.filter_by(
        vehiculo_id=vehiculo_id, nombre_archivo=nombre_archivo
    ).first_or_404()
    directorio_fotos = Path(app.instance_path) / 'vehiculo_fotos'
    return send_from_directory(directorio_fotos, foto.nombre_archivo)


@app.route('/vehiculos/<int:vehiculo_id>/fotos/<int:foto_id>/eliminar', methods=['POST'])
@admin_required
def eliminar_foto_vehiculo(vehiculo_id, foto_id):
    vehiculo = Vehiculo.query.get_or_404(vehiculo_id)
    foto = FotoVehiculo.query.filter_by(
        id=foto_id, vehiculo_id=vehiculo.id
    ).first_or_404()
    nombre_archivo = foto.nombre_archivo

    db.session.delete(foto)
    db.session.commit()

    ruta_archivo = Path(app.instance_path) / 'vehiculo_fotos' / nombre_archivo
    try:
        if ruta_archivo.exists():
            ruta_archivo.unlink()
    except OSError:
        flash('La foto se quitó de la galería, pero no se pudo borrar el archivo. Revisá el almacenamiento.', 'error')
    else:
        flash('La foto se eliminó correctamente.', 'success')

    return redirect(url_for('fotos_vehiculos', vehiculo_id=vehiculo.id))


@app.route('/vehiculos/editar/<int:id>', methods=['GET', 'POST'])
@admin_required
def editar_vehiculo(id):
    v = Vehiculo.query.get_or_404(id)
    if request.method == 'POST':
        archivos = [archivo for archivo in request.files.getlist('fotos') if archivo.filename]
        if len(v.fotos) + len(archivos) > 10:
            flash('Podés cargar hasta 10 fotos por vehículo.', 'error')
            return redirect(url_for('editar_vehiculo', id=v.id))

        fotos_validadas = []
        for archivo in archivos:
            contenido = archivo.stream.read(8 * 1024 * 1024 + 1)
            if len(contenido) > 8 * 1024 * 1024:
                flash('Cada foto debe pesar como máximo 8 MB.', 'error')
                return redirect(url_for('editar_vehiculo', id=v.id))
            if contenido.startswith(b'\xff\xd8\xff'):
                extension = 'jpg'
            elif contenido.startswith(b'\x89PNG\r\n\x1a\n'):
                extension = 'png'
            elif contenido[:4] == b'RIFF' and contenido[8:12] == b'WEBP':
                extension = 'webp'
            else:
                flash('Usá imágenes JPG, PNG o WebP para las fotos del vehículo.', 'error')
                return redirect(url_for('editar_vehiculo', id=v.id))
            fotos_validadas.append((extension, contenido))

        v.dominio = request.form['dominio'].upper()
        v.tipo = request.form['tipo']
        v.marca = request.form['marca'].upper()
        v.version = request.form['version']
        v.año = request.form['año']
        v.km = request.form['km']
        v.color = request.form['color']
        v.chasis = request.form['chasis']
        v.motor = request.form['motor']
        v.precio_compra = float(request.form['compra'] or 0)
        v.precio_venta = float(request.form['venta'] or 0)
        v.origen = request.form.get('origen', v.origen or 'propio')
        v.titular_nombre_apellido = request.form.get('titular_nombre_apellido') or None
        v.titular_telefono = request.form.get('titular_telefono') or None
        v.titular_localidad = request.form.get('titular_localidad') or None
        v.titular_direccion = request.form.get('titular_direccion') or None
        directorio_fotos = Path(app.instance_path) / 'vehiculo_fotos'
        archivos_guardados = []
        try:
            directorio_fotos.mkdir(parents=True, exist_ok=True)
            for extension, contenido in fotos_validadas:
                nombre_archivo = f'{uuid4().hex}.{extension}'
                ruta_archivo = directorio_fotos / nombre_archivo
                ruta_archivo.write_bytes(contenido)
                archivos_guardados.append(ruta_archivo)
                db.session.add(FotoVehiculo(vehiculo_id=v.id, nombre_archivo=nombre_archivo))
            db.session.commit()
        except (OSError, SQLAlchemyError):
            db.session.rollback()
            for ruta_archivo in archivos_guardados:
                if ruta_archivo.exists():
                    ruta_archivo.unlink()
            flash('No se pudieron guardar los cambios y las fotos. Revisá el almacenamiento e intentá nuevamente.', 'error')
            return redirect(url_for('editar_vehiculo', id=v.id))
        return redirect('/vehiculos')
    return render_template('editar_vehiculo.html', v=v)

@app.route('/vehiculos/eliminar/<int:id>', methods=['POST'])
@admin_required
def eliminar_vehiculo(id):
    v = Vehiculo.query.get_or_404(id)
    nombres_fotos = [foto.nombre_archivo for foto in v.fotos]
    db.session.delete(v)
    db.session.commit()
    directorio_fotos = Path(app.instance_path) / 'vehiculo_fotos'
    for nombre_archivo in nombres_fotos:
        ruta_archivo = directorio_fotos / nombre_archivo
        if ruta_archivo.exists():
            ruta_archivo.unlink()
    return redirect('/vehiculos')


@app.route('/vehiculos/<int:vehiculo_id>/posibles-compradores')
@admin_required
def posibles_compradores(vehiculo_id):
    vehiculo = Vehiculo.query.get_or_404(vehiculo_id)
    termino = texto_busqueda(' '.join(filter(None, [vehiculo.marca, vehiculo.version, vehiculo.tipo])))
    palabras = [palabra for palabra in termino.split() if len(palabra) >= 3]
    candidatos = []
    for cliente in clientes_crm_visibles().order_by(Cliente.nombre.asc()).all():
        interes = texto_busqueda(' '.join(filter(None, [cliente.interes, cliente.descripcion])))
        coincidencias = sum(1 for palabra in palabras if palabra in interes)
        if coincidencias:
            candidatos.append((coincidencias, cliente))
    candidatos.sort(key=lambda item: (-item[0], texto_busqueda(item[1].nombre)))
    return render_template(
        'posibles_compradores.html', vehiculo=vehiculo,
        compradores=[cliente for _, cliente in candidatos],
        palabras_busqueda=palabras
    )

# ---------------- CLIENTES ----------------
@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    if request.method == 'POST':
        if session.get('rol') != 'admin':
            return redirect(url_for('clientes'))
        ultimo = Cliente.query.order_by(Cliente.interno.desc()).first()
        nuevo_interno = 1 if not ultimo else ultimo.interno + 1

        c = Cliente(
            interno=nuevo_interno,
            nombre=request.form['nombre'],
            dni=request.form.get('dni', '').strip(),
            telefono=request.form.get('telefono', '').strip(),
            email=request.form.get('email', '').strip(),
            domicilio=request.form.get('domicilio', '').strip(),
            localidad=request.form.get('localidad', '').strip(),
            provincia=request.form.get('provincia', '').strip(),
            tiene_usado=request.form.get('usado', 'No'),
            usado_detalle=request.form.get('detalle_usado', '').strip(),
            interes=request.form.get('interes', '').strip(),
            origen_cliente=request.form.get('origen_cliente', '').strip() or 'Otro',
            origen_registro='crm',
            descripcion='',
            proximo_contacto=request.form.get('proximo', '').strip(),
            estado=request.form.get('estado', 'Nuevo'),
            vendedor_id=session['user_id'],
            cargado_por_id=session['user_id']
        )
        db.session.add(c)
        db.session.commit()

        descripcion = request.form.get('descripcion', '').strip()
        if descripcion:
            db.session.add(NotaCliente(
                cliente_id=c.id,
                usuario_id=session['user_id'],
                contenido=descripcion,
                creado_en=datetime.now()
            ))
            db.session.commit()

        if request.form.get('_redirect') == '/ventas':
            return redirect('/ventas')
        return redirect('/clientes')

    busqueda = request.args.get('buscar', '').strip()
    consulta_clientes = clientes_crm_visibles()
    if busqueda:
        clientes_lista = consulta_clientes.filter(
            Cliente.nombre.contains(busqueda) |
            Cliente.dni.contains(busqueda) |
            Cliente.telefono.contains(busqueda) |
            Cliente.interes.contains(busqueda)
        ).order_by(Cliente.interno.desc()).all()
    else:
        clientes_lista = consulta_clientes.order_by(Cliente.interno.desc()).all()

    hoy_fecha = date.today()
    hoy = hoy_fecha.isoformat()

    def fecha_contacto(cliente):
        if not cliente.proximo_contacto:
            return None
        try:
            return datetime.strptime(cliente.proximo_contacto[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return None

    segmento = request.args.get('segmento', '').strip()
    estado_filtro = request.args.get('estado', '').strip().upper()
    if estado_filtro == 'COMPRA':
        clientes_lista = [cliente for cliente in clientes_lista if cliente.estado == 'COMPRA']
    elif estado_filtro == 'NO COMPRA':
        clientes_lista = [cliente for cliente in clientes_lista if cliente.estado == 'NO COMPRA']
    elif estado_filtro == 'TODOS':
        pass
    else:
        clientes_lista = [
            cliente for cliente in clientes_lista
            if cliente.estado not in ('COMPRA', 'NO COMPRA')
        ]
    vendedor_id = request.args.get('vendedor_id', type=int)
    sin_asignar = request.args.get('sin_asignar') == '1'
    if vendedor_id:
        clientes_lista = [cliente for cliente in clientes_lista if cliente.vendedor_id == vendedor_id]
    elif sin_asignar:
        clientes_lista = [cliente for cliente in clientes_lista if cliente.vendedor_id is None]
    if segmento == 'atrasados':
        clientes_lista = [cliente for cliente in clientes_lista if fecha_contacto(cliente) and fecha_contacto(cliente) < hoy_fecha]
    elif segmento == 'hoy':
        clientes_lista = [cliente for cliente in clientes_lista if fecha_contacto(cliente) == hoy_fecha]
    elif segmento == 'proximos':
        clientes_lista = [cliente for cliente in clientes_lista if fecha_contacto(cliente) and fecha_contacto(cliente) > hoy_fecha]

    alertas = [c for c in clientes_lista if c.proximo_contacto == hoy]
    clientes_atrasados = []
    clientes_hoy = []
    clientes_proximos = []
    clientes_por_vendedor = {}
    ventas_por_vendedor = {}
    ventas_query = Venta.query if session.get('rol') == 'admin' else Venta.query.filter_by(vendedor_id=session['user_id'])
    usuarios_informe = Usuario.query.all() if session.get('rol') == 'admin' else [Usuario.query.get(session['user_id'])]
    for usuario in usuarios_informe:
        if usuario:
            ventas_por_vendedor[(usuario.id, usuario.nombre_completo or usuario.username)] = {
                'cantidad': 0, 'facturacion': 0, 'finalizadas': 0, 'reservas': 0,
                'username': usuario.username
            }
    for venta in ventas_query.order_by(Venta.fecha.desc()).all():
        usuario_venta = Usuario.query.get(venta.vendedor_id) if venta.vendedor_id else None
        vendedor_nombre = usuario_venta.nombre_completo or usuario_venta.username if usuario_venta else 'Sin asignar'
        vendedor_key = (venta.vendedor_id, vendedor_nombre)
        resumen = ventas_por_vendedor.setdefault(vendedor_key, {
            'cantidad': 0, 'facturacion': 0, 'finalizadas': 0, 'reservas': 0,
            'username': usuario_venta.username if usuario_venta else None
        })
        resumen['cantidad'] += 1
        resumen['facturacion'] += venta.total_general or venta.precio or 0
        if venta.estado == 'finalizada':
            resumen['finalizadas'] += 1
        elif venta.estado == 'reserva':
            resumen['reservas'] += 1
    informe_ventas = {
        'cantidad': sum(resumen['cantidad'] for resumen in ventas_por_vendedor.values()),
        'facturacion': sum(resumen['facturacion'] for resumen in ventas_por_vendedor.values()),
        'finalizadas': sum(resumen['finalizadas'] for resumen in ventas_por_vendedor.values()),
        'reservas': sum(resumen['reservas'] for resumen in ventas_por_vendedor.values())
    }
    for cliente in clientes_crm_visibles().all():
        vendedor_nombre = cliente.vendedor.nombre_completo or cliente.vendedor.username if cliente.vendedor else 'Sin asignar'
        vendedor_key = (cliente.vendedor_id, vendedor_nombre)
        clientes_por_vendedor[vendedor_key] = clientes_por_vendedor.get(vendedor_key, 0) + 1
        contacto = fecha_contacto(cliente)
        if contacto is None:
            continue
        if contacto < hoy_fecha:
            clientes_atrasados.append(cliente)
        elif contacto == hoy_fecha:
            clientes_hoy.append(cliente)
        else:
            clientes_proximos.append(cliente)

    return render_template(
        'clientes.html', clientes=clientes_lista, alertas=alertas,
        total_clientes=clientes_crm_visibles().count(),
        clientes_por_vendedor=sorted(
            [(vendedor_id, vendedor, cantidad) for (vendedor_id, vendedor), cantidad in clientes_por_vendedor.items()],
            key=lambda item: item[1].lower()
        ),
        ventas_por_vendedor=sorted(
            [(vendedor_id, vendedor, resumen) for (vendedor_id, vendedor), resumen in ventas_por_vendedor.items()],
            key=lambda item: item[2]['facturacion'], reverse=True
        ),
        informe_ventas=informe_ventas,
        cantidad_atrasados=len(clientes_atrasados),
        cantidad_hoy=len(clientes_hoy),
        cantidad_proximos=len(clientes_proximos)
    )

@app.route('/clientes-por-vendedor')
@login_required
def informe_clientes_por_vendedor():
    return redirect(url_for('clientes', informe='clientes'))

@app.route('/informes-ventas-usuario')
@login_required
def informe_ventas_por_usuario():
    return redirect(url_for('clientes', informe='ventas'))

@app.route('/clientes/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_cliente(id):
    c = clientes_crm_visibles().filter_by(id=id).first_or_404()
    if request.method == 'POST':
        c.nombre = request.form['nombre']
        c.dni = request.form.get('dni', '').strip()
        c.telefono = request.form['telefono']
        c.email = request.form['email']
        c.domicilio = request.form['domicilio']
        c.localidad = request.form['localidad']
        c.provincia = request.form['provincia']
        c.tiene_usado = request.form['usado']
        c.usado_detalle = request.form['detalle_usado']
        c.interes = request.form['interes']
        c.origen_cliente = request.form.get('origen_cliente', '').strip() or c.origen_cliente
        c.proximo_contacto = request.form['proximo']
        c.estado = request.form.get('estado', c.estado)
        if c.estado == 'NO COMPRA':
            c.motivo_no_compra = request.form.get('motivo_no_compra', '').strip() or c.motivo_no_compra
        if session.get('rol') == 'admin':
            vendedor_id = request.form.get('vendedor_id', '').strip()
            if vendedor_id:
                vendedor = Usuario.query.filter_by(id=vendedor_id, rol='vendedor').first()
                if vendedor:
                    c.vendedor_id = vendedor.id
            else:
                c.vendedor_id = None
        db.session.commit()
        return redirect('/clientes')
    vendedores = Usuario.query.filter_by(rol='vendedor').order_by(Usuario.nombre_completo, Usuario.username).all()
    return render_template('editar_cliente.html', c=c, vendedores=vendedores)

@app.route('/clientes/eliminar/<int:id>', methods=['POST'])
@admin_required
def eliminar_cliente(id):
    c = Cliente.query.filter_by(id=id, origen_registro='crm').first_or_404()
    db.session.delete(c)
    db.session.commit()
    return redirect('/clientes')

@app.route('/clientes/ver/<int:id>')
@login_required
def ver_cliente(id):
    c = clientes_crm_visibles().filter_by(id=id).first_or_404()
    return render_template('ver_cliente.html', c=c)

@app.route('/clientes/atender/<int:id>', methods=['GET', 'POST'])
@login_required
def atender_cliente(id):
    c = clientes_crm_visibles().filter_by(id=id).first_or_404()
    if c.estado == 'COMPRA':
        return redirect(url_for('ver_cliente', id=c.id))
    if request.method == 'POST':
        resultado_caso = request.form.get('resultado_caso', '').strip()
        comentario = request.form.get('comentario', '').strip()
        motivo = request.form.get('motivo_no_compra', '').strip()
        vehiculo_comprado_id = request.form.get('vehiculo_comprado_id', type=int)
        if resultado_caso not in ('', 'compra', 'no_compra'):
            return redirect(url_for('atender_cliente', id=c.id, error='resultado_invalido'))
        if resultado_caso == 'no_compra' and not motivo:
            return redirect(url_for('atender_cliente', id=c.id, error='motivo_requerido'))
        if resultado_caso == 'compra':
            vehiculo = Vehiculo.query.get(vehiculo_comprado_id) if vehiculo_comprado_id else None
            if not vehiculo:
                return redirect(url_for('atender_cliente', id=c.id, error='vehiculo_requerido'))
            c.estado = 'COMPRA'
            c.vehiculo_comprado_id = vehiculo.id
            c.motivo_no_compra = None
            c.proximo_contacto = None
            comentario = (
                f'Caso cerrado como COMPRA. Vehículo: {vehiculo.marca} '
                f'{vehiculo.version} · {vehiculo.dominio}'
                if not comentario else comentario
            )
        elif resultado_caso == 'no_compra':
            c.estado = 'NO COMPRA'
            c.vehiculo_comprado_id = None
            c.motivo_no_compra = motivo
            c.proximo_contacto = None
            comentario = f'Caso cerrado como NO COMPRA. Motivo: {motivo}' if not comentario else comentario
        else:
            c.proximo_contacto = request.form.get('proximo') or None
        if comentario:
            db.session.add(NotaCliente(
                cliente_id=c.id,
                usuario_id=session['user_id'],
                contenido=comentario,
                creado_en=datetime.now()
            ))
        db.session.commit()
        return redirect(f'/clientes/ver/{c.id}')
    notas = NotaCliente.query.filter_by(cliente_id=c.id).order_by(
        NotaCliente.creado_en.desc(), NotaCliente.id.desc()
    ).all()
    vehiculos = Vehiculo.query.order_by(Vehiculo.marca, Vehiculo.version).all()
    return render_template('atender_cliente.html', c=c, notas=notas, vehiculos=vehiculos)


@app.route('/clientes/notas/<int:nota_id>/eliminar', methods=['POST'])
@admin_required
def eliminar_nota_cliente(nota_id):
    nota = NotaCliente.query.get_or_404(nota_id)
    cliente_id = nota.cliente_id
    registrar_auditoria('eliminar', 'nota_cliente', nota.id, nota.cliente.nombre)
    db.session.delete(nota)
    db.session.commit()
    return redirect(url_for('atender_cliente', id=cliente_id))


# ---------------- LAVADERO ----------------
@app.route('/lavadero/buscar-patente')
@login_required
def buscar_patente_lavadero():
    patente = normalizar_patente(request.args.get('patente'))
    if not 5 <= len(patente) <= 10:
        return jsonify(ok=False, error='Ingrese una patente válida.'), 400

    vehiculo_lavadero = LavaderoVehiculo.query.filter_by(patente=patente).first()
    cliente = vehiculo_lavadero.cliente if vehiculo_lavadero else None
    if cliente:
        ultimo_lavado = vehiculo_lavadero.lavados.order_by(Lavado.id.desc()).first()
        unidad = vehiculo_lavadero.unidad or (ultimo_lavado.unidad if ultimo_lavado else 'Auto')
        categoria_moto = vehiculo_lavadero.categoria_moto
        if unidad == 'Moto' and not categoria_moto and ultimo_lavado:
            categoria_moto = ultimo_lavado.tipo
        cantidad_lavados = vehiculo_lavadero.lavados.filter_by(cuenta_para_promocion=True).count()
        proximo_lavado = cantidad_lavados + 1
        promociones = PromocionLavadero.query.filter(
            PromocionLavadero.cliente_id == cliente.id,
            PromocionLavadero.estado == 'preparada',
            PromocionLavadero.vence_en >= date.today()
        ).order_by(PromocionLavadero.vence_en.asc()).all()
        return jsonify(
            ok=True,
            existe=True,
            patente=patente,
            unidad=unidad,
            categoria_moto=categoria_moto,
            cliente={
                'nombre': cliente.nombre,
                'dni': cliente.dni or '',
                'telefono': cliente.telefono or '',
            },
            datos_vehiculo={
                'marca': vehiculo_lavadero.marca or '',
                'modelo': vehiculo_lavadero.modelo or '',
                'anio': vehiculo_lavadero.anio or '',
                'color': vehiculo_lavadero.color or '',
                'km': vehiculo_lavadero.km if vehiculo_lavadero.km is not None else '',
            },
            cantidad_lavados=cantidad_lavados,
            proximo_lavado=proximo_lavado,
            aplica_descuento=proximo_lavado % 5 == 0,
            promociones=[{
                'id': promo.id,
                'porcentaje': promo.porcentaje,
                'vence_en': promo.vence_en.strftime('%d/%m/%Y'),
            } for promo in promociones],
        )

    return jsonify(
        ok=True,
        existe=False,
        patente=patente,
        cliente_pendiente=bool(vehiculo_lavadero),
    )


@app.route('/lavadero', methods=['GET', 'POST'])
@login_required
def lavadero():
    precios_lavado = {
        'Auto': {
            'Común': Decimal('15000.00'),
            'Intermedio': Decimal('18000.00'),
            'Premium': Decimal('25000.00'),
        },
        'Camioneta': {
            'Común': Decimal('20000.00'),
            'Intermedio': Decimal('25000.00'),
            'Premium': Decimal('30000.00'),
        },
        'Moto': {
            'Chica 110 cc': Decimal('7000.00'),
            'Mediana hasta 500 cc': Decimal('14000.00'),
            'Grande más de 500 cc': Decimal('18000.00'),
        },
    }
    tipos_lavado = ('Común', 'Intermedio', 'Premium')
    categorias_moto = ('Chica 110 cc', 'Mediana hasta 500 cc', 'Grande más de 500 cc')

    def mostrar_lavadero(form_data=None, error=None, status=200):
        lavados = Lavado.query.order_by(Lavado.fecha.desc(), Lavado.id.desc()).limit(50).all()
        lavados_ingresados_hoy = Lavado.query.filter(
            func.date(Lavado.fecha) == date.today().isoformat()
        ).order_by(Lavado.fecha.desc(), Lavado.id.desc()).all()
        fecha_cobros_texto = request.args.get('fecha_cobros', date.today().isoformat())
        try:
            fecha_cobros = datetime.strptime(fecha_cobros_texto, '%Y-%m-%d').date()
        except ValueError:
            fecha_cobros = date.today()
        cobros_del_dia = Lavado.query.filter(
            func.date(Lavado.cobrado_en) == fecha_cobros.isoformat()
        ).order_by(Lavado.cobrado_en.desc(), Lavado.id.desc()).all()
        total_cobros_dia = sum((lavado.importe_final for lavado in cobros_del_dia), Decimal('0'))
        total_efectivo_dia = sum(
            (lavado.importe_final for lavado in cobros_del_dia if lavado.medio_cobro == 'Efectivo'),
            Decimal('0')
        )
        total_transferencias_dia = sum(
            (lavado.importe_final for lavado in cobros_del_dia if lavado.medio_cobro == 'Transferencia'),
            Decimal('0')
        )
        ids_proximo_descuento = {
            lavado.id for lavado in lavados
            if lavado.cuenta_para_promocion
            and lavado.vehiculo.lavados.filter(
                Lavado.id <= lavado.id,
                Lavado.cuenta_para_promocion.is_(True)
            ).count() % 5 == 4
        }
        lavados_hoy = len(lavados_ingresados_hoy)
        total_lavados = Lavado.query.count()
        total_recaudado = db.session.query(func.sum(Lavado.importe_final)).filter(
            Lavado.cobrado_en.isnot(None)
        ).scalar() or Decimal('0')
        vista = request.args.get('vista') if request.method == 'GET' else None
        if vista not in ('clientes', 'ingresos', 'historial'):
            vista = 'lavadero'
        unidad_filtro = request.args.get('unidad', 'Total')
        if unidad_filtro not in ('Total', 'Auto', 'Camioneta', 'Moto'):
            unidad_filtro = 'Total'
        consulta_clientes = Cliente.query.filter_by(origen_registro='lavadero').join(
            LavaderoVehiculo
        ).filter(
            LavaderoVehiculo.lavados.any()
        )
        if unidad_filtro != 'Total':
            consulta_clientes = consulta_clientes.filter(LavaderoVehiculo.unidad == unidad_filtro)
        clientes_lavadero = consulta_clientes.distinct().order_by(Cliente.nombre).all()
        vehiculos_por_cliente = {
            cliente.id: [
                vehiculo for vehiculo in cliente.lavadero_vehiculos
                if vehiculo.lavados.count() > 0
                and (unidad_filtro == 'Total' or vehiculo.unidad == unidad_filtro)
            ]
            for cliente in clientes_lavadero
        }
        promociones_activas = {
            promo.cliente_id: promo
            for promo in PromocionLavadero.query.filter(
                PromocionLavadero.estado == 'preparada',
                PromocionLavadero.vence_en >= date.today()
            ).order_by(PromocionLavadero.creada_en.asc()).all()
        }
        return render_template(
            'lavadero.html', lavados=lavados,
            lavados_ingresados_hoy=lavados_ingresados_hoy,
            lavados_hoy=lavados_hoy,
            total_lavados=total_lavados, total_recaudado=total_recaudado,
            tipos_lavado=tipos_lavado,
            categorias_moto=categorias_moto,
            precios_lavado=precios_lavado,
            precios_lavado_json={
                unidad: {tipo: int(precio) for tipo, precio in tipos.items()}
                for unidad, tipos in precios_lavado.items()
            },
            clientes_lavadero=clientes_lavadero,
            promociones_activas=promociones_activas,
            vehiculos_por_cliente=vehiculos_por_cliente,
            vista=vista,
            unidad_filtro=unidad_filtro,
            cobros_del_dia=cobros_del_dia,
            fecha_cobros=fecha_cobros,
            total_cobros_dia=total_cobros_dia,
            total_efectivo_dia=total_efectivo_dia,
            total_transferencias_dia=total_transferencias_dia,
            ids_proximo_descuento=ids_proximo_descuento,
            fecha_promocion_default=(date.today() + timedelta(days=7)).isoformat(),
            today_iso=date.today().isoformat(),
            form_data=form_data or {}, error=error
        ), status

    if request.method == 'POST':
        form_data = request.form
        patente = normalizar_patente(form_data.get('patente'))
        if not 5 <= len(patente) <= 10:
            return mostrar_lavadero(
                form_data, 'Ingrese una patente válida (entre 5 y 10 letras o números).', 400
            )
        lavadero_vehiculo = LavaderoVehiculo.query.filter_by(patente=patente).first()
        cliente = lavadero_vehiculo.cliente if lavadero_vehiculo else None

        tipo = form_data.get('tipo_lavado', '').strip()
        if lavadero_vehiculo:
            ultimo_lavado = lavadero_vehiculo.lavados.order_by(Lavado.id.desc()).first()
            unidad = lavadero_vehiculo.unidad or (
                ultimo_lavado.unidad if ultimo_lavado else form_data.get('unidad') or 'Auto'
            )
            categoria_moto = lavadero_vehiculo.categoria_moto or (
                ultimo_lavado.tipo if unidad == 'Moto' and ultimo_lavado else None
            )
        else:
            unidad = form_data.get('unidad', '').strip()
            categoria_moto = form_data.get('tipo_moto', '').strip() if unidad == 'Moto' else None
        marca_vehiculo = form_data.get('marca_vehiculo', '').strip() or None
        modelo_vehiculo = form_data.get('modelo_vehiculo', '').strip() or None
        color_vehiculo = form_data.get('color_vehiculo', '').strip() or None
        try:
            anio_vehiculo = int(form_data['anio_vehiculo']) if form_data.get('anio_vehiculo', '').strip() else None
            km_vehiculo = int(form_data['km_vehiculo']) if form_data.get('km_vehiculo', '').strip() else None
        except ValueError:
            return mostrar_lavadero(form_data, 'El año y el kilometraje deben ser números enteros.', 400)
        if anio_vehiculo is not None and not 1886 <= anio_vehiculo <= date.today().year + 1:
            return mostrar_lavadero(form_data, 'Ingrese un año válido para el vehículo.', 400)
        if km_vehiculo is not None and km_vehiculo < 0:
            return mostrar_lavadero(form_data, 'El kilometraje no puede ser negativo.', 400)
        interesado_vender = form_data.get('interesado_vender') == 'si'
        proximo_contacto_texto = form_data.get('proximo_contacto', '').strip()
        promocion_id = form_data.get('promocion_id', type=int)

        if unidad not in precios_lavado:
            return mostrar_lavadero(form_data, 'Seleccione un tipo de unidad válido.', 400)
        if unidad == 'Moto' and lavadero_vehiculo:
            categoria_moto = lavadero_vehiculo.categoria_moto or categoria_moto
            if not categoria_moto:
                return mostrar_lavadero(
                    form_data, 'La categoría de esta moto no está registrada; actualice los datos de la unidad.', 400
                )
            tipo = categoria_moto
        elif unidad == 'Moto':
            tipo = categoria_moto
        precio = precios_lavado[unidad].get(tipo)
        if precio is None:
            return mostrar_lavadero(
                form_data, 'Seleccione un servicio válido para el tipo de unidad.', 400
            )

        proximo_contacto = None
        if interesado_vender:
            try:
                proximo_contacto = datetime.strptime(proximo_contacto_texto, '%Y-%m-%d').date()
            except ValueError:
                return mostrar_lavadero(
                    form_data, 'Indique una fecha válida para agendar el contacto comercial.', 400
                )

        if cliente is None:
            datos_cliente = {
                'nombre': form_data.get('nombre', '').strip(),
                'dni': form_data.get('dni', '').strip() or None,
                'telefono': form_data.get('telefono', '').strip() or None,
                'email': form_data.get('email', '').strip() or None,
                'domicilio': form_data.get('domicilio', '').strip() or None,
                'localidad': form_data.get('localidad', '').strip() or None,
                'provincia': form_data.get('provincia', '').strip() or None,
            }
            if not datos_cliente['nombre']:
                return mostrar_lavadero(form_data, 'Ingrese el nombre y apellido del cliente.', 400)

            if cliente is None:
                ultimo_cliente = Cliente.query.order_by(Cliente.interno.desc()).first()
                cliente = Cliente(
                    interno=(ultimo_cliente.interno or 0) + 1 if ultimo_cliente else 1,
                    origen_cliente='Lavadero', origen_registro='lavadero', estado='Nuevo',
                    vendedor_id=session['user_id'], cargado_por_id=session['user_id'],
                    **datos_cliente
                )
                db.session.add(cliente)
                db.session.flush()

        if lavadero_vehiculo is None:
            lavadero_vehiculo = LavaderoVehiculo(
                patente=patente, cliente=cliente, unidad=unidad,
                categoria_moto=categoria_moto if unidad == 'Moto' else None,
                marca=marca_vehiculo, modelo=modelo_vehiculo,
                anio=anio_vehiculo, color=color_vehiculo, km=km_vehiculo
            )
            db.session.add(lavadero_vehiculo)
            db.session.flush()
        elif lavadero_vehiculo.cliente is None:
            lavadero_vehiculo.cliente = cliente
        if lavadero_vehiculo.unidad is None:
            lavadero_vehiculo.unidad = unidad
        if unidad == 'Moto' and not lavadero_vehiculo.categoria_moto:
            lavadero_vehiculo.categoria_moto = categoria_moto or tipo

        numero_lavado = lavadero_vehiculo.lavados.count() + 1
        promocion_especial = None
        if promocion_id:
            promocion_especial = PromocionLavadero.query.filter_by(
                id=promocion_id, cliente_id=cliente.id, estado='preparada'
            ).filter(PromocionLavadero.vence_en >= date.today()).first()
            if promocion_especial is None:
                return mostrar_lavadero(
                    form_data, 'La promoción especial seleccionada no está vigente o no pertenece al cliente.', 400
                )

        lavados_validos = lavadero_vehiculo.lavados.filter_by(cuenta_para_promocion=True).count()
        descuento_porcentaje = (
            promocion_especial.porcentaje if promocion_especial
            else (50 if (lavados_validos + 1) % 5 == 0 else 0)
        )
        importe_final = (precio * Decimal(100 - descuento_porcentaje) / Decimal(100)).quantize(
            Decimal('0.01')
        )
        lavado = Lavado(
            vehiculo=lavadero_vehiculo, usuario_id=session['user_id'],
            numero_lavado=numero_lavado, unidad=unidad, tipo=tipo, precio=precio,
            descuento_porcentaje=descuento_porcentaje, total_cobrado=importe_final,
            importe_final=importe_final,
            cuenta_para_promocion=promocion_especial is None,
            promocion_especial=promocion_especial,
            interesado_vender=interesado_vender,
            fecha=datetime.now(),
            control_tiempo=True
        )
        db.session.add(lavado)
        db.session.flush()
        if promocion_especial:
            promocion_especial.estado = 'usada'
            promocion_especial.lavado_id = lavado.id

        descripcion = form_data.get('descripcion', '').strip()
        if descripcion:
            db.session.add(NotaCliente(
                cliente_id=cliente.id, usuario_id=session['user_id'],
                contenido=f'Ingreso al lavadero · patente {patente}: {descripcion}',
                creado_en=datetime.now()
            ))

        if interesado_vender:
            cliente.proximo_contacto = proximo_contacto.isoformat()
            db.session.add(Seguimiento(
                cliente_id=cliente.id, vendedor_id=session['user_id'],
                fecha=proximo_contacto, tipo_contacto='Llamada',
                resultado='Interesado en vender vehículo',
                observaciones=f'Interés manifestado durante el lavado de la patente {patente}.',
                estado='pendiente'
            ))

        registrar_auditoria(
            'crear', 'lavado', lavado.id,
            f'{patente} · {tipo} · ${importe_final}'
        )
        db.session.commit()
        mensaje = f'Lavado #{numero_lavado} de la patente {patente} registrado.'
        if descuento_porcentaje:
            mensaje += f' Se aplicó el {descuento_porcentaje}% de descuento por promoción.'
        if promocion_especial:
            mensaje += ' Este lavado no suma para la promoción de fidelidad.'
        flash(mensaje, 'success')
        return redirect(url_for('lavadero'))

    return mostrar_lavadero()


@app.route('/lavadero/<int:lavado_id>/terminar', methods=['POST'])
@login_required
def terminar_lavado(lavado_id):
    lavado = Lavado.query.get_or_404(lavado_id)
    if lavado.terminado_en is None:
        lavado.terminado_en = datetime.now()
        registrar_auditoria(
            'actualizar', 'lavado', lavado.id,
            f'{lavado.vehiculo.patente} · lavado terminado'
        )
        db.session.commit()
        flash(f'Lavado de {lavado.vehiculo.patente} marcado como terminado.', 'success')
    else:
        flash('Este lavado ya estaba marcado como terminado.', 'info')
    vista = request.form.get('vista')
    if vista != 'historial':
        vista = None
    return redirect(url_for('lavadero', vista=vista))


@app.route('/lavadero/<int:lavado_id>/cobrar', methods=['POST'])
@login_required
def cobrar_lavado(lavado_id):
    lavado = Lavado.query.get_or_404(lavado_id)
    if lavado.cobrado_en is not None:
        flash('Este lavado ya fue cobrado.', 'info')
        return redirect(url_for('lavadero'))

    medio = request.form.get('medio_cobro', '').strip()
    try:
        descuento_cobro = int(request.form.get('descuento_cobro_porcentaje', '0'))
    except (TypeError, ValueError):
        descuento_cobro = -1
    titular = request.form.get('titular_transferencia', '').strip()
    aplicacion = request.form.get('app_transferencia', '').strip()
    if medio not in ('Efectivo', 'Transferencia'):
        flash('Seleccione contado (efectivo) o transferencia.', 'danger')
        return redirect(url_for('lavadero'))
    if descuento_cobro != 0 and not 10 <= descuento_cobro <= 100 or descuento_cobro % 10 != 0:
        flash('Seleccione un descuento válido entre 10% y 100%.', 'danger')
        return redirect(url_for('lavadero'))
    if medio == 'Transferencia' and (not titular or not aplicacion):
        flash('Para una transferencia indique el titular y la aplicación o banco utilizado.', 'danger')
        return redirect(url_for('lavadero'))

    importe_original = lavado.importe_final
    lavado.descuento_cobro_porcentaje = descuento_cobro
    lavado.importe_final = (
        importe_original * Decimal(100 - descuento_cobro) / Decimal(100)
    ).quantize(Decimal('0.01'))
    lavado.total_cobrado = lavado.importe_final
    fecha_cobro = datetime.now()
    lavado.cobrado_en = fecha_cobro
    lavado.medio_cobro = medio
    lavado.titular_transferencia = titular or None
    lavado.app_transferencia = aplicacion or None
    registrar_auditoria(
        'cobrar', 'lavado', lavado.id,
        f'{lavado.vehiculo.patente} · {medio} · descuento {descuento_cobro}% · ${lavado.importe_final}'
    )
    db.session.commit()
    flash(f'Cobranza de ${lavado.importe_final} registrada en la caja independiente del lavadero.', 'success')
    return redirect(url_for('lavadero'))


@app.route('/lavadero/reporte-ingresos')
@login_required
def reporte_ingresos_lavadero():
    periodo = request.args.get('periodo', 'dia').strip()
    if periodo not in ('dia', 'semana', 'mes'):
        abort(400, description='Seleccione un período válido para el reporte.')
    try:
        fecha_referencia = datetime.strptime(
            request.args.get('fecha', ''), '%Y-%m-%d'
        ).date()
    except ValueError:
        abort(400, description='Seleccione una fecha válida para el reporte.')

    if periodo == 'dia':
        rango_desde = fecha_referencia
        rango_hasta = fecha_referencia
        etiqueta_periodo = 'Reporte diario'
    elif periodo == 'semana':
        rango_desde = fecha_referencia - timedelta(days=fecha_referencia.weekday())
        rango_hasta = rango_desde + timedelta(days=6)
        etiqueta_periodo = 'Reporte semanal'
    else:
        rango_desde = fecha_referencia.replace(day=1)
        primer_dia_mes_siguiente = (
            rango_desde.replace(year=rango_desde.year + 1, month=1)
            if rango_desde.month == 12
            else rango_desde.replace(month=rango_desde.month + 1)
        )
        rango_hasta = primer_dia_mes_siguiente - timedelta(days=1)
        etiqueta_periodo = 'Reporte mensual'

    inicio = datetime.combine(rango_desde, datetime.min.time())
    fin_exclusivo = datetime.combine(rango_hasta + timedelta(days=1), datetime.min.time())
    cobros = Lavado.query.filter(
        Lavado.cobrado_en >= inicio,
        Lavado.cobrado_en < fin_exclusivo
    ).order_by(Lavado.cobrado_en.asc(), Lavado.id.asc()).all()
    total = sum((cobro.importe_final for cobro in cobros), Decimal('0'))
    total_efectivo = sum(
        (cobro.importe_final for cobro in cobros if cobro.medio_cobro == 'Efectivo'),
        Decimal('0')
    )
    total_transferencias = sum(
        (cobro.importe_final for cobro in cobros if cobro.medio_cobro == 'Transferencia'),
        Decimal('0')
    )
    return render_template(
        'reporte_ingresos_lavadero.html',
        cobros=cobros,
        total=total,
        total_efectivo=total_efectivo,
        total_transferencias=total_transferencias,
        rango_desde=rango_desde,
        rango_hasta=rango_hasta,
        etiqueta_periodo=etiqueta_periodo,
        generado_en=datetime.now(),
    )


@app.route('/lavadero/clientes/<int:cliente_id>/promocion', methods=['POST'])
@login_required
def crear_promocion_lavadero(cliente_id):
    cliente = Cliente.query.filter_by(
        id=cliente_id, origen_registro='lavadero'
    ).join(LavaderoVehiculo).first_or_404()
    telefono = ''.join(
        caracter for caracter in (cliente.whatsapp or cliente.telefono or '')
        if caracter.isdigit()
    )
    if telefono.startswith('0'):
        telefono = telefono[1:]
    if not telefono.startswith('54'):
        telefono = f'54{telefono}'
    if len(telefono) < 10:
        flash('El cliente no tiene un teléfono válido para WhatsApp.', 'error')
        return redirect(url_for('lavadero') + '#clientes-lavadero')

    porcentaje = request.form.get('porcentaje', type=int)
    if porcentaje is None or not 1 <= porcentaje <= 90:
        flash('El descuento especial debe estar entre 1% y 90%.', 'error')
        return redirect(url_for('lavadero') + '#clientes-lavadero')

    try:
        vence_en = datetime.strptime(request.form.get('vence_en', ''), '%Y-%m-%d').date()
    except ValueError:
        flash('Indique una fecha de vencimiento válida para la promoción.', 'error')
        return redirect(url_for('lavadero') + '#clientes-lavadero')
    if vence_en < date.today():
        flash('La fecha de vencimiento no puede ser anterior a hoy.', 'error')
        return redirect(url_for('lavadero') + '#clientes-lavadero')

    promocion = PromocionLavadero(
        cliente_id=cliente.id, usuario_id=session['user_id'],
        porcentaje=porcentaje, vence_en=vence_en
    )
    db.session.add(promocion)
    registrar_auditoria(
        'crear', 'promocion_lavadero', cliente.id,
        f'{porcentaje}% hasta {vence_en.isoformat()}'
    )
    db.session.commit()

    mensaje = (
        f'Hola {cliente.nombre}, te invitamos a volver a nuestro lavadero. '
        f'Tenés un {porcentaje}% de descuento especial, válido hasta el '
        f'{vence_en.strftime("%d/%m/%Y")}. ¡Te esperamos!'
    )
    return redirect(f'https://wa.me/{telefono}?text={quote(mensaje)}')


# ---------------- GASTOS DE VEHICULOS ----------------
@app.route('/vehiculos/<int:vehiculo_id>/gastos', methods=['GET', 'POST'])
@admin_required
def gestionar_gastos(vehiculo_id):
    vehiculo = Vehiculo.query.get_or_404(vehiculo_id)
    if request.method == 'POST':
        monto = decimal_form_value(request.form.get('monto'))
        tipo = request.form.get('tipo', '').strip()
        if not tipo or monto is None or monto <= 0:
            return redirect(url_for('gestionar_gastos', vehiculo_id=vehiculo.id))
        gasto = GastoVehiculo(
            vehiculo_id=vehiculo.id, usuario_id=session['user_id'], tipo=tipo,
            monto=monto, descripcion=request.form.get('descripcion', '').strip() or None
        )
        db.session.add(gasto)
        registrar_auditoria('crear', 'gasto_vehiculo', vehiculo.id, f'{tipo}: {monto}')
        db.session.commit()
        return redirect(url_for('gestionar_gastos', vehiculo_id=vehiculo.id))

    gastos = GastoVehiculo.query.filter_by(vehiculo_id=vehiculo.id).order_by(GastoVehiculo.fecha_registro.desc()).all()
    total_gastos = sum((g.monto for g in gastos), Decimal('0'))
    precio_venta = Decimal(str(vehiculo.precio_venta or 0))
    precio_compra = Decimal(str(vehiculo.precio_compra or 0))
    ganancia_real = precio_venta - precio_compra - total_gastos
    return render_template('gestionar_gastos.html', vehiculo=vehiculo, gastos=gastos,
                           total_gastos=total_gastos, ganancia_real=ganancia_real)


@app.route('/vehiculos/<int:vehiculo_id>/gastos/editar/<int:gasto_id>', methods=['GET', 'POST'])
@admin_required
def editar_gasto(vehiculo_id, gasto_id):
    vehiculo = Vehiculo.query.get_or_404(vehiculo_id)
    gasto = GastoVehiculo.query.filter_by(id=gasto_id, vehiculo_id=vehiculo.id).first_or_404()
    if request.method == 'POST':
        monto = decimal_form_value(request.form.get('monto'))
        tipo = request.form.get('tipo', '').strip()
        if tipo and monto is not None and monto > 0:
            gasto.tipo = tipo
            gasto.monto = monto
            gasto.descripcion = request.form.get('descripcion', '').strip() or None
            registrar_auditoria('editar', 'gasto_vehiculo', gasto.id, tipo)
            db.session.commit()
            return redirect(url_for('gestionar_gastos', vehiculo_id=vehiculo.id))
    return render_template('editar_gasto.html', vehiculo=vehiculo, gasto=gasto)


@app.route('/vehiculos/<int:vehiculo_id>/gastos/eliminar/<int:gasto_id>', methods=['POST'])
@admin_required
def eliminar_gasto(vehiculo_id, gasto_id):
    gasto = GastoVehiculo.query.filter_by(id=gasto_id, vehiculo_id=vehiculo_id).first_or_404()
    db.session.delete(gasto)
    registrar_auditoria('eliminar', 'gasto_vehiculo', gasto.id)
    db.session.commit()
    return redirect(url_for('gestionar_gastos', vehiculo_id=vehiculo_id))


# ---------------- AGENDA DE SEGUIMIENTOS ----------------
@app.route('/seguimientos', methods=['GET', 'POST'])
@login_required
def seguimientos():
    if request.method == 'POST':
        if session.get('rol') not in ('admin', 'vendedor'):
            return redirect(url_for('seguimientos'))
        cliente = clientes_crm_visibles().filter_by(
            id=request.form.get('cliente_id')
        ).first()
        if not cliente:
            return redirect(url_for('seguimientos'))
        fecha = request.form.get('fecha') or date.today().isoformat()
        try:
            fecha = datetime.strptime(fecha, '%Y-%m-%d').date()
        except ValueError:
            fecha = date.today()
        proximo = request.form.get('proximo_contacto') or None
        try:
            proximo = datetime.strptime(proximo, '%Y-%m-%d').date() if proximo else None
        except ValueError:
            proximo = None
        seguimiento = Seguimiento(
            cliente_id=cliente.id, vendedor_id=session['user_id'], fecha=fecha,
            hora=datetime.now().time(),
            tipo_contacto=request.form.get('tipo_contacto', 'Otro'),
            resultado=request.form.get('resultado', '').strip() or None,
            proximo_contacto=proximo,
            observaciones=request.form.get('observaciones', '').strip() or None
        )
        db.session.add(seguimiento)
        registrar_auditoria('crear', 'seguimiento', cliente.id)
        db.session.commit()
        return redirect(url_for('seguimientos'))

    query = Seguimiento.query
    query = query.filter(Seguimiento.cliente.has(Cliente.origen_registro == 'crm'))
    if session.get('rol') != 'admin':
        query = query.filter(Seguimiento.cliente.has(
            Cliente.cargado_por_id == session['user_id']
        ))
    agenda = query.order_by(Seguimiento.fecha.asc(), Seguimiento.id.asc()).all()
    clientes_lista = clientes_crm_visibles().order_by(Cliente.nombre.asc()).all()
    hoy = date.today()
    return render_template(
        'seguimientos.html', seguimientos=agenda, clientes=clientes_lista,
        hoy=hoy, seguimientos_hoy=[s for s in agenda if s.fecha == hoy],
        seguimientos_atrasados=[s for s in agenda if s.fecha < hoy and s.estado == 'pendiente'],
        seguimientos_proximos=[s for s in agenda if s.fecha > hoy]
    )


@app.route('/seguimientos/<int:seguimiento_id>/eliminar', methods=['POST'])
@admin_required
def eliminar_seguimiento(seguimiento_id):
    seguimiento = Seguimiento.query.get_or_404(seguimiento_id)
    registrar_auditoria('eliminar', 'seguimiento', seguimiento.id, seguimiento.cliente.nombre)
    db.session.delete(seguimiento)
    db.session.commit()
    return redirect(url_for('seguimientos'))

# ---------------- VENTAS ----------------
@app.route('/ventas', methods=['GET', 'POST'])
@admin_required
def ventas():
    clientes_lista = clientes_crm_visibles().order_by(Cliente.nombre).all()
    vehiculos_lista = Vehiculo.query.filter_by(estado="disponible").all()
    ventas_lista = db.session.query(Venta, Cliente, Vehiculo, Usuario)\
        .join(Cliente, Venta.cliente_id == Cliente.id)\
        .join(Vehiculo, Venta.vehiculo_id == Vehiculo.id)\
        .join(Usuario, Venta.vendedor_id == Usuario.id)\
        .order_by(Venta.fecha.desc()).all()

    ultima_venta = Venta.query.order_by(Venta.id.desc()).first()
    try:
        proximo_numero = int(ultima_venta.numero_operacion) + 1 if ultima_venta else 1
    except (TypeError, ValueError):
        proximo_numero = (ultima_venta.id + 1) if ultima_venta else 1
    while Venta.query.filter_by(numero_operacion=str(proximo_numero)).first():
        proximo_numero += 1

    if request.method == 'POST':
        vehiculo = Vehiculo.query.get(request.form.get('vehiculo'))
        cliente = Cliente.query.filter_by(
            id=request.form.get('cliente'), origen_registro='crm'
        ).first()

        if not vehiculo or not cliente:
            return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero, error="Debe seleccionar un cliente y un vehículo.")

        if vehiculo.estado != 'disponible':
            vehiculos_lista = Vehiculo.query.filter_by(estado='disponible').all()
            return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero, error="El vehículo seleccionado ya no está disponible porque está reservado o vendido.")

        precio_vehiculo = float(request.form.get('precio_vehiculo') or vehiculo.precio_venta or 0)
        bonificaciones = float(request.form.get('bonificaciones') or 0)
        total_vehiculo = precio_vehiculo - bonificaciones
        gestoria = float(request.form.get('gestoria') or 0)
        total_general = total_vehiculo + gestoria

        senia = float(request.form.get('senia') or 0)
        contado = float(request.form.get('contado') or 0)
        toma_usado = bool(request.form.get('toma_usado'))
        precio_toma = float(request.form.get('precio_toma') or 0) if toma_usado else 0
        detalle_toma_usado = request.form.get('detalle_toma_usado', '').strip() if toma_usado else ''
        financiado = float(request.form.get('financiado') or 0)
        banco = request.form.get('banco', '').strip() if financiado else ''
        total_pago = senia + contado + precio_toma + financiado
        nombre_operacion = str(proximo_numero)

        datos_completos = all([
            cliente.nombre, cliente.telefono, cliente.email, cliente.domicilio,
            cliente.localidad, cliente.provincia
        ])
        if not datos_completos:
            return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero, error="El cliente debe tener todos los datos completos.")

        if precio_vehiculo < 0 or bonificaciones < 0 or bonificaciones > precio_vehiculo or gestoria < 0:
            return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero, error="Los importes del boleto no son válidos.")

        if min(senia, contado, precio_toma, financiado) < 0:
            return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero, error="Los importes de la forma de pago no pueden ser negativos.")

        if toma_usado and not detalle_toma_usado:
            return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero, error="Debe describir el vehículo usado recibido.")

        if financiado and not banco:
            return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero, error="Debe indicar la entidad financiera.")

        caja_actual = caja_principal()
        if (senia > 0 or contado > 0) and caja_actual.estado != 'abierta':
            return render_template(
                'ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista,
                ventas=ventas_lista, proximo_numero=proximo_numero,
                error="Debe abrir la caja para registrar la seña o el pago inicial en efectivo."
            )

        venta = Venta(
            numero_operacion=nombre_operacion,
            cliente_id=cliente.id,
            vehiculo_id=vehiculo.id,
            vendedor_id=session['user_id'],
            tipo_operacion='financiado' if financiado else 'contado',
            precio=total_general,
            precio_vehiculo=precio_vehiculo,
            bonificaciones=bonificaciones,
            total_vehiculo=total_vehiculo,
            gestoria=gestoria,
            total_general=total_general,
            entrega=contado,
            monto_financiado=financiado,
            banco=banco,
            toma_usado=toma_usado,
            precio_toma=precio_toma,
            detalle_toma_usado=detalle_toma_usado,
            total_pago=total_pago,
            fecha=datetime.now(),
            senia=senia,
            estado="reserva"
        )
        db.session.add(venta)
        db.session.flush()
        pagos_iniciales = (
            (senia, 'Seña inicial', 'Seña de vehículo'),
            (contado, 'Pago inicial al contado', 'Venta de vehículo'),
        )
        for monto, concepto, categoria in pagos_iniciales:
            if monto <= 0:
                continue
            db.session.add(PagoVenta(
                venta_id=venta.id, monto=monto, medio='efectivo',
                concepto=concepto, usuario_id=session['user_id'],
                fecha=datetime.now()
            ))
            db.session.add(MovimientoCaja(
                caja_id=caja_actual.id, tipo='ingreso', categoria=categoria,
                concepto=f'{concepto} operación #{venta.numero_operacion}',
                monto=Decimal(str(monto)).quantize(Decimal('0.01')),
                medio_pago='Efectivo', cuenta='Caja', cliente_id=venta.cliente_id,
                vehiculo_id=venta.vehiculo_id, venta_id=venta.id,
                usuario_id=session['user_id'],
                observaciones='Pago inicial registrado desde ventas'
            ))
        vehiculo.estado = "reservado"
        db.session.commit()
        return redirect(url_for('ver_venta', venta_id=venta.id))

    return render_template('ventas.html', clientes=clientes_lista, vehiculos=vehiculos_lista, ventas=ventas_lista, proximo_numero=proximo_numero)


# ---------------- CONTROL DE CAJA ----------------
CAJA_CATEGORIAS_INGRESO = [
    'Seña de vehículo', 'Venta de vehículo', 'Cuota', 'Anticipo',
    'Reserva', 'Cobro de financiación', 'Comisión', 'Servicio', 'Otro'
]
CAJA_CATEGORIAS_EGRESO = [
    'Compra de vehículo', 'Pago a proveedor', 'Combustible', 'Reparación',
    'Lavado', 'Gestoría', 'Transferencia', 'Patente', 'Seguro', 'Publicidad',
    'Sueldos', 'Comisiones', 'Alquiler', 'Servicios', 'Gastos administrativos', 'Otro'
]
CAJA_MEDIOS = ['Efectivo', 'Transferencia', 'Depósito bancario', 'Tarjeta de débito',
               'Tarjeta de crédito', 'Mercado Pago', 'Cheque', 'Otro']
CAJA_CUENTAS = ['Caja', 'Banco', 'Mercado Pago', 'Otra cuenta']


def medio_caja_desde_pago(medio):
    return {
        'efectivo': 'Efectivo',
        'transferencia': 'Transferencia',
        'tarjeta': 'Tarjeta de débito',
        'mercado_pago': 'Mercado Pago',
        'cheque': 'Cheque',
    }.get(medio, 'Otro')


def regularizar_movimientos_ventas_finalizadas(caja):
    ventas_finalizadas = Venta.query.filter_by(estado='finalizada').all()
    if not ventas_finalizadas:
        return

    venta_ids = [venta.id for venta in ventas_finalizadas]
    pagos_por_venta = {}
    for pago in PagoVenta.query.filter(PagoVenta.venta_id.in_(venta_ids)).all():
        pagos_por_venta.setdefault(pago.venta_id, []).append(pago)
    movimientos_por_venta = {}
    for movimiento in MovimientoCaja.query.filter(
        MovimientoCaja.venta_id.in_(venta_ids)
    ).all():
        movimientos_por_venta.setdefault(movimiento.venta_id, []).append(movimiento)

    hubo_cambios = False
    for venta in ventas_finalizadas:
        pagos = pagos_por_venta.get(venta.id, [])
        movimientos = movimientos_por_venta.get(venta.id, [])
        fecha = venta.fecha_finalizacion or venta.fecha or datetime.now()
        iniciales = (
            (
                Decimal(str(venta.senia or 0)).quantize(Decimal('0.01')),
                'Seña inicial', 'Seña de vehículo', 'Efectivo', 'Caja',
                ('Seña inicial',)
            ),
            (
                Decimal(str(venta.entrega or 0)).quantize(Decimal('0.01')),
                'Pago inicial al contado', 'Venta de vehículo', 'Efectivo', 'Caja',
                ('Pago inicial al contado',)
            ),
        )
        for importe, concepto, categoria, medio, cuenta, conceptos_pago in iniciales:
            if importe <= 0:
                continue
            pago_registrado = sum(
                (Decimal(str(pago.monto or 0)) for pago in pagos if pago.concepto in conceptos_pago),
                Decimal('0')
            )
            movimiento_registrado = sum(
                (movimiento.monto for movimiento in movimientos
                 if movimiento.tipo == 'ingreso' and movimiento.categoria == categoria),
                Decimal('0')
            )
            faltante_pago = max(importe - max(pago_registrado, movimiento_registrado), Decimal('0'))
            faltante_movimiento = max(importe - movimiento_registrado, Decimal('0'))
            if faltante_pago > 0:
                db.session.add(PagoVenta(
                    venta_id=venta.id, monto=float(faltante_pago), medio=medio.lower(),
                    concepto=concepto, usuario_id=session['user_id'], fecha=fecha,
                    observaciones='Regularización de pago inicial de operación finalizada'
                ))
                hubo_cambios = True
            if faltante_movimiento > 0:
                db.session.add(MovimientoCaja(
                    caja_id=caja.id, tipo='ingreso', categoria=categoria,
                    concepto=f'{concepto} operación #{venta.numero_operacion}',
                    monto=faltante_movimiento, medio_pago=medio, cuenta=cuenta,
                    cliente_id=venta.cliente_id, vehiculo_id=venta.vehiculo_id,
                    venta_id=venta.id, usuario_id=session['user_id'], fecha=fecha,
                    observaciones='Regularización histórica de operación finalizada'
                ))
                hubo_cambios = True

        financiacion = Decimal(str(venta.monto_financiado or 0)).quantize(Decimal('0.01'))
        if financiacion <= 0:
            continue
        pagos_financiacion = sum(
            (Decimal(str(pago.monto or 0)) for pago in pagos
             if pago.concepto in ('Financiación inicial', 'Financiación aprobada')),
            Decimal('0')
        )
        movimientos_financiacion = sum(
            (movimiento.monto for movimiento in movimientos
             if movimiento.tipo == 'ingreso' and movimiento.categoria == 'Cobro de financiación'),
            Decimal('0')
        )
        faltante_pago = max(financiacion - max(pagos_financiacion, movimientos_financiacion), Decimal('0'))
        faltante_movimiento = max(financiacion - movimientos_financiacion, Decimal('0'))
        if faltante_pago > 0:
            db.session.add(PagoVenta(
                venta_id=venta.id, monto=float(faltante_pago), medio='transferencia',
                concepto='Financiación inicial', usuario_id=session['user_id'],
                fecha=fecha, observaciones='Regularización de financiación de operación finalizada'
            ))
            hubo_cambios = True
        if faltante_movimiento > 0:
            db.session.add(MovimientoCaja(
                caja_id=caja.id, tipo='ingreso', categoria='Cobro de financiación',
                concepto=f'Financiación inicial operación #{venta.numero_operacion}',
                monto=faltante_movimiento, medio_pago='Transferencia', cuenta='Banco',
                cliente_id=venta.cliente_id, vehiculo_id=venta.vehiculo_id,
                venta_id=venta.id, usuario_id=session['user_id'], fecha=fecha,
                observaciones=venta.banco or 'Regularización histórica de operación finalizada'
            ))
            hubo_cambios = True

    if hubo_cambios:
        db.session.commit()


@app.route('/caja')
@caja_required
def caja():
    caja_actual = caja_principal()
    regularizar_movimientos_ventas_finalizadas(caja_actual)
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    mes = hoy.replace(day=1)
    movimientos_query = MovimientoCaja.query.filter_by(caja_id=caja_actual.id)
    desde = request.args.get('desde', '').strip()
    hasta = request.args.get('hasta', '').strip()
    fecha_desde = datetime.strptime(desde, '%Y-%m-%d') if desde else None
    fecha_hasta = (
        datetime.strptime(hasta, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        if hasta else None
    )
    tipo_filtro = request.args.get('tipo', '').strip()
    medio_filtro = request.args.get('medio_pago', '').strip()
    cuenta_filtro = request.args.get('cuenta', '').strip()
    buscar = request.args.get('buscar', '').strip()

    if desde:
        movimientos_query = movimientos_query.filter(MovimientoCaja.fecha >= fecha_desde)
    if hasta:
        movimientos_query = movimientos_query.filter(MovimientoCaja.fecha <= fecha_hasta)
    if tipo_filtro:
        movimientos_query = movimientos_query.filter(MovimientoCaja.tipo == tipo_filtro)
    if medio_filtro:
        movimientos_query = movimientos_query.filter(MovimientoCaja.medio_pago == medio_filtro)
    if cuenta_filtro:
        movimientos_query = movimientos_query.filter(MovimientoCaja.cuenta == cuenta_filtro)
    if buscar:
        movimientos_query = movimientos_query.filter(
            MovimientoCaja.concepto.contains(buscar) |
            MovimientoCaja.comprobante.contains(buscar) |
            MovimientoCaja.proveedor.contains(buscar)
        )
    movimientos = movimientos_query.order_by(MovimientoCaja.fecha.desc()).limit(200).all()

    activos = MovimientoCaja.query.filter_by(caja_id=caja_actual.id, estado='activo').all()
    ingresos_hoy = sum((m.monto for m in activos if m.tipo == 'ingreso' and m.fecha >= hoy), Decimal('0'))
    egresos_hoy = sum((m.monto for m in activos if m.tipo == 'egreso' and m.fecha >= hoy), Decimal('0'))
    ingresos_mes = sum((m.monto for m in activos if m.tipo == 'ingreso' and m.fecha >= mes), Decimal('0'))
    egresos_mes = sum((m.monto for m in activos if m.tipo == 'egreso' and m.fecha >= mes), Decimal('0'))
    ingresos_total, egresos_total, saldo_actual = resumen_caja(caja_actual)

    por_medio = {}
    for medio in CAJA_MEDIOS:
        ingresos = sum((m.monto for m in activos if m.medio_pago == medio and m.tipo == 'ingreso'), Decimal('0'))
        egresos = sum((m.monto for m in activos if m.medio_pago == medio and m.tipo == 'egreso'), Decimal('0'))
        por_medio[medio] = (ingresos, egresos, ingresos - egresos)
    por_cuenta = {}
    for cuenta in CAJA_CUENTAS:
        cuenta_key = cuenta.lower()
        ingresos = sum((m.monto for m in activos if m.cuenta.lower() == cuenta_key and m.tipo == 'ingreso'), Decimal('0'))
        egresos = sum((m.monto for m in activos if m.cuenta.lower() == cuenta_key and m.tipo == 'egreso'), Decimal('0'))
        por_cuenta[cuenta] = ingresos - egresos

    fecha_operacion = func.coalesce(Venta.fecha_finalizacion, Venta.fecha)
    ventas_query = Venta.query.filter_by(estado='finalizada')
    if fecha_desde:
        ventas_query = ventas_query.filter(fecha_operacion >= fecha_desde)
    if fecha_hasta:
        ventas_query = ventas_query.filter(fecha_operacion <= fecha_hasta)
    ventas_finalizadas = ventas_query.order_by(fecha_operacion.desc()).all()
    vehiculos_finalizados = {
        venta.vehiculo_id: Vehiculo.query.get(venta.vehiculo_id)
        for venta in ventas_finalizadas
    }
    gastos_por_vehiculo = {}
    if vehiculos_finalizados:
        gastos = GastoVehiculo.query.filter(
            GastoVehiculo.vehiculo_id.in_(vehiculos_finalizados)
        ).all()
        for gasto in gastos:
            gastos_por_vehiculo.setdefault(gasto.vehiculo_id, []).append(gasto)

    informe_operaciones = []
    total_facturacion = Decimal('0')
    ganancia_total = Decimal('0')
    for venta in ventas_finalizadas:
        fecha_finalizacion = venta.fecha_finalizacion or venta.fecha
        facturacion = Decimal(str(venta.total_general or venta.precio or 0))
        vehiculo = vehiculos_finalizados[venta.vehiculo_id]
        costo_vehiculo = Decimal(str((vehiculo.precio_compra if vehiculo else 0) or 0))
        gastos_vehiculo = sum(
            (gasto.monto for gasto in gastos_por_vehiculo.get(venta.vehiculo_id, [])
             if gasto.fecha_registro <= fecha_finalizacion),
            Decimal('0')
        )
        ganancia = facturacion - costo_vehiculo - gastos_vehiculo
        total_facturacion += facturacion
        ganancia_total += ganancia
        informe_operaciones.append({
            'venta': venta, 'fecha': fecha_finalizacion, 'facturacion': facturacion,
            'costo_vehiculo': costo_vehiculo, 'gastos_vehiculo': gastos_vehiculo,
            'ganancia': ganancia
        })

    clientes_lista = clientes_crm_visibles().order_by(Cliente.nombre).all()
    vehiculos_lista = Vehiculo.query.order_by(Vehiculo.marca, Vehiculo.version).all()
    cierres = CierreCaja.query.filter_by(caja_id=caja_actual.id).order_by(CierreCaja.fecha.desc()).limit(20).all()
    return render_template(
        'caja.html', caja=caja_actual, movimientos=movimientos, clientes=clientes_lista,
        vehiculos=vehiculos_lista, cierres=cierres, categorias_ingreso=CAJA_CATEGORIAS_INGRESO,
        categorias_egreso=CAJA_CATEGORIAS_EGRESO, medios=CAJA_MEDIOS, cuentas=CAJA_CUENTAS,
        ingresos_hoy=ingresos_hoy, egresos_hoy=egresos_hoy,
        resultado_hoy=ingresos_hoy - egresos_hoy, ingresos_mes=ingresos_mes,
        egresos_mes=egresos_mes, resultado_mes=ingresos_mes - egresos_mes,
        ingresos_total=ingresos_total, egresos_total=egresos_total,
        saldo_actual=saldo_actual, por_medio=por_medio, por_cuenta=por_cuenta,
        informe_operaciones=informe_operaciones,
        total_facturacion=total_facturacion, ganancia_total=ganancia_total,
        filtros=request.args, now=datetime.now()
    )


@app.route('/caja/apertura', methods=['POST'])
@caja_required
def abrir_caja():
    caja_actual = caja_principal()
    if caja_actual.estado == 'abierta':
        return redirect(url_for('caja'))
    saldo_inicial = decimal_form_value(request.form.get('saldo_inicial'))
    if saldo_inicial is None or saldo_inicial < 0:
        return redirect(url_for('caja'))
    caja_actual.estado = 'abierta'
    caja_actual.fecha_apertura = datetime.now()
    caja_actual.usuario_apertura_id = session['user_id']
    caja_actual.saldo_inicial = saldo_inicial
    caja_actual.observaciones = request.form.get('observaciones', '').strip()
    db.session.commit()
    return redirect(url_for('caja'))


@app.route('/caja/movimiento', methods=['POST'])
@caja_required
def registrar_movimiento_caja():
    caja_actual = caja_principal()
    if caja_actual.estado != 'abierta':
        return redirect(url_for('caja'))
    monto = decimal_form_value(request.form.get('monto'))
    tipo = request.form.get('tipo', '').strip()
    categoria = request.form.get('categoria', '').strip()
    concepto = request.form.get('concepto', '').strip()
    medio = request.form.get('medio_pago', '').strip()
    cuenta = request.form.get('cuenta', 'Caja').strip()
    if monto is None or monto <= 0 or tipo not in ('ingreso', 'egreso') or not categoria or not concepto or not medio:
        return redirect(url_for('caja'))
    movimiento = MovimientoCaja(
        caja_id=caja_actual.id, tipo=tipo, categoria=categoria, concepto=concepto,
        monto=monto, medio_pago=medio, cuenta=cuenta,
        cliente_id=request.form.get('cliente_id') or None,
        vehiculo_id=request.form.get('vehiculo_id') or None,
        usuario_id=session['user_id'], proveedor=request.form.get('proveedor', '').strip() or None,
        comprobante=request.form.get('comprobante', '').strip() or None,
        observaciones=request.form.get('observaciones', '').strip() or None
    )
    db.session.add(movimiento)
    db.session.commit()
    return redirect(url_for('caja'))


@app.route('/caja/cierre', methods=['POST'])
@caja_required
def cerrar_caja():
    caja_actual = caja_principal()
    if caja_actual.estado != 'abierta':
        return redirect(url_for('caja'))
    saldo_real = decimal_form_value(request.form.get('saldo_real'))
    if saldo_real is None or saldo_real < 0:
        return redirect(url_for('caja'))
    ingresos, egresos, esperado = resumen_caja(caja_actual)
    diferencia = saldo_real - esperado
    observaciones = request.form.get('observaciones', '').strip()
    if diferencia != 0 and not observaciones:
        return redirect(url_for('caja'))
    cierre = CierreCaja(
        caja_id=caja_actual.id, usuario_id=session['user_id'], saldo_inicial=caja_actual.saldo_inicial,
        total_ingresos=ingresos, total_egresos=egresos, saldo_esperado=esperado,
        saldo_real=saldo_real, diferencia=diferencia, observaciones=observaciones or None
    )
    caja_actual.estado = 'cerrada'
    db.session.add(cierre)
    db.session.commit()
    return redirect(url_for('caja'))


@app.route('/caja/transferir', methods=['POST'])
@caja_required
def transferir_caja():
    caja_actual = caja_principal()
    monto = decimal_form_value(request.form.get('monto'))
    origen = request.form.get('origen', '').strip()
    destino = request.form.get('destino', '').strip()
    concepto = request.form.get('concepto', '').strip() or 'Transferencia entre cuentas'
    if caja_actual.estado != 'abierta' or monto is None or monto <= 0 or origen == destino or origen not in CAJA_CUENTAS or destino not in CAJA_CUENTAS:
        return redirect(url_for('caja'))
    transferencia_id = str(uuid4())
    db.session.add_all([
        MovimientoCaja(
            caja_id=caja_actual.id, tipo='egreso', categoria='Transferencia', concepto=concepto,
            monto=monto, medio_pago='Transferencia', cuenta=origen, usuario_id=session['user_id'],
            transferencia_id=transferencia_id
        ),
        MovimientoCaja(
            caja_id=caja_actual.id, tipo='ingreso', categoria='Transferencia', concepto=concepto,
            monto=monto, medio_pago='Transferencia', cuenta=destino, usuario_id=session['user_id'],
            transferencia_id=transferencia_id
        )
    ])
    db.session.commit()
    return redirect(url_for('caja'))


@app.route('/caja/movimiento/<int:movimiento_id>/anular', methods=['POST'])
@admin_required
def anular_movimiento_caja(movimiento_id):
    movimiento = MovimientoCaja.query.get_or_404(movimiento_id)
    if movimiento.estado == 'activo':
        motivo = request.form.get('motivo', '').strip()
        if not motivo:
            return redirect(url_for('caja'))
        movimiento.estado = 'anulado'
        movimiento.anulado_por_id = session['user_id']
        movimiento.fecha_anulacion = datetime.now()
        movimiento.motivo_anulacion = motivo
        db.session.commit()
    return redirect(url_for('caja'))


@app.route('/caja/movimiento/<int:movimiento_id>')
@caja_required
def ver_movimiento_caja(movimiento_id):
    movimiento = MovimientoCaja.query.get_or_404(movimiento_id)
    return render_template('movimiento_caja.html', movimiento=movimiento)


# ---------------- BOLETO DE VENTA PDF ----------------
@app.route('/boleto_venta/<int:venta_id>')
@admin_required
def boleto_venta(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    cliente = Cliente.query.get(venta.cliente_id)
    vehiculo = Vehiculo.query.get(venta.vehiculo_id)
    vendedor = Usuario.query.get(venta.vendedor_id)
    empresa = {
        'nombre': 'Monteros Automotores',
        'direccion': 'Av. Principal 123, Monteros',
        'telefono': '381-1234567',
        'email': 'info@monteros.com'
    }
    if render_pdf:
        return render_pdf(
            HTML(string=render_template('boleto_venta.html',
                venta=venta, cliente=cliente, vehiculo=vehiculo, vendedor=vendedor, empresa=empresa
            ))
        )
    return render_template('boleto_venta.html',
        venta=venta, cliente=cliente, vehiculo=vehiculo, vendedor=vendedor, empresa=empresa
    )

@app.route('/ventas/ver/<int:venta_id>')
@admin_required
def ver_venta(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    cliente = Cliente.query.get(venta.cliente_id)
    vehiculo = Vehiculo.query.get(venta.vehiculo_id)
    vendedor = Usuario.query.get(venta.vendedor_id)
    pagos = pagos_de_venta(venta)
    return render_template(
        'ver_venta.html', venta=venta, cliente=cliente, vehiculo=vehiculo,
        vendedor=vendedor, pagos=pagos, total_cobrado=total_cobrado(venta),
        saldo=saldo_venta(venta)
    )

@app.route('/ventas/editar/<int:venta_id>', methods=['GET', 'POST'])
@admin_required
def editar_venta(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    if venta.estado != 'reserva':
        return redirect(url_for('ver_venta', venta_id=venta.id))

    clientes_lista = Cliente.query.filter_by(
        origen_registro='crm'
    ).order_by(Cliente.nombre).all()
    vehiculos_lista = Vehiculo.query.filter(
        (Vehiculo.estado == 'disponible') | (Vehiculo.id == venta.vehiculo_id)
    ).order_by(Vehiculo.marca).all()

    if request.method == 'POST':
        cliente = Cliente.query.filter_by(
            id=request.form.get('cliente'), origen_registro='crm'
        ).first()
        vehiculo = Vehiculo.query.get(request.form.get('vehiculo'))
        precio_vehiculo = float(request.form.get('precio_vehiculo') or 0)
        bonificaciones = float(request.form.get('bonificaciones') or 0)
        gestoria = float(request.form.get('gestoria') or 0)
        total_vehiculo = precio_vehiculo - bonificaciones
        total_general = total_vehiculo + gestoria
        senia = float(request.form.get('senia') or 0)
        contado = float(request.form.get('contado') or 0)
        toma_usado = bool(request.form.get('toma_usado'))
        precio_toma = float(request.form.get('precio_toma') or 0) if toma_usado else 0
        detalle_toma_usado = request.form.get('detalle_toma_usado', '').strip() if toma_usado else ''
        financiado = float(request.form.get('financiado') or 0)
        banco = request.form.get('banco', '').strip() if financiado else ''

        if not cliente or not vehiculo:
            error = 'Debe seleccionar un cliente y un vehículo.'
        elif precio_vehiculo < 0 or bonificaciones < 0 or bonificaciones > precio_vehiculo or gestoria < 0:
            error = 'Los importes del boleto no son válidos.'
        elif min(senia, contado, precio_toma, financiado) < 0:
            error = 'Los importes no pueden ser negativos.'
        elif toma_usado and not detalle_toma_usado:
            error = 'Debe describir el vehículo usado recibido.'
        elif financiado and not banco:
            error = 'Debe indicar la entidad financiera.'
        else:
            if vehiculo.id != venta.vehiculo_id:
                anterior = Vehiculo.query.get(venta.vehiculo_id)
                if anterior:
                    anterior.estado = 'disponible'
                vehiculo.estado = 'reservado'
            venta.cliente_id = cliente.id
            venta.vehiculo_id = vehiculo.id
            venta.tipo_operacion = 'financiado' if financiado else 'contado'
            venta.precio = total_general
            venta.precio_vehiculo = precio_vehiculo
            venta.bonificaciones = bonificaciones
            venta.total_vehiculo = total_vehiculo
            venta.gestoria = gestoria
            venta.total_general = total_general
            venta.senia = senia
            venta.entrega = contado
            venta.toma_usado = toma_usado
            venta.precio_toma = precio_toma
            venta.detalle_toma_usado = detalle_toma_usado
            venta.monto_financiado = financiado
            venta.banco = banco
            venta.total_pago = senia + contado + precio_toma + financiado
            db.session.commit()
            return redirect(url_for('ver_venta', venta_id=venta.id))

        return render_template('editar_venta.html', venta=venta, clientes=clientes_lista, vehiculos=vehiculos_lista, error=error)

    return render_template('editar_venta.html', venta=venta, clientes=clientes_lista, vehiculos=vehiculos_lista)

@app.route('/ventas/agregar_anticipo/<int:venta_id>', methods=['POST'])
@admin_required
def agregar_anticipo(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    if venta.estado != 'reserva':
        return redirect(url_for('ver_venta', venta_id=venta.id))
    try:
        dinero = float(request.form.get('dinero_adicional') or 0)
    except (TypeError, ValueError):
        dinero = 0
    if dinero <= 0 or round(dinero, 2) > max(saldo_venta(venta), 0):
        return redirect(url_for('ver_venta', venta_id=venta.id))
    caja_actual = caja_principal()
    if caja_actual.estado != 'abierta':
        return redirect(url_for('ver_venta', venta_id=venta.id))

    medio = request.form.get('medio', 'efectivo').strip() or 'efectivo'
    observaciones = request.form.get('observaciones', '').strip()
    pago = PagoVenta(
        venta_id=venta.id, monto=round(dinero, 2), medio=medio,
        concepto='Pago de cuenta', usuario_id=session['user_id'],
        fecha=datetime.now(), observaciones=observaciones or None
    )
    db.session.add(pago)
    db.session.add(MovimientoCaja(
        caja_id=caja_actual.id, tipo='ingreso', categoria='Seña de vehículo',
        concepto=f'Pago operación #{venta.numero_operacion}', monto=Decimal(str(dinero)).quantize(Decimal('0.01')),
        medio_pago=medio_caja_desde_pago(medio), cuenta='Caja', cliente_id=venta.cliente_id,
        vehiculo_id=venta.vehiculo_id, venta_id=venta.id, usuario_id=session['user_id'],
        observaciones=observaciones or 'Ingreso registrado desde ventas'
    ))
    db.session.commit()
    return redirect(url_for('ver_venta', venta_id=venta.id))


@app.route('/ventas/pago/<int:venta_id>/recibo/<int:pago_id>')
@admin_required
def recibo_pago(venta_id, pago_id):
    venta = Venta.query.get_or_404(venta_id)
    pago = PagoVenta.query.filter_by(id=pago_id, venta_id=venta.id).first_or_404()
    cliente = Cliente.query.get(venta.cliente_id)
    vehiculo = Vehiculo.query.get(venta.vehiculo_id)
    return render_template(
        'recibo_pago.html', venta=venta, pago=pago, cliente=cliente,
        vehiculo=vehiculo, saldo=saldo_venta(venta)
    )

@app.route('/ventas/finalizar/<int:venta_id>', methods=['GET', 'POST'])
@admin_required
def finalizar_venta(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    if venta.estado != 'reserva':
        return redirect(url_for('ver_venta', venta_id=venta.id))
    cliente = Cliente.query.get(venta.cliente_id)
    vehiculo = Vehiculo.query.get(venta.vehiculo_id)
    vendedor = Usuario.query.get(venta.vendedor_id)

    error = None
    if request.method == 'POST':
        efectivo = float(request.form.get('efectivo') or 0)
        toma_usado = True if request.form.get('toma_usado') else False
        precio_toma = float(request.form.get('precio_toma') or 0) if toma_usado else 0
        detalle_toma_usado = request.form.get('detalle_toma_usado', '').strip() if toma_usado else venta.detalle_toma_usado
        financiado = float(request.form.get('financiado') or 0)
        banco = request.form.get('banco') or venta.banco
        observaciones = request.form.get('observaciones') or venta.observaciones

        anticipos_pendientes = anticipos_iniciales_pendientes(venta)
        financiacion_pendiente = financiacion_inicial_pendiente(venta)
        pagos_registrados = (
            total_cobrado(venta) + (venta.precio_toma or 0)
            + financiacion_inicial_pendiente(venta)
        )
        total_pagado = pagos_registrados + efectivo + precio_toma + financiado
        total_general = venta.total_general or venta.precio or 0
        if min(efectivo, precio_toma, financiado) < 0:
            error = 'Los importes no pueden ser negativos.'
        elif round(total_pagado, 2) != round(total_general, 2):
            error = 'El Total General del boleto debe coincidir con el Total de la forma de pago para finalizar.'
        else:
            caja_actual = caja_principal()
            if (efectivo > 0 or financiado > 0 or anticipos_pendientes or financiacion_pendiente) and caja_actual.estado != 'abierta':
                error = 'Debe abrir la caja antes de registrar el pago final.'
            else:
                for concepto, categoria, monto in anticipos_pendientes:
                    db.session.add(PagoVenta(
                        venta_id=venta.id, monto=monto, medio='efectivo',
                        concepto=concepto, usuario_id=session['user_id'],
                        fecha=datetime.now()
                    ))
                    db.session.add(MovimientoCaja(
                        caja_id=caja_actual.id, tipo='ingreso', categoria=categoria,
                        concepto=f'{concepto} operación #{venta.numero_operacion}',
                        monto=Decimal(str(monto)).quantize(Decimal('0.01')),
                        medio_pago='Efectivo', cuenta='Caja', cliente_id=venta.cliente_id,
                        vehiculo_id=venta.vehiculo_id, venta_id=venta.id,
                        usuario_id=session['user_id'],
                        observaciones='Pago inicial registrado al finalizar la operación'
                    ))
                if financiacion_pendiente > 0:
                    db.session.add(PagoVenta(
                        venta_id=venta.id, monto=financiacion_pendiente,
                        medio='transferencia', concepto='Financiación inicial',
                        usuario_id=session['user_id'], fecha=datetime.now()
                    ))
                    db.session.add(MovimientoCaja(
                        caja_id=caja_actual.id, tipo='ingreso',
                        categoria='Cobro de financiación',
                        concepto=f'Financiación inicial operación #{venta.numero_operacion}',
                        monto=Decimal(str(financiacion_pendiente)).quantize(Decimal('0.01')),
                        medio_pago='Transferencia', cuenta='Banco',
                        cliente_id=venta.cliente_id, vehiculo_id=venta.vehiculo_id,
                        venta_id=venta.id, usuario_id=session['user_id'],
                        observaciones=venta.banco or 'Financiación registrada al finalizar la operación'
                    ))
                if efectivo > 0:
                    db.session.add(PagoVenta(
                        venta_id=venta.id, monto=efectivo, medio='efectivo',
                        concepto='Pago final', usuario_id=session['user_id'], fecha=datetime.now()
                    ))
                    db.session.add(MovimientoCaja(
                        caja_id=caja_actual.id, tipo='ingreso', categoria='Venta de vehículo',
                        concepto=f'Pago final operación #{venta.numero_operacion}', monto=Decimal(str(efectivo)).quantize(Decimal('0.01')),
                        medio_pago='Efectivo', cuenta='Caja', cliente_id=venta.cliente_id,
                        vehiculo_id=venta.vehiculo_id, venta_id=venta.id, usuario_id=session['user_id'],
                        observaciones=observaciones or 'Pago final registrado desde ventas'
                    ))
                if financiado > 0:
                    db.session.add(PagoVenta(
                        venta_id=venta.id, monto=financiado, medio='transferencia',
                        concepto='Financiación aprobada', usuario_id=session['user_id'], fecha=datetime.now()
                    ))
                    db.session.add(MovimientoCaja(
                        caja_id=caja_actual.id, tipo='ingreso', categoria='Cobro de financiación',
                        concepto=f'Financiación operación #{venta.numero_operacion}', monto=Decimal(str(financiado)).quantize(Decimal('0.01')),
                        medio_pago='Transferencia', cuenta='Banco', cliente_id=venta.cliente_id,
                        vehiculo_id=venta.vehiculo_id, venta_id=venta.id, usuario_id=session['user_id'],
                        observaciones=banco or 'Financiación registrada desde ventas'
                    ))
                venta.toma_usado = toma_usado
                venta.precio_toma = (venta.precio_toma or 0) + precio_toma
                venta.detalle_toma_usado = detalle_toma_usado
                venta.monto_financiado = (venta.monto_financiado or 0) + financiado
                venta.banco = banco
                venta.observaciones = observaciones
                venta.estado = 'finalizada'
                venta.fecha_finalizacion = datetime.now()
                vehiculo.estado = 'vendido'
                db.session.commit()
                return redirect(url_for('ver_venta', venta_id=venta.id))

    return render_template(
        'finalizar_venta.html', venta=venta, cliente=cliente, vehiculo=vehiculo,
        vendedor=vendedor, error=error, saldo=saldo_venta(venta),
        total_cobrado=total_cobrado(venta)
    )

@app.route('/ventas/cancelar/<int:venta_id>', methods=['POST'])
@admin_required
def cancelar_venta(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    if venta.estado == 'reserva':
        vehiculo = Vehiculo.query.get(venta.vehiculo_id)
        vehiculo.estado = 'disponible'
        venta.estado = 'cancelada'
        db.session.commit()
    return redirect(url_for('historial'))

@app.route('/ventas/eliminar/<int:venta_id>', methods=['POST'])
@admin_required
def eliminar_venta(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    vehiculo = Vehiculo.query.get(venta.vehiculo_id)
    if vehiculo and venta.estado in ('reserva', 'cancelada'):
        vehiculo.estado = 'disponible'
    db.session.delete(venta)
    db.session.commit()
    return redirect(url_for('historial'))

@app.route('/documento_reserva/<int:venta_id>')
@admin_required
def documento_reserva(venta_id):
    venta = Venta.query.get_or_404(venta_id)
    cliente = Cliente.query.get(venta.cliente_id)
    vehiculo = Vehiculo.query.get(venta.vehiculo_id)
    vendedor = Usuario.query.get(venta.vendedor_id)
    fecha_vencimiento = (venta.fecha or datetime.now()) + timedelta(days=30)
    empresa = {
        'nombre': 'Monteros Automotores',
        'direccion': 'Av. Principal 123, Monteros',
        'telefono': '381-1234567',
        'email': 'info@monteros.com'
    }
    return render_template(
        'documento_reserva.html', venta=venta, cliente=cliente,
        vehiculo=vehiculo, vendedor=vendedor, empresa=empresa,
        fecha_vencimiento=fecha_vencimiento
    )

# ---------------- HISTORIAL DE VENTAS ----------------
@app.route('/historial')
@admin_required
def historial():
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('buscar', '').strip()
    vendedor_filtro = request.args.get('vendedor', '')

    query = db.session.query(Venta, Cliente, Vehiculo, Usuario)\
        .join(Cliente, Venta.cliente_id == Cliente.id)\
        .join(Vehiculo, Venta.vehiculo_id == Vehiculo.id)\
        .join(Usuario, Venta.vendedor_id == Usuario.id)

    if busqueda:
        query = query.filter(
            Cliente.nombre.contains(busqueda) |
            Vehiculo.marca.contains(busqueda) |
            Vehiculo.dominio.contains(busqueda)
        )
    if vendedor_filtro:
        query = query.filter(Usuario.username == vendedor_filtro)

    total = query.count()
    ventas_pag = query.order_by(Venta.fecha.desc()).paginate(page=page, per_page=15)

    vendedores = Usuario.query.all()
    total_filtrado = db.session.query(func.sum(Venta.precio))
    if busqueda or vendedor_filtro:
        total_filtrado_val = query.with_entities(func.sum(Venta.precio)).scalar() or 0
    else:
        total_filtrado_val = db.session.query(func.sum(Venta.precio)).scalar() or 0

    return render_template('historial.html',
        ventas=ventas_pag,
        vendedores=vendedores,
        total_filtrado=total_filtrado_val,
        busqueda=busqueda,
        vendedor_filtro=vendedor_filtro
    )

# ---------------- PRECIOS ----------------
@app.route('/precios')
@login_required
def precios():
    vehiculos_lista = Vehiculo.query.filter_by(estado="disponible")\
                              .order_by(Vehiculo.interno.asc()).all()
    return render_template('precios.html', vehiculos=vehiculos_lista)

# ---------------- USUARIOS (ADMIN) ----------------
@app.route('/usuarios', methods=['GET', 'POST'])
@admin_required
def usuarios():
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'crear':
            if not Usuario.query.filter_by(username=request.form['username']).first():
                u = Usuario(
                    username=request.form['username'],
                    password=generate_password_hash(request.form['password']),
                    rol=request.form['rol'],
                    nombre_completo=request.form['nombre_completo'],
                    telefono=request.form.get('telefono', '').strip() or None
                )
                db.session.add(u)
                db.session.commit()
        elif accion == 'actualizar_telefono':
            u = Usuario.query.get(request.form['user_id'])
            if u:
                u.telefono = request.form.get('telefono', '').strip() or None
                db.session.commit()
        elif accion == 'cambiar_password':
            u = Usuario.query.get(request.form['user_id'])
            if u:
                u.password = generate_password_hash(request.form['nueva_password'])
                db.session.commit()
        elif accion == 'eliminar':
            u = Usuario.query.get(request.form['user_id'])
            if u and u.username != 'admin':
                db.session.delete(u)
                db.session.commit()
        return redirect('/usuarios')

    lista = Usuario.query.all()
    return render_template('usuarios.html', usuarios=lista)

# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=False)
