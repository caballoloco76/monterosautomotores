from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(200))
    rol = db.Column(db.String(20))  # admin / vendedor
    nombre_completo = db.Column(db.String(100))
    telefono = db.Column(db.String(30))

    clientes_asignados = db.relationship('Cliente', foreign_keys='Cliente.vendedor_id', back_populates='vendedor', lazy='dynamic')
    seguimientos_asignados = db.relationship('Seguimiento', foreign_keys='Seguimiento.vendedor_id', back_populates='vendedor', lazy='dynamic')

class Vehiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    interno = db.Column(db.Integer, unique=True)
    dominio = db.Column(db.String(20))
    tipo = db.Column(db.String(20))  # Auto, Moto, Camioneta, Camión
    marca = db.Column(db.String(50))
    version = db.Column(db.String(50))
    año = db.Column(db.Integer)
    km = db.Column(db.Integer)
    color = db.Column(db.String(50))
    chasis = db.Column(db.String(50))
    motor = db.Column(db.String(50))
    precio_compra = db.Column(db.Float)
    precio_venta = db.Column(db.Float)
    origen = db.Column(db.String(20), default="propio")  # propio / consignacion
    titular_nombre_apellido = db.Column(db.String(120))
    titular_telefono = db.Column(db.String(30))
    titular_localidad = db.Column(db.String(100))
    titular_direccion = db.Column(db.String(200))
    estado = db.Column(db.String(20), default="disponible")
    fecha_ingreso = db.Column(db.DateTime, default=datetime.utcnow)
    fotos = db.relationship(
        'FotoVehiculo', back_populates='vehiculo',
        cascade='all, delete-orphan', order_by='FotoVehiculo.creado_en'
    )


class FotoVehiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehiculo_id = db.Column(db.Integer, db.ForeignKey('vehiculo.id'), nullable=False, index=True)
    nombre_archivo = db.Column(db.String(80), nullable=False, unique=True)
    creado_en = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    vehiculo = db.relationship('Vehiculo', back_populates='fotos')

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    interno = db.Column(db.Integer, unique=True)
    dni = db.Column(db.String(20))
    nombre = db.Column(db.String(100))
    telefono = db.Column(db.String(20))
    email = db.Column(db.String(100))
    domicilio = db.Column(db.String(150))
    localidad = db.Column(db.String(100))
    provincia = db.Column(db.String(100))
    tiene_usado = db.Column(db.String(10))
    usado_detalle = db.Column(db.String(200))
    interes = db.Column(db.String(100))
    descripcion = db.Column(db.Text)
    estado = db.Column(db.String(20), default="Nuevo")
    motivo_no_compra = db.Column(db.Text)
    fecha_alta = db.Column(db.DateTime, default=datetime.utcnow)
    proximo_contacto = db.Column(db.String(20))
    apellido = db.Column(db.String(100))
    fecha_nacimiento = db.Column(db.Date)
    whatsapp = db.Column(db.String(30))
    codigo_postal = db.Column(db.String(12))
    ocupacion = db.Column(db.String(100))
    estado_civil = db.Column(db.String(30))
    vendedor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), index=True)
    cargado_por_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), index=True)
    origen_cliente = db.Column(db.String(40), default='Otro')
    origen_registro = db.Column(db.String(20), nullable=False, default='crm')
    vehiculo_interes_id = db.Column(db.Integer, db.ForeignKey('vehiculo.id'), index=True)
    vehiculo_comprado_id = db.Column(db.Integer, db.ForeignKey('vehiculo.id'), index=True)

    vendedor = db.relationship('Usuario', foreign_keys=[vendedor_id], back_populates='clientes_asignados')
    cargado_por = db.relationship('Usuario', foreign_keys=[cargado_por_id])
    vehiculo_interes = db.relationship('Vehiculo', foreign_keys=[vehiculo_interes_id])
    vehiculo_comprado = db.relationship('Vehiculo', foreign_keys=[vehiculo_comprado_id])
    seguimientos = db.relationship('Seguimiento', back_populates='cliente', cascade='all, delete-orphan')
    notas = db.relationship('NotaCliente', back_populates='cliente', cascade='all, delete-orphan')
    lavadero_vehiculos = db.relationship('LavaderoVehiculo', back_populates='cliente')

