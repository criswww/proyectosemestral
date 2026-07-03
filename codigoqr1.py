#librerias
import ipaddress
import uuid
import json
import os
from datetime import datetime
import qrcode
from flask import Flask, render_template, request, abort, redirect, url_for, session

#para que flask guarde los datos
app = Flask(__name__)
app.secret_key = "clave_secreta_123"

#ips que pueden entrar en la pagina
REDES_PERMITIDAS = [
    ipaddress.ip_network("192.168.137.0/24"),
    ipaddress.ip_network("127.0.0.1/32"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("192.168.1.0/24"),
    ipaddress.ip_network("10.0.136.0/22"),
    ipaddress.ip_network("172.20.10.0/28"),
]

#revisa que la ip esta en la whitelist
def ip_permitida(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in red for red in REDES_PERMITIDAS)

#se ejecuta antes de cada sol. q llega
@app.before_request
def limitar_por_red():
    if request.endpoint == "static":
        return
    #las clases online no necesitan estar en la red
    if request.endpoint == "asistir_online":
        return
    if not ip_permitida(request.remote_addr):
        abort(403)

#acceso denegado:
@app.errorhandler(403)
def acceso_denegado(error):
    return render_template("access_denied.html"), 403

#usuario prueba
USERS = {
    "profesor@ulagos.cl":   {"password": "1234", "rol": "docente"},
    "estudiante@ulagos.cl": {"password": "1234", "rol": "estudiante"},
}

#base de datos
BASE_DATOS = os.path.join(os.path.dirname(__file__), "Base_Datos")

#lee y devuelve el json
def leer_json(nombre):
    ruta = os.path.join(BASE_DATOS, nombre)
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)

#guarda los cambios en el json
def guardar_json(nombre, datos):
    ruta = os.path.join(BASE_DATOS, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=4)


#LOGIN

@app.route("/", methods=["GET", "POST"])
def login():
    message = ""
    message_color = "gray"

    if request.method == "POST":
        #seobtiene datos ingresados por el usuario
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        #verifica

        if email in USERS and USERS[email]["password"] == password:
            session["email"] = email
            session["rol"]   = USERS[email]["rol"]

            #revisa si habia un codigo de asistencia pendiente

            codigo_pendiente = session.pop("codigo_pendiente", None)
            tipo_pendiente   = session.pop("tipo_pendiente", "presencial")

            #si el estudiante entro desde un codigo lo envia directamente a registrar asistencia
            if codigo_pendiente and session["rol"] == "estudiante":
                if tipo_pendiente == "online":
                    return redirect(url_for("asistir_online", codigo=codigo_pendiente))
                return redirect(url_for("asistir", codigo=codigo_pendiente))
            #si no hay codigo se redirige panel principal

            return redirect(url_for("panel"))
        else:
            #credenciales incorrectas
            message = "Correo o contraseña incorrectos"
            message_color = "red"

    return render_template("login.html", message=message, message_color=message_color)

@app.route("/forgot-password", methods=["POST"])
def forgot_password():
    #simula el envio del enlace de recuperacion
    message = "Enlace de recuperación enviado a su correo institucional."
    return render_template("login.html", message=message, message_color="gray")

@app.route("/logout")
def logout():
    #cierra la sesion del usuario
    session.clear()
    return redirect(url_for("login"))


#PANEL principal

@app.route("/panel")
def panel():
    #verifica que el usuario haya iniciado sesion
    if "email" not in session:
        return redirect(url_for("login"))
    #muestra el panel principal
    return render_template(
        "base.html",
        usuario=session["email"],
        correo=session["email"],
        nombre_clase="Programación Web",
        rol=session["rol"],
    )


#generar qr presencial y online

