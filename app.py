from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, time, timedelta, date
from werkzeug.utils import secure_filename
import os
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import telegram
import logging
import asyncio

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///turnos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Modelo de Paciente
class Paciente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dni = db.Column(db.String(10), unique=True, nullable=False)
    nombre = db.Column(db.String(100), nullable=False)
    telefono = db.Column(db.String(20))
    domicilio = db.Column(db.String(200))
    obra_social = db.Column(db.String(50))
    nota = db.Column(db.Text)
    turnos = db.relationship('Turno', backref='paciente', lazy=True)
    informe = db.Column(db.String(255))  # Para guardar la ruta del archivo

# Modelo de Turno
class Turno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False)
    hora = db.Column(db.Time, nullable=False)
    paciente_id = db.Column(db.Integer, db.ForeignKey('paciente.id'), nullable=False)
    estado = db.Column(db.String(20), default='Pendiente')  # Ahora incluirá 'Atención Finalizada'

@app.route('/')
def index():
    hoy = date.today()
    turnos_hoy = Turno.query.filter(Turno.fecha == hoy).order_by(Turno.hora.asc()).all()
    cantidad_turnos = len(turnos_hoy)
    return render_template('index.html', turnos_hoy=turnos_hoy, cantidad_turnos=cantidad_turnos)

@app.route('/pacientes', methods=['GET', 'POST'])
def pacientes():
    if request.method == 'POST':
        # Verificar si el DNI ya existe
        if Paciente.query.filter_by(dni=request.form['dni']).first():
            # Puedes mostrar un mensaje de error usando flash o render_template
            return render_template('pacientes.html', pacientes=Paciente.query.order_by(Paciente.id.desc()).limit(15).all(),
                                   busqueda='', filtro='nombre',
                                   error='Ya existe un paciente con ese DNI.')
        nuevo_paciente = Paciente(
            dni=request.form['dni'],
            nombre=request.form['nombre'],
            telefono=request.form['telefono'],
            domicilio=request.form['domicilio'],
            obra_social=request.form['obra_social'],
            nota=request.form['nota']
        )
        db.session.add(nuevo_paciente)
        db.session.commit()
        
        # Si se marcó la opción de asignar turno
        if 'asignarTurno' in request.form:
            return redirect(url_for('nuevo_turno', paciente_id=nuevo_paciente.id))
        
        return redirect(url_for('pacientes'))

    # Código existente para GET
    busqueda = request.args.get('busqueda', '')
    filtro = request.args.get('filtro', 'nombre')
    
    query = Paciente.query
    if busqueda:
        if filtro == 'nombre':
            query = query.filter(Paciente.nombre.ilike(f'%{busqueda}%'))
        elif filtro == 'dni':
            query = query.filter(Paciente.dni.ilike(f'%{busqueda}%'))
        elif filtro == 'telefono':
            query = query.filter(Paciente.telefono.ilike(f'%{busqueda}%'))
    
    pacientes = query.order_by(Paciente.id.desc()).limit(15).all()
    return render_template('pacientes.html', pacientes=pacientes, busqueda=busqueda, filtro=filtro)

@app.route('/turnos')
def turnos():
    paciente_id = request.args.get('paciente_id', None)
    fecha_filtro = request.args.get('fecha', None)
    dias_adicionales = int(request.args.get('dias', 1))
    telegram_success = request.args.get('telegram_success')
    fecha_busqueda = request.args.get('fecha_busqueda', None)

    query = Turno.query

    if paciente_id:
        query = query.filter(Turno.paciente_id == paciente_id)

    fecha_siguiente = datetime.now().date() + timedelta(days=dias_adicionales)
    if fecha_filtro == 'siguiente':
        query = query.filter(Turno.fecha == fecha_siguiente)

    if fecha_busqueda:
        try:
            fecha_obj = datetime.strptime(fecha_busqueda, '%Y-%m-%d').date()
            query = query.filter(Turno.fecha == fecha_obj)
            fecha_siguiente = fecha_obj
        except Exception:
            pass

    turnos = query.order_by(Turno.fecha.asc(), Turno.hora.asc()).all()

    return render_template('turnos.html',
                          turnos=turnos,
                          dias_adicionales=dias_adicionales,
                          fecha_siguiente=fecha_siguiente,
                          telegram_success=telegram_success)