class Venta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_operacion = db.Column(db.String(20), unique=True, nullable=False)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'))
    vehiculo_id = db.Column(db.Integer, db.ForeignKey('vehiculo.id'))
    vendedor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    tipo_operacion = db.Column(db.String(20))  # contado / financiado
    precio = db.Column(db.Float)
    precio_vehiculo = db.Column(db.Float, default=0)
    bonificaciones = db.Column(db.Float, default=0)
    total_vehiculo = db.Column(db.Float, default=0)
    gestoria = db.Column(db.Float, default=0)
    total_general = db.Column(db.Float, default=0)
    entrega = db.Column(db.Float, default=0)
    monto_financiado = db.Column(db.Float, default=0)
    banco = db.Column(db.String(100))
    toma_usado = db.Column(db.Boolean, default=False)
    precio_toma = db.Column(db.Float, default=0)
    detalle_toma_usado = db.Column(db.String(250))
    total_pago = db.Column(db.Float, default=0)
    fecha = db.Column(db.DateTime)
    fecha_finalizacion = db.Column(db.DateTime)
    observaciones = db.Column(db.Text)
    observaciones_senia = db.Column(db.Text)
    senia = db.Column(db.Float, default=0)
    estado = db.Column(db.String(20), default="reserva")  # reserva, finalizada
    cliente = db.relationship('Cliente')
    vehiculo = db.relationship('Vehiculo')


class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False, index=True)
    estado = db.Column(db.String(30), nullable=False, default='nuevo')
    origen = db.Column(db.String(40))
    fecha_alta = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    observaciones = db.Column(db.Text)

    cliente = db.relationship('Cliente', backref=db.backref('leads', lazy='dynamic', cascade='all, delete-orphan'))
    seguimientos = db.relationship('Seguimiento', back_populates='lead', cascade='all, delete-orphan')


class Seguimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), index=True)
    vendedor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), index=True)
    fecha = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    hora = db.Column(db.Time)
    tipo_contacto = db.Column(db.String(30), nullable=False, default='Otro')
    resultado = db.Column(db.String(100))
    proximo_contacto = db.Column(db.Date)
    observaciones = db.Column(db.Text)
    estado = db.Column(db.String(20), nullable=False, default='pendiente')
    creado_en = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    cliente = db.relationship('Cliente', back_populates='seguimientos')
    lead = db.relationship('Lead', back_populates='seguimientos')
    vendedor = db.relationship('Usuario', foreign_keys=[vendedor_id], back_populates='seguimientos_asignados')


class NotaCliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False, index=True)
    contenido = db.Column(db.Text, nullable=False)
    creado_en = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    cliente = db.relationship('Cliente', back_populates='notas')
    usuario = db.relationship('Usuario')


class GastoVehiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehiculo_id = db.Column(db.Integer, db.ForeignKey('vehiculo.id'), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False, index=True)
    tipo = db.Column(db.String(50), nullable=False)
    monto = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    descripcion = db.Column(db.Text)
    fecha_registro = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vehiculo = db.relationship('Vehiculo', backref=db.backref('gastos', lazy='dynamic', cascade='all, delete-orphan'))
    usuario = db.relationship('Usuario')

    @property
    def fecha(self):
        return self.fecha_registro


class LavaderoVehiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patente = db.Column(db.String(10), nullable=False, unique=True, index=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), index=True)
    unidad = db.Column(db.String(20), nullable=True)
    categoria_moto = db.Column(db.String(40))
    marca = db.Column(db.String(50))
    modelo = db.Column(db.String(50))
    anio = db.Column(db.Integer)
    color = db.Column(db.String(50))
    km = db.Column(db.Integer)

    cliente = db.relationship('Cliente', back_populates='lavadero_vehiculos')
    lavados = db.relationship(
        'Lavado', back_populates='vehiculo', lazy='dynamic',
        cascade='all, delete-orphan'
    )