@app.route("/qr", methods=["GET", "POST"])
def generar_qr():
    #verifica que el usuario sea docente
    if "email" not in session:
        return redirect(url_for("login"))
    if session["rol"] != "docente":
        abort(403)

    #obtiene los datos del formulario
    modalidad    = request.form.get("modalidad", "presencial")
    codigo_clase = request.form.get("codigo_clase", "").strip().upper()

    if modalidad == "online" and not codigo_clase:
        return redirect(url_for("panel"))

    if modalidad != "online":
        codigo_clase = None

    #genera un codigo unico para el qr
    codigo = str(uuid.uuid4())

    if modalidad == "online":
        url_asistencia = f"http://{request.host}/asistir-online/{codigo}"
    else:
        url_asistencia = f"http://{request.host}/asistir/{codigo}"

    registros_qr = leer_json("qr.json")
    #desactiva los qr anteriores
    for q in registros_qr:
        q["activo"] = False

    nuevo = {
        "id": len(registros_qr) + 1,
        "clase_id": 1,
        "codigo": codigo,
        "modalidad": modalidad,
        "activo": True,
        "generado_en": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    if codigo_clase:
        nuevo["codigo_clase"] = codigo_clase

    registros_qr.append(nuevo)
    guardar_json("qr.json", registros_qr)

    #guarda la imagen del qr
    img = qrcode.make(url_asistencia)
    img.save(os.path.join(os.path.dirname(__file__), "static", "qr.png"))

    return redirect(url_for("ver_qr"))

@app.route("/ver-qr")
def ver_qr():
    if "email" not in session or session["rol"] != "docente":
        return redirect(url_for("login"))

    #leer el qr activo para mostrar la modalidad en la vista
    registros_qr = leer_json("qr.json")
    qr_activo = next((q for q in registros_qr if q.get("activo")), None)

    return render_template("ver_qr.html",
                           usuario=session["email"],
                           rol=session["rol"],
                           qr_activo=qr_activo)


#ASISTENCIAS

@app.route("/asistencias")
def asistencias():
    #solo el docente puede ver las asistencias
    if "email" not in session or session["rol"] != "docente":
        return redirect(url_for("login"))

    #lee los registros de asistencia
    registros       = leer_json("asistencia.json")
    alumnos         = leer_json("alumnos.json")
    #calcula el resumen
    total_inscritos = len(alumnos)
    total_presentes = len(registros)
    total_ausentes  = total_inscritos - total_presentes

    return render_template(
        "asistencias.html",
        usuario=session["email"],
        rol=session["rol"],
        registros=registros,
        total_inscritos=total_inscritos,
        total_presentes=total_presentes,
        total_ausentes=total_ausentes,
    )


#REGISTRAR ASISTENCIA PRESENCIAL

@app.route("/asistir/<codigo>")
def asistir(codigo):
    if "email" not in session:
        session["codigo_pendiente"] = codigo
        session["tipo_pendiente"]   = "presencial"
        return redirect(url_for("login"))

    if session["rol"] != "estudiante":
        return render_template("resultado_asistencia.html",
                               mensaje="Esta acción es solo para estudiantes.",
                               color="red",
                               usuario=session["email"],
                               rol=session["rol"])

    email_est    = session["email"]
    registros_qr = leer_json("qr.json")
    qr_encontrado = next(
        (q for q in registros_qr if q.get("activo") and q.get("codigo") == codigo),
        None
    )

    if not qr_encontrado:
        return render_template("resultado_asistencia.html",
                               mensaje="El código QR no es válido o ya expiró.",
                               color="red",
                               usuario=email_est,
                               rol=session["rol"])

    return _guardar_asistencia(email_est, qr_encontrado)


#REGISTRAR ASISTENCIA ONLINE

@app.route("/asistir-online/<codigo>", methods=["GET", "POST"])
def asistir_online(codigo):
    #esta ruta no requiere validacion por red
    if "email" not in session:
        session["codigo_pendiente"] = codigo
        session["tipo_pendiente"]   = "online"
        return redirect(url_for("login"))

    if session["rol"] != "estudiante":
        return render_template("resultado_asistencia.html",
                               mensaje="Esta acción es solo para estudiantes.",
                               color="red",
                               usuario=session["email"],
                               rol=session["rol"])

    email_est    = session["email"]
    registros_qr = leer_json("qr.json")
    qr_encontrado = next(
        (q for q in registros_qr if q.get("activo") and q.get("codigo") == codigo),
        None
    )

    if not qr_encontrado:
        return render_template("resultado_asistencia.html",
                               mensaje="El código QR no es válido o ya expiró.",
                               color="red",
                               usuario=email_est,
                               rol=session["rol"])

    #revisar si ya registro antes de mostrar el formulario
    lista = leer_json("asistencia.json")
    #verifica si el estudiante ya registro asistencia
    ya_registrado = any(
        a.get("correo_estudiante") == email_est and a.get("clase_id") == qr_encontrado["clase_id"]
        for a in lista
    )
    if ya_registrado:
        return render_template("resultado_asistencia.html",
                               mensaje="Ya tienes asistencia registrada en esta clase.",
                               color="orange",
                               usuario=email_est,
                               rol=session["rol"])

    if request.method == "POST":
        codigo_ingresado = request.form.get("codigo_clase", "").strip().upper()
        if codigo_ingresado == qr_encontrado.get("codigo_clase", ""):
            return _guardar_asistencia(email_est, qr_encontrado)
        else:
            return render_template("codigo_online.html",
                                   codigo=codigo,
                                   usuario=email_est,
                                   rol=session["rol"],
                                   error="Código incorrecto. Inténtalo de nuevo.")

    #mostrar el formulario para ingresar el codigo de clase
    return render_template("codigo_online.html",
                           codigo=codigo,
                           usuario=email_est,
                           rol=session["rol"],
                           error=None)


#guarda la asistencia del estudiante
def _guardar_asistencia(email_est, qr):
    clase_id = qr["clase_id"]
    lista    = leer_json("asistencia.json")

    ya_registrado = any(
        a.get("correo_estudiante") == email_est and a.get("clase_id") == clase_id
        for a in lista
    )
    if ya_registrado:
        return render_template("resultado_asistencia.html",
                               mensaje="Ya tienes asistencia registrada en esta clase.",
                               color="orange",
                               usuario=email_est,
                               rol=session.get("rol", "estudiante"))

    #agrega la asistencia al registro
    lista.append({
        "id": len(lista) + 1,
        "correo_estudiante": email_est,
        "clase_id": clase_id,
        "modalidad": qr.get("modalidad", "presencial"),
        "hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    guardar_json("asistencia.json", lista)

    return render_template("resultado_asistencia.html",
                           mensaje="Asistencia registrada correctamente.",
                           color="green",
                           usuario=email_est,
                           rol=session.get("rol", "estudiante"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