@app.route('/nuevo_turno', methods=['GET', 'POST'])
def nuevo_turno():
    if request.method == 'POST':
        fecha = datetime.strptime(request.form['fecha'], '%Y-%m-%d').date()
        hora = datetime.strptime(request.form['hora'], '%H:%M').time()
        paciente_id = request.form['paciente_id']

        # Contar turnos existentes para la fecha seleccionada
        turnos_existentes = Turno.query.filter_by(fecha=fecha).count()

        # Validar límite de 10 turnos por día
        if turnos_existentes >= 10:
            return render_template('nuevo_turno.html', 
                                   pacientes=Paciente.query.all(),
                                   error="No se pueden asignar más de 10 turnos por día.")

        # Validar horario (8-13 y 16-20)
        if (time(8, 0) <= hora <= time(13, 0)) or (time(16, 0) <= hora <= time(20, 0)):
            turno = Turno(fecha=fecha, hora=hora, paciente_id=paciente_id)
            db.session.add(turno)
            db.session.commit()
            return redirect(url_for('turnos'))
    
    pacientes = Paciente.query.all()
    return render_template('nuevo_turno.html', pacientes=pacientes)

@app.route('/finalizar_turno/<int:turno_id>', methods=['POST'])
def finalizar_turno(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    turno.estado = 'Atención Finalizada'
    db.session.commit()
    return redirect(url_for('turnos'))

@app.route('/cambiar_estado/<int:turno_id>/<string:nuevo_estado>', methods=['POST'])
def cambiar_estado(turno_id, nuevo_estado):
    turno = Turno.query.get_or_404(turno_id)
    estados_validos = ['Pendiente', 'Cancelado', 'Finalizado']
    if nuevo_estado in estados_validos:
        turno.estado = nuevo_estado
        db.session.commit()
    return redirect(url_for('turnos'))

@app.route('/eliminar_turno_finalizado/<int:turno_id>', methods=['POST'])
def eliminar_turno_finalizado(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    if turno.estado == 'Finalizado':
        db.session.delete(turno)
        db.session.commit()
    return redirect(url_for('turnos', ver_finalizados=1))

@app.route('/eliminar_paciente/<int:paciente_id>', methods=['POST'])
def eliminar_paciente(paciente_id):
    paciente = Paciente.query.get_or_404(paciente_id)
    # Eliminar primero los turnos asociados
    Turno.query.filter_by(paciente_id=paciente_id).delete()
    db.session.delete(paciente)
    db.session.commit()
    return redirect(url_for('pacientes'))

@app.route('/editar_paciente/<int:paciente_id>', methods=['POST'])
def editar_paciente(paciente_id):
    paciente = Paciente.query.get_or_404(paciente_id)
    paciente.dni = request.form['dni']
    paciente.nombre = request.form['nombre']
    paciente.telefono = request.form['telefono']
    paciente.domicilio = request.form['domicilio']
    paciente.obra_social = request.form['obra_social']
    paciente.nota = request.form['nota']
    db.session.commit()
    return redirect(url_for('pacientes'))

@app.route('/api/dias_disponibles', methods=['GET'])
def dias_disponibles():
    # Simulación de días disponibles (puedes reemplazar con lógica de tu base de datos)
    dias_disponibles = [
        (datetime.now().date() + timedelta(days=i)).isoformat()
        for i in range(1, 15) if i % 2 == 0  # Ejemplo: días pares disponibles
    ]
    return jsonify(dias_disponibles)

@app.route('/api/horarios_disponibles/<string:fecha>', methods=['GET'])
def horarios_disponibles(fecha):
    fecha_obj = datetime.strptime(fecha, '%Y-%m-%d').date()
    turnos_existentes = Turno.query.filter_by(fecha=fecha_obj).all()
    horarios_ocupados = {turno.hora.strftime('%H:%M') for turno in turnos_existentes}

    # Generar horarios disponibles (8:00-13:00 y 16:00-20:00) con intervalos de 20 minutos
    horarios = []
    for hora in range(8, 13):  # Mañana
        for minuto in range(0, 60, 20):
            horario = f"{hora:02}:{minuto:02}"
            if horario not in horarios_ocupados:
                horarios.append(horario)

    for hora in range(16, 20):  # Tarde
        for minuto in range(0, 60, 20):
            horario = f"{hora:02}:{minuto:02}"
            if horario not in horarios_ocupados:
                horarios.append(horario)

    # Contar turnos ocupados y disponibles
    total_turnos = len(horarios) + len(horarios_ocupados)
    turnos_ocupados = len(horarios_ocupados)
    turnos_disponibles = len(horarios)

    return jsonify({
        'horarios': horarios,
        'turnos_ocupados': turnos_ocupados,
        'turnos_disponibles': turnos_disponibles,
        'total_turnos': total_turnos
    })


@app.route('/pacientes/<int:id>/adjuntar_archivo', methods=['POST'])
def adjuntar_archivo(id):
    paciente = Paciente.query.get_or_404(id)
    if 'archivo' in request.files:
        archivo = request.files['archivo']
        if archivo.filename != '':
            filename = secure_filename(archivo.filename)
            ruta = os.path.join('static/informes', filename)
            archivo.save(ruta)
            #paciente.informe = ruta
            paciente.informe = filename

            db.session.commit()
    return redirect(url_for('pacientes'))

@app.route('/turnos_diarios_pdf')
def turnos_diarios_pdf():
    from datetime import date
    turnos = Turno.query.filter_by(fecha=date.today()).order_by(Turno.hora.asc()).all()

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, height - 50, "Turnos del día")

    y = height - 80
    p.setFont("Helvetica", 12)
    for turno in turnos:
        texto = f"{turno.hora.strftime('%H:%M')} - {turno.paciente.nombre} ({turno.paciente.obra_social})"
        p.drawString(50, y, texto)
        y -= 20
        if y < 50:
            p.showPage()
            y = height - 50

    p.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=False, download_name="turnos_diarios.pdf", mimetype='application/pdf')