class Lavado(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vehiculo_id = db.Column(db.Integer, db.ForeignKey('lavadero_vehiculo.id'), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False, index=True)
    numero_lavado = db.Column(db.Integer, nullable=False)
    unidad = db.Column(db.String(20), nullable=False, default='Auto')
    tipo = db.Column(db.String(40), nullable=False)
    precio = db.Column(db.Numeric(14, 2), nullable=False)
    descuento_porcentaje = db.Column(db.Integer, nullable=False, default=0)
    descuento_cobro_porcentaje = db.Column(db.Integer, nullable=False, default=0)
    total_cobrado = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    importe_final = db.Column(db.Numeric(14, 2), nullable=False)
    cuenta_para_promocion = db.Column(db.Boolean, nullable=False, default=True)
    promocion_especial_id = db.Column(db.Integer, db.ForeignKey('promocion_lavadero.id'), index=True)
    interesado_vender = db.Column(db.Boolean, nullable=False, default=False)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    terminado_en = db.Column(db.DateTime)
    control_tiempo = db.Column(db.Boolean, nullable=False, default=True)
    cobrado_en = db.Column(db.DateTime, index=True)
    medio_cobro = db.Column(db.String(30))
    titular_transferencia = db.Column(db.String(120))
    app_transferencia = db.Column(db.String(80))
    movimiento_caja_id = db.Column(db.Integer, db.ForeignKey('movimiento_caja.id'), unique=True)

    __table_args__ = (
        db.UniqueConstraint('vehiculo_id', 'numero_lavado', name='uq_lavado_vehiculo_numero'),
    )

    vehiculo = db.relationship('LavaderoVehiculo', back_populates='lavados')
    usuario = db.relationship('Usuario')
    promocion_especial = db.relationship('PromocionLavadero', foreign_keys=[promocion_especial_id])
    movimiento_caja = db.relationship('MovimientoCaja', foreign_keys=[movimiento_caja_id])


class PromocionLavadero(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False, index=True)
    porcentaje = db.Column(db.Integer, nullable=False)
    mensaje = db.Column(db.Text)
    creada_en = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    vence_en = db.Column(db.Date, nullable=False, index=True)
    estado = db.Column(db.String(20), nullable=False, default='preparada', index=True)
    lavado_id = db.Column(db.Integer, db.ForeignKey('lavado.id'), unique=True)

    cliente = db.relationship('Cliente')
    usuario = db.relationship('Usuario')


class Auditoria(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), index=True)
    accion = db.Column(db.String(80), nullable=False)
    entidad = db.Column(db.String(80), nullable=False)
    entidad_id = db.Column(db.Integer)
    detalle = db.Column(db.Text)
    creado_en = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    usuario = db.relationship('Usuario')


class PagoVenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=False, index=True)
    monto = db.Column(db.Float, nullable=False)
    medio = db.Column(db.String(30), nullable=False, default='efectivo')
    concepto = db.Column(db.String(120), nullable=False, default='Pago')
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    observaciones = db.Column(db.Text)

    venta = db.relationship('Venta', backref=db.backref('pagos', lazy='dynamic', cascade='all, delete-orphan'))
    usuario = db.relationship('Usuario')


class Caja(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False, default='Caja principal')
    estado = db.Column(db.String(20), nullable=False, default='cerrada')
    fecha_apertura = db.Column(db.DateTime)
    usuario_apertura_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    saldo_inicial = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    observaciones = db.Column(db.Text)

    usuario_apertura = db.relationship('Usuario', foreign_keys=[usuario_apertura_id])


class MovimientoCaja(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caja_id = db.Column(db.Integer, db.ForeignKey('caja.id'), nullable=False, index=True)
    tipo = db.Column(db.String(10), nullable=False)  # ingreso / egreso
    categoria = db.Column(db.String(60), nullable=False)
    concepto = db.Column(db.String(200), nullable=False)
    monto = db.Column(db.Numeric(14, 2), nullable=False)
    medio_pago = db.Column(db.String(40), nullable=False)
    cuenta = db.Column(db.String(40), nullable=False, default='caja')
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'))
    vehiculo_id = db.Column(db.Integer, db.ForeignKey('vehiculo.id'))
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'))
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    proveedor = db.Column(db.String(120))
    comprobante = db.Column(db.String(80))
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    observaciones = db.Column(db.Text)
    estado = db.Column(db.String(20), nullable=False, default='activo')
    anulado_por_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    fecha_anulacion = db.Column(db.DateTime)
    motivo_anulacion = db.Column(db.Text)
    transferencia_id = db.Column(db.String(40), index=True)

    caja = db.relationship('Caja', backref=db.backref('movimientos', lazy='dynamic'))
    cliente = db.relationship('Cliente')
    vehiculo = db.relationship('Vehiculo')
    venta = db.relationship('Venta')
    usuario = db.relationship('Usuario', foreign_keys=[usuario_id])
    anulado_por = db.relationship('Usuario', foreign_keys=[anulado_por_id])


class CierreCaja(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caja_id = db.Column(db.Integer, db.ForeignKey('caja.id'), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    saldo_inicial = db.Column(db.Numeric(14, 2), nullable=False)
    total_ingresos = db.Column(db.Numeric(14, 2), nullable=False)
    total_egresos = db.Column(db.Numeric(14, 2), nullable=False)
    saldo_esperado = db.Column(db.Numeric(14, 2), nullable=False)
    saldo_real = db.Column(db.Numeric(14, 2), nullable=False)
    diferencia = db.Column(db.Numeric(14, 2), nullable=False)
    observaciones = db.Column(db.Text)

    caja = db.relationship('Caja', backref=db.backref('cierres', lazy='dynamic'))
    usuario = db.relationship('Usuario')
