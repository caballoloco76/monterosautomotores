# ── REEMPLAZAR el bloque @app.route('/ventas') en app.py con esto ──

@app.route('/ventas', methods=['GET', 'POST'])
@login_required
def ventas():
    clientes_lista = Cliente.query.order_by(Cliente.nombre).all()
    vehiculos_lista = Vehiculo.query.filter_by(estado="disponible").all()

    # Número de operación automático
    ultima_venta = Venta.query.order_by(Venta.id.desc()).first()
    proximo_numero = (ultima_venta.id + 1) if ultima_venta else 1

    if request.method == 'POST':
        vehiculo = Vehiculo.query.get(request.form['vehiculo'])
        cliente = Cliente.query.get(request.form['cliente'])

        # Precios
        precio_lista = float(request.form.get('precio_lista') or vehiculo.precio_venta)
        descuento = float(request.form.get('descuento') or 0)
        gestoria = float(request.form.get('gestoria') or 0)
        precio_total = float(request.form.get('precio_total_unidad') or precio_lista)

        # Formas de pago
        anticipo = float(request.form.get('anticipo') or 0)
        efectivo = float(request.form.get('efectivo') or 0)
        toma_usado = True if request.form.get('toma_usado') else False
        precio_toma = float(request.form.get('precio_toma') or 0)
        financiado = float(request.form.get('financiado') or 0)
        banco = request.form.get('banco', '')

        # Número de operación
        numero_operacion = request.form.get('numero_operacion', str(proximo_numero))
        if Venta.query.filter_by(numero_operacion=numero_operacion).first():
            numero_operacion = str(proximo_numero)

        # Tipo de operación
        if financiado > 0:
            tipo_operacion = 'financiado'
        else:
            tipo_operacion = 'contado'

        # Determinar entrega (anticipo + efectivo)
        entrega = anticipo + efectivo

        venta = Venta(
            numero_operacion=numero_operacion,
            cliente_id=cliente.id,
            vehiculo_id=vehiculo.id,
            vendedor_id=session['user_id'],
            tipo_operacion=tipo_operacion,
            precio=precio_total,
            entrega=entrega,
            monto_financiado=financiado,
            banco=banco,
            toma_usado=toma_usado,
            precio_toma=precio_toma,
            fecha=datetime.now(),
            senia=anticipo,
            estado="finalizada"
        )
        vehiculo.estado = "vendido"
        cliente.estado = "Vendido"
        db.session.add(venta)
        db.session.commit()
        return redirect(url_for('historial'))

    return render_template('ventas.html',
        clientes=clientes_lista,
        vehiculos=vehiculos_lista,
        proximo_numero=proximo_numero
    )
