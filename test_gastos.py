#!/usr/bin/env python
"""
Script de prueba para verificar que el sistema de gastos funciona correctamente
"""
from decimal import Decimal

from test_support import reset_test_database

from app import app, db
from models import Usuario, Vehiculo, Cliente, Venta, GastoVehiculo
from werkzeug.security import generate_password_hash

def test_gastos_system():
    reset_test_database()
    with app.app_context():
        print("🔍 VERIFICANDO SISTEMA DE GASTOS...")
        print()
        
        # 1. Verificar que la tabla GastoVehiculo existe
        inspector = db.inspect(db.engine)
        tables = inspector.get_table_names()
        assert 'gasto_vehiculo' in tables, "La tabla GastoVehiculo no existe"
        print("✅ Tabla GastoVehiculo existe correctamente")
        
        print()
        
        # 2. Crear datos de prueba
        print("📝 Creando datos de prueba...")
        
        # Verificar si el usuario admin existe
        admin = Usuario.query.filter_by(username="admin").first()
        if not admin:
            admin = Usuario(
                username="admin",
                password=generate_password_hash("test"),
                rol="admin",
                nombre_completo="Administrador de prueba"
            )
            db.session.add(admin)
            db.session.commit()
        
        # Crear un vehículo de prueba
        vehiculo_test = Vehiculo.query.first()
        if not vehiculo_test:
            print("  📌 No hay vehículos. Creando uno de prueba...")
            vehiculo_test = Vehiculo(
                interno=999,
                dominio="TEST001",
                tipo="Auto",
                marca="TestMarca",
                version="TestVersion",
                año=2024,
                km=0,
                color="Rojo",
                precio_compra=50000,
                precio_venta=60000
            )
            db.session.add(vehiculo_test)
            db.session.commit()
            print(f"  ✅ Vehículo de prueba creado (ID: {vehiculo_test.id})")
        else:
            print(f"  ✅ Usando vehículo existente (ID: {vehiculo_test.id}, {vehiculo_test.dominio})")
        
        print()
        
        # 3. Crear gastos de prueba
        print("💰 Agregando gastos de prueba...")
        
        gastos_tipos = [
            ("Mecánica", 5000, "Revisión completa"),
            ("Pintura", 8000, "Repintado de panel frontal"),
            ("Limpieza", 1500, "Lavado interior y exterior"),
        ]
        
        for tipo, monto, desc in gastos_tipos:
            gasto = GastoVehiculo(
                vehiculo_id=vehiculo_test.id,
                tipo=tipo,
                descripcion=desc,
                monto=monto,
                usuario_id=admin.id
            )
            db.session.add(gasto)
            print(f"  ✅ Gasto agregado: {tipo} - ${monto}")
        
        db.session.commit()
        print()
        
        # 4. Verificar que los gastos se guardaron
        print("🔍 Verificando gastos guardados...")
        gastos = GastoVehiculo.query.filter_by(vehiculo_id=vehiculo_test.id).all()
        total_gastos = sum((g.monto for g in gastos), Decimal('0'))
        assert len(gastos) == len(gastos_tipos)
        assert total_gastos == Decimal('14500')
        
        print(f"  ✅ Gastos registrados: {len(gastos)}")
        print(f"  💵 Total de gastos: ${total_gastos}")
        
        # Calcular ganancia real
        precio_compra = Decimal(str(vehiculo_test.precio_compra or 0))
        precio_venta = Decimal(str(vehiculo_test.precio_venta or 0))
        ganancia_real = precio_venta - precio_compra - total_gastos
        
        print()
        print("📊 RESUMEN FINANCIERO:")
        print(f"  Precio Compra:  ${precio_compra}")
        print(f"  Precio Venta:   ${precio_venta}")
        print(f"  Total Gastos:   -${total_gastos}")
        print(f"  GANANCIA REAL:  ${ganancia_real}")
        
        if ganancia_real > 0:
            print(f"  ✅ Ganancia positiva: ${ganancia_real}")
        else:
            print(f"  ⚠️  Pérdida: ${ganancia_real}")
        
        print()
        print("=" * 50)
        print("✅ PRUEBAS COMPLETADAS EXITOSAMENTE")
        print("=" * 50)
        print()
        print("📌 PRÓXIMOS PASOS:")
        print("  1. Abre http://localhost:5000/")
        print("  2. Inicia sesión con una cuenta de prueba")
        print("  3. Ve a VEHÍCULOS y selecciona 'Gestionar Gastos'")
        print()
        
if __name__ == "__main__":
    try:
        test_gastos_system()
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