TELEGRAM_TOKEN = '7665763175:AAHkH8FBtz9T4X8tk4DgSydtXouwWVIG5r0'
TELEGRAM_CHAT_ID = '290714995'

@app.route('/enviar_turnos_pdf_telegram')
def enviar_turnos_pdf_telegram():
    from datetime import date
    
    turnos = Turno.query.filter_by(fecha=date.today()).order_by(Turno.hora.asc()).all()
    cantidad_turnos = len(turnos)

    if not turnos:
        mensaje = "No hay turnos para hoy."
    else:
        mensaje = f"📅 Turnos del día {date.today().strftime('%d/%m/%Y')}:\n"
        mensaje += f"📊 Total de pacientes: {cantidad_turnos}\n\n"
        for turno in turnos:
            mensaje += f"🕒 {turno.hora.strftime('%H:%M')} - "
            mensaje += f"👤 {turno.paciente.nombre}\n"
            mensaje += f"🏥 Obra Social: {turno.paciente.obra_social}\n"
            #mensaje += f"📋 Estado: {turno.estado}\n"
            mensaje += "-------------------\n"

    async def send_message():
        try:
            bot = telegram.Bot(token=TELEGRAM_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=mensaje)
            return True
        except telegram.error.Unauthorized:
            return "Error: Token del bot no válido"
        except telegram.error.BadRequest as e:
            return f"Error: Solicitud incorrecta - {str(e)}"
        except Exception as e:
            return f"Error inesperado: {str(e)}"
                
    result = asyncio.run(send_message())
    if result is True:
        return redirect(url_for('turnos', telegram_success='1'))
    return result

@app.route('/cancelar_turno_hoy/<int:turno_id>', methods=['POST'])
def cancelar_turno_hoy(turno_id):
    turno = Turno.query.get_or_404(turno_id)
    # Solo mover si el turno es de hoy
    if turno.fecha == date.today():
        turno.fecha = date.today() + timedelta(days=1)
        turno.estado = 'Pendiente'  # Opcional: puedes dejar el estado igual o ponerlo en Pendiente
        db.session.commit()
    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    #app.run(debug=True)
    app.run(host='0.0.0.0', port=5000)








