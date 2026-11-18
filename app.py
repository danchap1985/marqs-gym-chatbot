import os
import re
import logging
import requests
import unicodedata
from pathlib import Path
from flask import Flask, request, jsonify

app = Flask(__name__)

# Consola compacta: ocultamos los accesos HTTP repetitivos de Flask/Werkzeug.
# Los eventos importantes se imprimen con mensajes claros más abajo.
logging.getLogger("werkzeug").setLevel(logging.ERROR)

def log_evento(tipo, detalle=""):
    hora = __import__("datetime").datetime.now().strftime("%H:%M:%S")
    linea = f"[{hora}] {tipo}"
    if detalle:
        linea += f" | {detalle}"
    print(linea, flush=True)

# =========================================================
# CONFIGURACION
# =========================================================
# Seguridad:
# - Revoca el token que estaba en el archivo anterior, porque quedó expuesto.
# - Guarda el token nuevo en la variable de entorno WHATSAPP_TOKEN.
# - No compartas el token en capturas ni mensajes.
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1396723693530591")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "daniel_2026_bot")

GYM_NAME = "MARQ'S GYM & SPORTS"
GYM_ADDRESS = (
    "C. Tierra Colorada 29, Tierra Nueva, Azcapotzalco, "
    "02130, Ciudad de México, CDMX"
)
# Coordenadas del gym (verificadas por geocodificación de la dirección):
# 19.5002, -99.20119. WhatsApp no permite poner una imagen/logo en el pin
# de ubicación: el pin es el estándar de mapas, con nombre y dirección.
GYM_LATITUDE = 19.5002
GYM_LONGITUDE = -99.20119
GYM_MAPS_URL = "https://www.google.com/maps/search/?api=1&query=19.5002,-99.20119"
# Redes sociales del gym (links que el usuario puede tocar en WhatsApp).
GYM_INSTAGRAM = "https://www.instagram.com/marqsgymsports/"
GYM_FACEBOOK = "https://www.facebook.com/marqsgymsports"

# Este número queda editable para la futura administración por WhatsApp.
# Se guarda sin + ni espacios. Para agregar/cambiarlo, edita esta lista.
# En esta primera demo todavía NO hay comandos administrativos activos.
ASSETS_DIR = Path(__file__).resolve().parent / "marqs_assets"
# Cambia este nombre para elegir qué imagen aparece al abrir el menú principal.
# El archivo debe estar dentro de la carpeta marqs_assets.
IMAGEN_MENU = "logo.jpg"  # Cambia aquí la imagen inicial si quieres probar otra
MEDIA_IDS = {}  # cache en memoria de IDs de imágenes subidas a Meta

ADMIN_NUMBERS = {
    n.strip() for n in os.environ.get("ADMIN_NUMBERS", "").split(",") if n.strip()
}  # Números autorizados para la futura versión admin, solo por variable de entorno (nunca en el código público).

# Horario DEMO transcrito de la imagen compartida.
# Como el gimnasio indica que los horarios pueden cambiar, el bot debe
# aclarar que se trata del horario de referencia y confirmar cambios en recepción.
# Formato: día, hora inicial, hora final, nombre de clase.
CLASS_SCHEDULE = [
    ("lunes", "08:30", "10:30", "Box"),
    ("martes", "08:30", "10:30", "Box"),
    ("miercoles", "08:30", "10:30", "Box"),
    ("jueves", "08:30", "10:30", "Box"),
    ("viernes", "08:30", "10:30", "Box"),

    ("miercoles", "09:30", "11:30", "Kickboxing"),
    ("viernes", "09:30", "11:30", "Kickboxing"),
    ("jueves", "10:00", "11:30", "Pole Dance"),
    ("sabado", "09:00", "10:30", "Pole Dance"),
    ("sabado", "13:00", "14:00", "Taekwondo"),

    ("lunes", "17:00", "18:00", "P Dance Kids"),

    ("lunes", "18:00", "19:00", "GAP/Box"),
    ("martes", "18:00", "19:00", "Aerobox/Box"),
    ("miercoles", "18:00", "19:00", "Movility/Box"),
    ("jueves", "18:00", "19:00", "RYABD/Box"),
    ("viernes", "18:00", "19:00", "Strong/Box"),

    ("lunes", "19:00", "20:00", "Zumba/Box"),
    ("martes", "19:00", "20:00", "Tabata/Box"),
    ("miercoles", "19:00", "20:00", "Strong/Box"),
    ("jueves", "19:00", "20:00", "Step/Box"),
    ("viernes", "19:00", "20:00", "Box"),

    ("lunes", "20:00", "21:30", "Pole Dance"),
    ("martes", "20:00", "21:30", "Taekwondo y Pole Dance"),
    ("miercoles", "20:00", "21:30", "Pole Dance"),
    ("jueves", "20:00", "21:30", "Taekwondo y Pole Dance"),
    ("viernes", "20:00", "21:30", "Taekwondo"),

    ("lunes", "20:00", "22:00", "Kickboxing"),
    ("miercoles", "20:00", "22:00", "Kickboxing"),
]

DAY_LABELS = {
    "lunes": "Lunes",
    "martes": "Martes",
    "miercoles": "Miércoles",
    "jueves": "Jueves",
    "viernes": "Viernes",
    "sabado": "Sábado",
    "domingo": "Domingo",
}
DAY_ORDER = {day: i for i, day in enumerate(DAY_LABELS)}

def normalizar_numero(to):
    numero = str(to or "").strip().replace("+", "").replace(" ", "").replace("-", "")
    # Ajuste que ya funcionó en este proyecto para los wa_id mexicanos con 521.
    if numero.startswith("521") and len(numero) == 13:
        numero = "52" + numero[3:]
    return numero

def normalizar_texto(texto):
    texto = str(texto or "").lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[¿?¡!.,;:]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()

# Conversación temporal por número. Se reinicia si se detiene el programa.
ESTADOS_CONVERSACION = {}
ACTIVIDAD_SELECCIONADA = {}  # actividad elegida por cada usuario durante la demo

# Deduplicación por ID de mensaje de WhatsApp (wamid): Meta a veces reenvía
# el mismo mensaje y sin esto el bot lo contesta otra vez como si fuera nuevo
# (eso explica los "hola" repetidos del 2026-11-15). Solo vive en memoria:
# se reinicia si el servicio se detiene o se duerme (Render Free), así que un
# reenvío después de un reinicio podría contestarse una vez más. Se guardan
# como máximo los últimos 500 IDs.
from collections import deque as _deque

_MENSAJES_PROCESADOS = set()
_MENSAJES_ORDEN = _deque()


def _mensaje_ya_procesado(mensaje_id):
    """True si este wamid ya se contestó; si es nuevo, lo registra y da False.
    Los mensajes sin ID no se deduplican (compatibilidad con pruebas viejas)."""
    if not mensaje_id:
        return False
    if mensaje_id in _MENSAJES_PROCESADOS:
        return True
    _MENSAJES_PROCESADOS.add(mensaje_id)
    _MENSAJES_ORDEN.append(mensaje_id)
    while len(_MENSAJES_PROCESADOS) > 500:
        _MENSAJES_PROCESADOS.discard(_MENSAJES_ORDEN.popleft())
    return False


def menu_principal():
    return (
        f"💚 *{GYM_NAME}*\n\n"
        "¡Entrena con energía, aprende la técnica y alcanza tus objetivos! 💪\n\n"
        "¿Qué te gustaría consultar?\n\n"
        "1️⃣ *Clases y horarios*\n"
        "2️⃣ *Membresías y precios*\n"
        "3️⃣ *Ver promociones* 🎉\n"
        "4️⃣ *Ubicación* 📍\n"
        "5️⃣ *Ayúdame a elegir una clase*\n\n"
        "Escribe el número o cuéntame qué necesitas."
    )


def menu_seguimiento():
    return (
        "¿Qué te gustaría hacer ahora? 😊\n"
        "1️⃣ Ver otro día de clases\n"
        "2️⃣ Preguntar por una actividad\n"
        "3️⃣ Consultar precios e inscripción\n"
        "4️⃣ Ver ubicación\n"
        "5️⃣ Volver al menú principal\n\n"
        "Puedes escribir tu pregunta directamente."
    )


def horario_completo():
    lines = ["📅 *Horario semanal de clases — DEMO*"]
    for day in DAY_LABELS:
        items = sorted(
            [row for row in CLASS_SCHEDULE if row[0] == day],
            key=lambda row: (row[1], row[2], row[3])
        )
        if items:
            lines.append(f"\n*{DAY_LABELS[day]}*")
            for _, start, end, name in items:
                lines.append(f"• {start}–{end}: {name}")
    lines.append(
        "\n⚠️ Este es un horario de referencia transcrito de la imagen compartida; "
        "puede contener cambios. Confirma el horario vigente con recepción antes de asistir.\n\n"
        + menu_seguimiento()
    )
    return "\n".join(lines)


def horario_dia(day):
    items = sorted(
        [row for row in CLASS_SCHEDULE if row[0] == day],
        key=lambda row: (row[1], row[2], row[3])
    )
    if not items:
        return (
            f"📅 No tengo clases registradas para {DAY_LABELS.get(day, day)} en este horario de referencia.\n"
            "Los horarios pueden cambiar; confirma con recepción.\n\n" + menu_seguimiento()
        )
    lines = [f"📅 *Clases del {DAY_LABELS[day]} — DEMO*"]
    for _, start, end, name in items:
        lines.append(f"• {start}–{end}: {name}")
    lines.append(
        "\n⚠️ Horario de referencia; puede cambiar. Confirma con recepción antes de asistir.\n\n"
        + menu_seguimiento()
    )
    return "\n".join(lines)


def detectar_dia(texto):
    # normalizar_texto ya elimina acentos; acepta frases como "el miercoles".
    aliases = {
        "lunes": "lunes", "martes": "martes", "miercoles": "miercoles",
        "jueves": "jueves", "viernes": "viernes", "sabado": "sabado",
        "domingo": "domingo",
    }
    for palabra, dia in aliases.items():
        if re.search(rf"\b{palabra}\b", texto):
            return dia
    return None


def parece_pregunta_clases(texto):
    terminos = (
        "clase", "clases", "horario", "horarios", "actividad", "actividades",
        "entrenar", "entrenamiento", "que hay", "que tienen", "que puedo hacer",
        "hay box", "hay pole", "hay zumba", "kickboxing", "pole dance", "taekwondo",
        "zumba", "tabata", "aerobox", "strong", "p dance", "movility", "step", "gap", "box"
    )
    return any(term in texto for term in terminos)



def detectar_actividad(texto):
    """Detecta actividades mencionadas por el usuario; devuelve términos de búsqueda."""
    aliases = {
        "kickboxing": ("kickboxing",),
        "pole dance": ("pole dance", "pole"),
        "taekwondo": ("taekwondo", "tae kwon do"),
        "zumba": ("zumba",),
        "tabata": ("tabata",),
        "aerobox": ("aerobox", "aero box"),
        "box": ("box", "boxeo"),
        "strong": ("strong",),
        "step": ("step",),
        "gap": ("gap",),
        "movility": ("movility", "movilidad"),
        "p dance kids": ("p dance kids", "pole kids", "baile kids"),
        "baile": ("baile", "bailar", "danza"),
    }
    # Prioriza nombres compuestos para que "pole dance" no se tome como otra cosa.
    for actividad in ("pole dance", "p dance kids", "kickboxing", "taekwondo", "aerobox", "movility", "zumba", "tabata", "strong", "step", "gap", "box", "baile"):
        if any(alias in texto for alias in aliases[actividad]):
            return actividad
    return None


def filtrar_por_hora(items, texto):
    """Filtra clases por franja o por una hora mencionada."""
    if any(x in texto for x in ("por la manana", "en la manana", "por la mañana")):
        return [r for r in items if int(r[1][:2]) < 12], "por la mañana"
    if any(x in texto for x in ("por la tarde", "en la tarde")):
        return [r for r in items if 12 <= int(r[1][:2]) < 20], "por la tarde"
    if any(x in texto for x in ("por la noche", "en la noche", "noche")):
        return [r for r in items if int(r[1][:2]) >= 19], "por la noche"

    # Ejemplos aceptados: "despues de las siete", "después de las 7", "a las 19:00".
    m = re.search(r"\bdespues de las?\s+(\d{1,2})(?::(\d{2}))?", texto)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        # Si dice 1 a 6 sin indicar formato de 24 h, se interpreta como tarde/noche.
        if hh <= 6:
            hh += 12
        target = hh * 60 + mm
        return [r for r in items if int(r[1][:2]) * 60 + int(r[1][3:]) >= target], f"después de las {m.group(1)}"

    m = re.search(r"\b(?:a las?|tipo)\s+(\d{1,2})(?::(\d{2}))?", texto)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        if hh <= 6:
            hh += 12
        target = hh * 60 + mm
        def incluye_hora(r):
            inicio = int(r[1][:2]) * 60 + int(r[1][3:])
            fin = int(r[2][:2]) * 60 + int(r[2][3:])
            return inicio <= target < fin
        return [r for r in items if incluye_hora(r)], f"alrededor de las {m.group(1)}"
    return items, ""


def respuesta_busqueda_clases(texto, day=None):
    """Consulta por actividad, día y/o hora sin exigir una frase exacta."""
    actividad = detectar_actividad(texto)
    items = [r for r in CLASS_SCHEDULE if not day or r[0] == day]
    if actividad:
        if actividad == "baile":
            items = [r for r in items if any(x in normalizar_texto(r[3]) for x in ("zumba", "pole dance", "p dance"))]
        elif actividad == "box":
            items = [r for r in items if "box" in normalizar_texto(r[3]) or "kickboxing" in normalizar_texto(r[3])]
        elif actividad == "p dance kids":
            items = [r for r in items if "p dance kids" in normalizar_texto(r[3])]
        else:
            items = [r for r in items if actividad in normalizar_texto(r[3])]

    items, filtro_hora = filtrar_por_hora(items, texto)
    if not items:
        foco = actividad.title() if actividad else "clases"
        dia_txt = f" del {DAY_LABELS[day]}" if day else ""
        hora_txt = f" {filtro_hora}" if filtro_hora else " en ese horario"
        return (f"No encuentro {foco}{dia_txt}{hora_txt} en el horario de referencia que tengo. 🙏\n"
                "Podemos revisar otro día u horario, o confirmar con recepción.\n\n" + menu_seguimiento())

    titulo = "🔎 *Opciones encontradas"
    if actividad:
        titulo += f" de {actividad.title()}"
    if day:
        titulo += f" del {DAY_LABELS[day]}"
    if filtro_hora:
        titulo += f" {filtro_hora}"
    lines = [titulo + " — DEMO*" ]
    for d, start, end, name in sorted(items, key=lambda r: (DAY_ORDER.get(r[0], 99), r[1], r[3])):
        etiqueta_dia = f"{DAY_LABELS[d]} " if not day else ""
        lines.append(f"• {etiqueta_dia}{start}–{end}: {name}")
    lines.append("\n⚠️ Horario de referencia; puede cambiar. Confirma con recepción antes de asistir.\n\n" + menu_seguimiento())
    return "\n".join(lines)

def respuesta_promociones():
    ESTADO = "promociones"
    return (
        "🎉 *¡Promociones de MARQ'S GYM & SPORTS!*\n\n"
        "Te comparto las promociones de referencia que tenemos disponibles. "
        "Confirma vigencia, requisitos y disponibilidad directamente en recepción.\n\n"
        "1️⃣ Planes de membresía\n"
        "2️⃣ Promoción para estudiantes\n"
        "3️⃣ Renueva y comparte\n"
        "4️⃣ Promoción de yoga\n"
        "5️⃣ Ver todas las promociones\n"
        "6️⃣ Volver al menú\n\n"
        "Escribe el número de la promoción que quieres ver."
    )

def responder(mensaje, remitente=""):
    texto = normalizar_texto(mensaje)
    estado = ESTADOS_CONVERSACION.get(remitente, "inicio")

    # Menú de promociones: las palabras van a promos en cualquier estado,
    # pero el número 3 solo es "promociones" en el menú principal (inicio/menu).
    # En seguimiento, 3 = precios; en promociones, 3 = ver la 3a promo.
    if texto in {"promociones", "ver promociones", "promo", "promos", "ofertas"} and estado in {"inicio", "menu", "seguimiento", "promociones"}:
        ESTADOS_CONVERSACION[remitente] = "promociones"
        return respuesta_promociones()
    if texto == "3" and estado in {"inicio", "menu"}:
        ESTADOS_CONVERSACION[remitente] = "promociones"
        return respuesta_promociones()

    if estado == "promociones" and texto in {"1", "2", "3", "4", "5"}:
        ESTADOS_CONVERSACION[remitente] = "promociones"
        claves = {"1": "promo_membresias.jpg", "2": "promo_estudiantes.jpg", "3": "promo_renueva_comparte.jpg", "4": "promo_yoga.jpg"}
        if texto == "5":
            return "PROMO_TODAS"
        return "PROMO_IMAGEN:" + claves[texto]

    if estado == "promociones" and texto in {"6", "menu", "inicio", "volver"}:
        ESTADOS_CONVERSACION[remitente] = "menu"
        return menu_principal()

    saludos = {"hola", "holi", "buenas", "buenos dias", "buenas tardes", "buenas noches", "menu", "inicio", "ayuda", "opciones", "0"}
    if texto in saludos or any(texto.startswith(s + " ") for s in ("hola", "buenas tardes", "buenos dias")):
        ESTADOS_CONVERSACION[remitente] = "menu"
        return menu_principal()

    if texto in {"volver", "menu principal", "regresar"}:
        ESTADOS_CONVERSACION[remitente] = "menu"
        return menu_principal()

    # Las opciones numéricas dependen del menú que el cliente está viendo.
    # En frío ("inicio") valen las del menú principal: 2 precios, 4 ubicación,
    # 5 ayúdame a elegir. El 5 en seguimiento vuelve al menú principal.
    if estado in {"menu", "inicio"} and texto in {"2", "4", "5"}:
        if texto == "2":
            texto = "precios"
        elif texto == "4":
            texto = "ubicacion"
        elif texto == "5":
            texto = "ayudame a elegir"
    elif estado == "seguimiento" and texto in {"2", "3", "4", "5"}:
        if texto == "2":
            texto = "preguntar por una actividad"
        elif texto == "3":
            texto = "precios"
        elif texto == "4":
            texto = "ubicacion"
        elif texto == "5":
            ESTADOS_CONVERSACION[remitente] = "menu"
            return menu_principal()

    # Opciones numéricas: se interpretan según el contexto y también desde el menú.
    if texto == "1" and estado in {"inicio", "menu", "seguimiento", "esperando_dia"}:
        ESTADOS_CONVERSACION[remitente] = "esperando_dia"
        return (
            "¡Vamos a encontrar un horario para ti! 📅\n"
            "¿Qué día te interesa? Puedes escribir lunes, martes, miércoles, jueves, viernes o sábado.\n\n"
            "Si prefieres, escribe *todos* para ver el horario semanal."
        )
    if texto in {"todos", "toda la semana", "horario completo", "semana completa"}:
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        return horario_completo()

    day = detectar_dia(texto)
    actividad = detectar_actividad(texto)
    menciona_hora = any(x in texto for x in ("manana", "tarde", "noche", "despues de las", "a las", "tipo")) or bool(re.search(r"\b\d{1,2}:\d{2}\b", texto))

    # Búsqueda más específica primero: actividad + día y/o hora.
    if actividad and (day or menciona_hora):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        return respuesta_busqueda_clases(texto, day)

    if day and (parece_pregunta_clases(texto) or estado == "esperando_dia" or texto == day):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        if menciona_hora:
            return respuesta_busqueda_clases(texto, day)
        return horario_dia(day)

    # Si pregunta por una actividad sin especificar día, muestra sus horarios semanales.
    if actividad and parece_pregunta_clases(texto):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        return respuesta_busqueda_clases(texto)

    # Si solo indica una franja u hora, se muestran las clases que coinciden.
    if menciona_hora and parece_pregunta_clases(texto):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        return respuesta_busqueda_clases(texto, day)

    if texto in {"redes", "redes sociales", "instagram", "facebook", "siguenos", "síguenos", "sigueme", "sígueme"} or any(
        phrase in texto for phrase in ("redes sociales", "tu instagram", "tu facebook", "donde los sigo")
    ):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        return (
            f"💚 ¡Síguenos en nuestras redes! 📲\n\n"
            f"Instagram: {GYM_INSTAGRAM}\n"
            f"Facebook: {GYM_FACEBOOK}\n\n"
            + menu_seguimiento()
        )

    if texto in {"2", "direccion", "ubicacion", "mapa", "como llego", "donde estan", "donde queda", "donde se ubican"} or any(
        phrase in texto for phrase in ("direccion del gimnasio", "ubicacion del gimnasio", "como llego al gimnasio", "donde esta el gimnasio", "donde queda el gym", "comparteme la ubicacion")
    ):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        # Centinela: receive_message() lo convierte en pin de ubicación
        # nativo de WhatsApp (enviar_ubicacion) + lista de seguimiento.
        return "UBICACION_PIN"

    if texto in {"3", "mensualidad", "mensualidades", "pagos", "4", "precio", "precios", "tarifas", "costos", "inscripcion", "inscripciones"} or any(
        phrase in texto for phrase in ("cuanto cuesta", "cuanto cobran", "precio de la mensualidad", "costo de inscripcion", "formas de pago", "como me inscribo", "quiero inscribirme", "cuanto es la mensualidad", "cuanto cuesta entrenar")
    ):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        return (
            "💚 ¡Con gusto te orientamos! Para no darte un precio desactualizado, "
            "las tarifas y condiciones de inscripción deben confirmarse con recepción.\n\n"
            "¿Quieres consultar algo más?\n"
            "1️⃣ Ver clases y horarios\n"
            "2️⃣ Ver ubicación\n"
            "3️⃣ Volver al menú principal\n\n"
            "Escribe *menu* para ver todas las opciones."
        )

    if texto == "preguntar por una actividad":
        ESTADOS_CONVERSACION[remitente] = "orientacion"
        return (
            "¿Qué actividad te interesa? 🥊\n"
            "Puedes escribir Box, Kickboxing, Zumba, Pole Dance, Taekwondo u otra clase.\n\n"
            "Si me dices el día, también te muestro el horario de referencia."
        )

    # Orientación guiada sin afirmar recomendaciones médicas ni inventar detalles.
    if texto == "4" or any(term in texto for term in ("ayudame a elegir", "que clase me recomiendas", "recomiendame una clase", "soy principiante", "quiero empezar", "quiero bajar de peso", "quiero aprender defensa", "quiero bailar")):
        ESTADOS_CONVERSACION[remitente] = "orientacion"
        return (
            "¡Claro! 💚 Para orientarte mejor, dime qué te interesa más:\n"
            "1️⃣ Box o kickboxing\n"
            "2️⃣ Baile y acondicionamiento\n"
            "3️⃣ Pole Dance\n"
            "4️⃣ Taekwondo\n"
            "5️⃣ Aún no estoy seguro/a\n\n"
            "Después podemos revisar los días y horarios de referencia."
        )

    if estado == "orientacion" and texto in {"1", "2", "3", "4", "5"}:
        interes = {
            "1": "Box o kickboxing", "2": "baile y acondicionamiento",
            "3": "Pole Dance", "4": "Taekwondo", "5": "explorar las opciones"
        }[texto]
        ESTADOS_CONVERSACION[remitente] = "esperando_dia"
        return (
            f"¡Perfecto! 💪 Revisemos opciones de *{interes}*.\n"
            "¿Qué día te queda mejor: lunes, martes, miércoles, jueves, viernes o sábado?\n"
            "También puedes escribir *todos* para ver la semana completa."
        )

    if parece_pregunta_clases(texto):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        if day:
            return horario_dia(day)
        return horario_completo()

    if any(term in texto for term in ("gracias", "muchas gracias", "te agradezco")):
        ESTADOS_CONVERSACION[remitente] = "seguimiento"
        return "¡Con gusto! 💚 ¿Te ayudo con horarios, precios o ubicación? Escribe *menu* para ver las opciones."

    if any(term in texto for term in ("adios", "hasta luego", "nos vemos", "bye")):
        ESTADOS_CONVERSACION[remitente] = "inicio"
        return "¡Hasta pronto! 💚 ¡Te esperamos en MARQ'S GYM & SPORTS! Cuando quieras, escribe *hola* para continuar."

    ESTADOS_CONVERSACION[remitente] = "menu"
    return (
        "Soy el asistente de MARQ'S GYM & SPORTS y solo te puedo ayudar con temas del gym: "
        "clases, horarios, precios, promociones, ubicación y redes sociales. 😊\n\n" + menu_principal()
    )

def enviar_imagen(to, nombre_archivo, caption=""):
    """Sube la imagen local a Meta (si no está cacheada) y la envía por WhatsApp."""
    if not TOKEN:
        log_evento("ERROR DE CONFIGURACION", "falta WHATSAPP_TOKEN")
        return False
    ruta = ASSETS_DIR / nombre_archivo
    if not ruta.exists():
        log_evento("ERROR DE ARCHIVO", f"no existe {ruta}")
        return False
    try:
        media_id = MEDIA_IDS.get(nombre_archivo)
        if not media_id:
            upload_url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/media"
            headers = {"Authorization": f"Bearer {TOKEN}"}
            with ruta.open("rb") as imagen:
                resp = requests.post(upload_url, headers=headers,
                    data={"messaging_product": "whatsapp"},
                    files={"file": (ruta.name, imagen, "image/jpeg")}, timeout=30)
            if resp.status_code != 200:
                log_evento("ERROR AL SUBIR IMAGEN", f"HTTP {resp.status_code} | {resp.text[:500]}")
                return False
            media_id = resp.json().get("id")
            MEDIA_IDS[nombre_archivo] = media_id
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
                   "to": normalizar_numero(to), "type": "image",
                   "image": {"id": media_id}}
        if caption:
            payload["image"]["caption"] = caption
        url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
        resp = requests.post(url, headers={"Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"}, json=payload, timeout=20)
        if resp.status_code == 200:
            log_evento("IMAGEN ENVIADA", f"archivo={nombre_archivo} | para={normalizar_numero(to)}")
            return True
        log_evento("ERROR DE META IMAGEN", f"HTTP {resp.status_code} | {resp.text[:500]}")
        return False
    except requests.RequestException as exc:
        log_evento("ERROR DE CONEXION IMAGEN", str(exc))
        return False

def enviar_botones(to, texto, botones):
    """Envía un mensaje interactivo de WhatsApp con hasta 3 botones."""
    if not TOKEN:
        log_evento("ERROR DE CONFIGURACION", "falta WHATSAPP_TOKEN")
        return False
    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalizar_numero(to),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": item[0], "title": item[1]}}
                for item in botones[:3]
            ]}
        }
    }
    try:
        resp = requests.post(url, headers={"Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"}, json=payload, timeout=20)
        if resp.status_code == 200:
            log_evento("BOTONES ENVIADOS", f"para={normalizar_numero(to)}")
            return True
        log_evento("ERROR META BOTONES", f"HTTP {resp.status_code} | {resp.text[:600]}")
        return False
    except requests.RequestException as exc:
        log_evento("ERROR CONEXION BOTONES", str(exc))
        return False


def enviar_lista(to, texto, boton, filas, titulo_seccion="Opciones"):
    """Envía una lista interactiva (la lista que se abre al tocar Opciones)."""
    if not TOKEN:
        log_evento("ERROR DE CONFIGURACION", "falta WHATSAPP_TOKEN")
        return False
    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalizar_numero(to),
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": texto},
            "action": {
                "button": boton,
                "sections": [{
                    "title": titulo_seccion,
                    "rows": [
                        {"id": item[0], "title": item[1], **({"description": item[2]} if len(item) > 2 and item[2] else {})}
                        for item in filas
                    ]
                }]
            }
        }
    }
    try:
        resp = requests.post(url, headers={"Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"}, json=payload, timeout=20)
        if resp.status_code == 200:
            log_evento("LISTA INTERACTIVA ENVIADA", f"para={normalizar_numero(to)}")
            return True
        log_evento("ERROR META LISTA", f"HTTP {resp.status_code} | {resp.text[:600]}")
        return False
    except requests.RequestException as exc:
        log_evento("ERROR CONEXION LISTA", str(exc))
        return False


def enviar_ubicacion(to):
    """Envía el pin de ubicación del gym (mapa nativo de WhatsApp).

    Al tocarlo, el usuario ve el mapa y puede pedir la ruta para llegar.
    WhatsApp no permite personalizar el pin con una imagen o logo: el pin
    es el estándar, con el nombre y la dirección del gym.
    """
    if not TOKEN:
        log_evento("ERROR DE CONFIGURACION", "falta WHATSAPP_TOKEN")
        return False
    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": normalizar_numero(to),
        "type": "location",
        "location": {
            "latitude": GYM_LATITUDE,
            "longitude": GYM_LONGITUDE,
            "name": GYM_NAME,
            "address": GYM_ADDRESS,
        },
    }
    try:
        resp = requests.post(url, headers={"Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"}, json=payload, timeout=20)
        if resp.status_code == 200:
            log_evento("UBICACION ENVIADA", f"para={normalizar_numero(to)}")
            return True
        log_evento("ERROR META UBICACION", f"HTTP {resp.status_code} | {resp.text[:600]}")
        return False
    except requests.RequestException as exc:
        log_evento("ERROR CONEXION UBICACION", str(exc))
        return False


def enviar_menu_principal(to):
    texto = (f"💚 *{GYM_NAME}*\n\n"
             "¡Entrena con energía, aprende la técnica y alcanza tus objetivos! 💪\n\n"
             "¿Qué te gustaría consultar?")
    return enviar_lista(to, texto, "Opciones", [
        ("menu_actividades", "Actividades"),
        ("menu_membresias", "Membresías y precios"),
        ("menu_promos", "Ver promociones"),
        ("menu_ubicacion", "Ubicación"),
        ("menu_info", "Más información"),
    ])


def enviar_menu_info(to):
    return enviar_lista(to, "¿Qué información necesitas? 📍", "Opciones", [
        ("info_precios", "Precios e inscripción"),
        ("info_ubicacion", "Ubicación"),
        ("info_elegir", "Elegir una clase"),
        ("info_redes", "Redes sociales"),
        ("volver_menu", "Menú principal"),
    ])



def enviar_lista_seguimiento(to):
    return enviar_lista(to, "¿Qué te gustaría consultar ahora? 😊", "Opciones", [
        ("follow_clases", "Clases y horarios"),
        ("follow_actividad", "Preguntar por actividad"),
        ("follow_precios", "Precios e inscripción"),
        ("follow_ubicacion", "Ubicación"),
        ("follow_redes", "Redes sociales"),
        ("volver_menu", "Menú principal"),
    ], "Más opciones")

# =========================================================
# WEBHOOK META
# =========================================================
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        log_evento("WEBHOOK VERIFICADO", "Meta completó la verificación")
        return challenge, 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                for status in value.get("statuses", []):
                    estado = status.get("status", "desconocido")
                    destinatario = status.get("recipient_id", "desconocido")
                    if estado == "failed":
                        errores = status.get("errors", [])
                        detalle = str(errores[0].get("title") or errores[0].get("message") or errores[0]) if errores else "sin detalle"
                        log_evento("ERROR DE ENTREGA", f"destinatario={destinatario} | {detalle}")
                    elif estado in {"sent", "delivered", "read"}:
                        log_evento("ESTADO WHATSAPP", f"{estado} | destinatario={destinatario}")

                for message in value.get("messages", []):
                    sender = message.get("from", "")
                    tipo_mensaje = message.get("type", "desconocido")
                    mensaje_id = message.get("id", "")
                    if _mensaje_ya_procesado(mensaje_id):
                        log_evento("MENSAJE DUPLICADO IGNORADO", f"de={sender} | id={mensaje_id}")
                        continue
                    seleccion_id = ""
                    if tipo_mensaje == "interactive":
                        interactive = message.get("interactive", {})
                        reply = interactive.get("button_reply", {}) or interactive.get("list_reply", {})
                        seleccion_id = reply.get("id", "")
                        # IDs de listas convertidos a entradas internas del flujo existente.
                        ids_a_texto = {
                            "menu_clases": "actividades",
                            "menu_actividades": "actividades",
                            "act_box": "Box",
                            "act_kickboxing": "Kickboxing",
                            "act_yoga": "Yoga",
                            "act_pole": "Pole Dance",
                            "act_taekwondo": "Taekwondo",
                            "act_funcional": "Entrenamiento funcional",
                            "act_otra": "Otra actividad",
                            "act_horarios": "Ver horarios",
                            "act_promocion": "Ver promoción",
                            "act_otra_actividad": "Elegir otra actividad",
                            "menu_membresias": "precios",
                            "menu_promos": "3",
                            "menu_ubicacion": "ubicacion",
                            "menu_elegir": "ayudame a elegir",
                            "menu_info": "mas informacion",
                            "info_precios": "precios",
                            "info_ubicacion": "ubicacion",
                            "info_elegir": "ayudame a elegir",
                            "volver_menu": "menu principal",
                            "follow_clases": "1",
                            "follow_actividad": "preguntar por una actividad",
                            "follow_precios": "precios",
                            "follow_ubicacion": "ubicacion",
                            "day_lunes": "lunes",
                            "day_martes": "martes",
                            "day_miercoles": "miercoles",
                            "day_jueves": "jueves",
                            "day_viernes": "viernes",
                            "day_sabado": "sabado",
                            "day_todos": "todos",
                            "promo_membresias": "1",
                            "promo_estudiantes": "2",
                            "promo_renueva": "3",
                            "promo_todas": "4",
                            "promo_volver": "5",
                            "interes_box": "1",
                            "interes_baile": "2",
                            "interes_pole": "3",
                            "interes_taekwondo": "4",
                            "interes_no_se": "5",
                        }
                        incoming_text = ids_a_texto.get(seleccion_id, reply.get("title", "")).strip()
                    elif tipo_mensaje == "text":
                        incoming_text = message.get("text", {}).get("body", "").strip()
                    else:
                        log_evento("MENSAJE OMITIDO", f"tipo={tipo_mensaje} | remitente={sender}")
                        continue

                    texto_norm = normalizar_texto(incoming_text)
                    log_evento("MENSAJE RECIBIDO", f"de={sender} | texto={incoming_text!r} | id={mensaje_id or '-'}")

                    # Saludo o regreso al menú principal: imagen configurable + lista principal.
                    if texto_norm in {"hola", "holi", "buenas", "buenos dias", "buenas tardes", "buenas noches", "menu", "inicio", "ayuda", "opciones", "0", "menu principal"}:
                        ESTADOS_CONVERSACION[sender] = "menu"
                        if texto_norm not in {"menu", "inicio", "opciones", "0", "menu principal"}:
                            enviar_imagen(sender, IMAGEN_MENU, "💚 ¡Bienvenido a MARQ'S GYM & SPORTS!")
                        enviar_menu_principal(sender)
                        continue

                    # Abrir listas de selección de días, promociones y orientación.
                    if seleccion_id in {"menu_clases", "menu_actividades"} or texto_norm in {"actividades", "clases y horarios"}:
                        ESTADOS_CONVERSACION[sender] = "seleccion_actividad"
                        enviar_lista(sender, "¿Qué actividad te interesa? Elige una para consultar horarios o ver su promoción.", "Elegir actividad", [
                            ("act_box", "Box"),
                            ("act_kickboxing", "Kickboxing"),
                            ("act_yoga", "Yoga"),
                            ("act_pole", "Pole Dance"),
                            ("act_taekwondo", "Taekwondo"),
                            ("act_funcional", "Entrenamiento funcional"),
                            ("act_otra", "Otra actividad"),
                        ], "Actividades")
                        continue

                    # Al elegir una actividad, enviar inmediatamente su promoción ilustrativa
                    # y después ofrecer horarios u otra ruta. No se separa la promoción en otro paso.
                    activity_ids = {
                        "act_box": "Box",
                        "act_kickboxing": "Kickboxing",
                        "act_yoga": "Yoga",
                        "act_pole": "Pole Dance",
                        "act_taekwondo": "Taekwondo",
                        "act_funcional": "Entrenamiento funcional",
                    }
                    if seleccion_id in activity_ids:
                        actividad = activity_ids[seleccion_id]
                        ACTIVIDAD_SELECCIONADA[sender] = actividad
                        ESTADOS_CONVERSACION[sender] = "actividad_elegida"
                        activity_image = {
                            "Box": "demo_actividad_box.jpg",
                            "Kickboxing": "demo_actividad_kickboxing.jpg",
                            "Yoga": "demo_actividad_yoga.jpg",
                            "Pole Dance": "demo_actividad_pole.jpg",
                            "Taekwondo": "demo_actividad_taekwondo.jpg",
                            "Entrenamiento funcional": "demo_actividad_funcional.jpg",
                        }.get(actividad)

                        if activity_image and (ASSETS_DIR / activity_image).is_file():
                            caption = (
                                f"💚 ¡Te mostramos {actividad} en MARQ'S GYM!\n\n"
                                f"🔥 *Promoción destacada de la demo*\n"
                                f"Esta es una imagen ilustrativa para mostrar cómo se verá la promoción. "
                                f"El precio, la vigencia y las condiciones oficiales están por confirmar."
                            )
                            enviar_imagen(sender, activity_image, caption)
                        else:
                            send_whatsapp_message(
                                sender,
                                f"💚 ¡Excelente elección: {actividad}!\n"
                                "Estamos preparando la imagen de esta actividad; consulta recepción para promociones vigentes."
                            )

                        enviar_lista(
                            sender,
                            f"¿Qué te gustaría hacer después de conocer {actividad}?",
                            "Continuar",
                            [
                                ("act_horarios", "Ver horarios"),
                                ("act_otra_actividad", "Otra actividad"),
                                ("act_menu", "Menú principal"),
                            ],
                            "Siguiente paso"
                        )
                        continue

                    if seleccion_id == "act_otra":
                        enviar_lista(sender, "¿Qué actividad te interesa?", "Elegir actividad", [
                            ("act_box", "Box"), ("act_kickboxing", "Kickboxing"),
                            ("act_yoga", "Yoga"), ("act_pole", "Pole Dance"),
                            ("act_taekwondo", "Taekwondo"), ("act_funcional", "Entrenamiento funcional"),
                        ], "Actividades")
                        continue

                    if seleccion_id == "act_otra_actividad":
                        enviar_lista(sender, "Elige otra actividad.", "Elegir actividad", [
                            ("act_box", "Box"), ("act_kickboxing", "Kickboxing"),
                            ("act_yoga", "Yoga"), ("act_pole", "Pole Dance"),
                            ("act_taekwondo", "Taekwondo"), ("act_funcional", "Entrenamiento funcional"),
                            ("act_otra", "Otra actividad"),
                        ], "Actividades")
                        continue

                    if seleccion_id == "act_menu":
                        ESTADOS_CONVERSACION[sender] = "menu"
                        enviar_menu_principal(sender)
                        continue

                    if seleccion_id == "act_horarios":
                        actividad = ACTIVIDAD_SELECCIONADA.get(sender, "")
                        enviar_lista(
                            sender,
                            f"¿Qué horarios de {actividad} quieres consultar?",
                            "Elegir horario",
                            [
                                ("hor_dia_lunes", "Lunes"),
                                ("hor_dia_martes", "Martes"),
                                ("hor_dia_miercoles", "Miércoles"),
                                ("hor_dia_jueves", "Jueves"),
                                ("hor_dia_viernes", "Viernes"),
                                ("hor_dia_sabado", "Sábado"),
                                ("hor_todos", "Todos los horarios"),
                                ("act_otra_actividad", "Otra actividad"),
                                ("act_menu", "Menú principal"),
                            ],
                            "Días y horarios"
                        )
                        continue

                    # Respuesta al selector de día o a "Todos los horarios".
                    horarios_dias = {
                        "hor_dia_lunes": "lunes",
                        "hor_dia_martes": "martes",
                        "hor_dia_miercoles": "miercoles",
                        "hor_dia_jueves": "jueves",
                        "hor_dia_viernes": "viernes",
                        "hor_dia_sabado": "sabado",
                    }
                    if seleccion_id in horarios_dias or seleccion_id == "hor_todos":
                        actividad = ACTIVIDAD_SELECCIONADA.get(sender, "")
                        actividad_norm = normalizar_texto(actividad)

                        # Usar solo horarios que ya están cargados; no inventar horarios oficiales.
                        if actividad_norm == "box":
                            matches = [r for r in CLASS_SCHEDULE if "box" in normalizar_texto(r[3]) and "kickboxing" not in normalizar_texto(r[3])]
                        elif actividad_norm == "kickboxing":
                            matches = [r for r in CLASS_SCHEDULE if "kickboxing" in normalizar_texto(r[3])]
                        elif actividad_norm == "pole dance":
                            matches = [r for r in CLASS_SCHEDULE if "pole dance" in normalizar_texto(r[3])]
                        elif actividad_norm == "taekwondo":
                            matches = [r for r in CLASS_SCHEDULE if "taekwondo" in normalizar_texto(r[3])]
                        else:
                            matches = []  # No hay horarios oficiales cargados para Yoga o funcional.

                        if seleccion_id in horarios_dias:
                            dia = horarios_dias[seleccion_id]
                            matches = [r for r in matches if r[0] == dia]

                        if matches:
                            if seleccion_id == "hor_todos":
                                lines = [f"📅 *Todos los horarios de {actividad} — referencia DEMO*"]
                            else:
                                dia_label = DAY_LABELS.get(horarios_dias[seleccion_id], horarios_dias[seleccion_id].title())
                                lines = [f"📅 *Horarios de {actividad} — {dia_label} (DEMO)*"]
                            for d, start, end, name in sorted(matches, key=lambda r: (DAY_ORDER.get(r[0], 99), r[1])):
                                lines.append(f"• {DAY_LABELS.get(d, d.title())}: {start}–{end} ({name})")
                            lines.append("\n⚠️ Horarios de referencia; confirma el horario vigente en recepción.")
                            send_whatsapp_message(sender, "\n".join(lines))
                        else:
                            if seleccion_id in horarios_dias:
                                dia_label = DAY_LABELS.get(horarios_dias[seleccion_id], horarios_dias[seleccion_id].title())
                                detalle = f" para {dia_label}"
                            else:
                                detalle = ""
                            send_whatsapp_message(
                                sender,
                                f"Aún no tengo horarios cargados de {actividad}{detalle} en esta demo. "
                                "Para no darte información incorrecta, confirma el horario vigente en recepción."
                            )

                        enviar_lista(sender, "¿Qué quieres hacer ahora?", "Continuar", [
                            ("act_horarios", "Consultar otro día"),
                            ("act_otra_actividad", "Otra actividad"),
                            ("act_menu", "Menú principal"),
                        ], "Siguiente paso")
                        continue

                    if seleccion_id == "act_promocion":
                        actividad = ACTIVIDAD_SELECCIONADA.get(sender, "")
                        activity_image = {
                            "Box": "demo_actividad_box.jpg",
                            "Kickboxing": "demo_actividad_kickboxing.jpg",
                            "Yoga": "demo_actividad_yoga.jpg",
                            "Pole Dance": "demo_actividad_pole.jpg",
                            "Taekwondo": "demo_actividad_taekwondo.jpg",
                            "Entrenamiento funcional": "demo_actividad_funcional.jpg",
                        }.get(actividad)
                        if activity_image and (ASSETS_DIR / activity_image).is_file():
                            enviar_imagen(sender, activity_image, f"IMAGEN ILUSTRATIVA DE DEMO — {actividad}. No publicar como promoción oficial; precios y horarios son ficticios.")
                        else:
                            send_whatsapp_message(sender, f"Todavía no hay una imagen promocional asociada a {actividad}. Cuando recepción comparta la oficial, la agregaremos aquí.")
                        enviar_lista(sender, "¿Qué quieres hacer ahora?", "Opciones", [
                            ("act_horarios", "Ver horarios"),
                            ("act_otra_actividad", "Elegir otra actividad"),
                            ("act_menu", "Menú principal"),
                        ], "Continuar")
                        continue

                    # El "3" escrito solo abre promos desde el menú principal
                    # (inicio/menu); en seguimiento 3 = precios y en promociones
                    # 3 = ver la 3a promo. Las palabras sí abren promos siempre.
                    _promo_por_numero = texto_norm == "3" and ESTADOS_CONVERSACION.get(sender) in {"inicio", "menu"}
                    _promo_por_palabra = texto_norm in {"promociones", "ver promociones", "promo", "promos", "ofertas"} and ESTADOS_CONVERSACION.get(sender) in {"inicio", "menu", "seguimiento", "promociones"}
                    if seleccion_id == "menu_promos" or _promo_por_numero or _promo_por_palabra:
                        ESTADOS_CONVERSACION[sender] = "promociones"
                        send_whatsapp_message(sender, "🎉 *Promociones de MARQ'S GYM & SPORTS*\nTe enviaré la imagen de la promoción que elijas. Confirma vigencia y requisitos en recepción.")
                        enviar_lista(sender, "Selecciona la promoción que quieres ver.", "Ver opciones", [
                            ("promo_membresias", "Planes de membresía"),
                            ("promo_estudiantes", "Promoción estudiantes"),
                            ("promo_renueva", "Renueva y comparte"),
                            ("promo_yoga", "Promoción de yoga"),
                            ("promo_todas", "Ver todas"),
                            ("promo_volver", "Menú principal")
                        ], "Promociones")
                        continue

                    if seleccion_id == "menu_elegir" or texto_norm in {"ayudame a elegir", "que clase me recomiendas", "recomiendame una clase"}:
                        ESTADOS_CONVERSACION[sender] = "orientacion"
                        send_whatsapp_message(sender, "¡Claro! 💚 ¿Qué tipo de actividad te interesa más?")
                        enviar_lista(sender, "Elige el tipo de actividad.", "Elegir actividad", [
                            ("interes_box", "Box o kickboxing"),
                            ("interes_baile", "Baile y fitness"),
                            ("interes_pole", "Pole Dance"),
                            ("interes_taekwondo", "Taekwondo"),
                            ("interes_no_se", "Aún no lo sé")
                        ], "Actividades")
                        continue

                    if seleccion_id == "menu_info":
                        enviar_menu_info(sender)
                        continue

                    if seleccion_id == "menu_membresias" or seleccion_id == "info_precios":
                        ESTADOS_CONVERSACION[sender] = "seguimiento"
                        send_whatsapp_message(sender, "💚 Para darte información confiable, los precios y condiciones de inscripción deben confirmarse con recepción.")
                        enviar_lista_seguimiento(sender)
                        continue

                    if seleccion_id == "menu_ubicacion" or seleccion_id == "info_ubicacion":
                        ESTADOS_CONVERSACION[sender] = "seguimiento"
                        if not enviar_ubicacion(sender):
                            send_whatsapp_message(sender, f"📍 *{GYM_NAME}*\n{GYM_ADDRESS}\n\nÁbrelo en Google Maps: {GYM_MAPS_URL}")
                        enviar_lista_seguimiento(sender)
                        continue

                    if seleccion_id in {"info_redes", "follow_redes"}:
                        ESTADOS_CONVERSACION[sender] = "seguimiento"
                        send_whatsapp_message(sender, f"💚 ¡Síguenos en nuestras redes! 📲\n\nInstagram: {GYM_INSTAGRAM}\nFacebook: {GYM_FACEBOOK}")
                        enviar_lista_seguimiento(sender)
                        continue

                    if seleccion_id == "info_elegir":
                        ESTADOS_CONVERSACION[sender] = "orientacion"
                        send_whatsapp_message(sender, "¡Claro! 💚 ¿Qué tipo de actividad te interesa más?")
                        enviar_lista(sender, "Elige el tipo de actividad.", "Elegir actividad", [
                            ("interes_box", "Box o kickboxing"), ("interes_baile", "Baile y fitness"),
                            ("interes_pole", "Pole Dance"), ("interes_taekwondo", "Taekwondo"),
                            ("interes_no_se", "Aún no lo sé")
                        ], "Actividades")
                        continue

                    # Procesar la selección de promoción: las respuestas se mantienen como IDs.
                    if seleccion_id in {"promo_membresias", "promo_estudiantes", "promo_renueva", "promo_yoga", "promo_todas"}:
                        promo_files = {
                            "promo_membresias": "promo_membresias.jpg",
                            "promo_estudiantes": "promo_estudiantes.jpg",
                            "promo_renueva": "promo_renueva_comparte.jpg",
                            "promo_yoga": "promo_yoga.jpg",
                        }
                        if seleccion_id == "promo_todas":
                            for nombre in ("promo_membresias.jpg", "promo_estudiantes.jpg", "promo_renueva_comparte.jpg", "promo_yoga.jpg"):
                                if not (ASSETS_DIR / nombre).is_file():
                                    log_evento("ERROR DE PROMOCION", f"falta archivo {ASSETS_DIR / nombre}")
                                else:
                                    enviar_imagen(sender, nombre)
                        else:
                            nombre = promo_files[seleccion_id]
                            if not (ASSETS_DIR / nombre).is_file():
                                log_evento("ERROR DE PROMOCION", f"falta archivo {ASSETS_DIR / nombre}")
                                send_whatsapp_message(sender, "No encontré la imagen de esa promoción en la carpeta marqs_assets. Revisa que el archivo exista y vuelve a intentarlo.")
                            else:
                                enviar_imagen(sender, nombre)
                        enviar_lista(sender, "¿Quieres ver otra promoción o regresar al menú?", "Opciones", [
                            ("promo_membresias", "Planes de membresía"),
                            ("promo_estudiantes", "Promoción estudiantes"),
                            ("promo_renueva", "Renueva y comparte"),
                            ("promo_yoga", "Promoción de yoga"),
                            ("promo_todas", "Ver todas"),
                            ("promo_volver", "Menú principal")
                        ], "Promociones")
                        continue

                    if seleccion_id == "promo_volver":
                        ESTADOS_CONVERSACION[sender] = "menu"
                        enviar_menu_principal(sender)
                        continue

                    # Una selección de día o actividad se traduce al flujo de respuestas existente.
                    reply_text = responder(incoming_text, sender)

                    # Cualquier respuesta de horarios que incluya el menú de seguimiento
                    # se divide en texto informativo + lista interactiva de continuación.
                    if "¿Qué te gustaría hacer ahora?" in reply_text:
                        reply_text = reply_text.split("¿Qué te gustaría hacer ahora?", 1)[0].rstrip()
                        if reply_text:
                            send_whatsapp_message(sender, reply_text)
                        enviar_lista_seguimiento(sender)
                        continue

                    if reply_text.startswith("PROMO_IMAGEN:"):
                        nombre = reply_text.split(":", 1)[1]
                        enviar_imagen(sender, nombre)
                        enviar_lista(sender, "¿Quieres ver otra promoción?", "Opciones", [
                            ("promo_membresias", "Planes de membresía"),
                            ("promo_estudiantes", "Promoción estudiantes"),
                            ("promo_renueva", "Renueva y comparte"),
                            ("promo_yoga", "Promoción de yoga"),
                            ("promo_todas", "Ver todas"),
                            ("promo_volver", "Menú principal")
                        ], "Promociones")
                        continue
                    if reply_text == "PROMO_TODAS":
                        for nombre in ("promo_membresias.jpg", "promo_estudiantes.jpg", "promo_renueva_comparte.jpg", "promo_yoga.jpg"):
                            enviar_imagen(sender, nombre)
                        enviar_lista(sender, "Estas son las promociones de referencia. Confirma vigencia y requisitos en recepción.", "Opciones", [
                            ("promo_membresias", "Planes de membresía"),
                            ("promo_estudiantes", "Promoción estudiantes"),
                            ("promo_renueva", "Renueva y comparte"),
                            ("promo_yoga", "Promoción de yoga"),
                            ("promo_todas", "Ver todas"),
                            ("promo_volver", "Menú principal")
                        ], "Promociones")
                        continue

                    # Ubicación por texto ("ubicacion", "4" en frío/seguimiento,
                    # "donde queda", etc.): pin nativo + lista de seguimiento.
                    if reply_text == "UBICACION_PIN":
                        if not enviar_ubicacion(sender):
                            send_whatsapp_message(sender, f"📍 *{GYM_NAME}*\n{GYM_ADDRESS}\n\nÁbrelo en Google Maps: {GYM_MAPS_URL}")
                        enviar_lista_seguimiento(sender)
                        continue

                    # Convertir prompts de texto numerados a listas interactivas.
                    if "¿Qué día te interesa?" in reply_text or "Puedes escribir lunes" in reply_text:
                        send_whatsapp_message(sender, "¡Vamos a encontrar un horario para ti! 📅")
                        enviar_lista(sender, "¿Qué día te interesa?", "Elegir día", [
                            ("day_lunes", "Lunes"), ("day_martes", "Martes"),
                            ("day_miercoles", "Miércoles"), ("day_jueves", "Jueves"),
                            ("day_viernes", "Viernes"), ("day_sabado", "Sábado"),
                            ("day_todos", "Ver toda la semana")
                        ], "Días")
                        continue

                    if "¿Qué actividad te interesa?" in reply_text:
                        send_whatsapp_message(sender, "¿Qué actividad te interesa? 🥊")
                        enviar_lista(sender, "Elige una actividad.", "Elegir actividad", [
                            ("interes_box", "Box o kickboxing"), ("interes_baile", "Baile y fitness"),
                            ("interes_pole", "Pole Dance"), ("interes_taekwondo", "Taekwondo"),
                            ("interes_no_se", "Aún no lo sé")
                        ], "Actividades")
                        continue

                    if "¿Quieres consultar algo más?" in reply_text:
                        send_whatsapp_message(sender, "💚 ¡Con gusto te orientamos! Confirma precios y condiciones de inscripción con recepción.")
                        enviar_lista_seguimiento(sender)
                        continue

                    # Fallback: nunca volver a mostrar números como menú.
                    if "¿Qué te gustaría consultar?" in reply_text or "Escribe el número" in reply_text:
                        enviar_menu_principal(sender)
                        continue

                    enviado = send_whatsapp_message(sender, reply_text)
                    if enviado:
                        log_evento("RESPUESTA ENVIADA", f"para={normalizar_numero(sender)}")
                    else:
                        log_evento("RESPUESTA NO ENVIADA", f"para={normalizar_numero(sender)} | revisa el error anterior")

    except Exception as exc:
        log_evento("ERROR PROCESANDO EVENTO", repr(exc))

    return jsonify({"status": "ok"}), 200

def send_whatsapp_message(to, message):
    if not TOKEN:
        log_evento("ERROR DE CONFIGURACION", "falta la variable de entorno WHATSAPP_TOKEN")
        return False

    url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    recipient = normalizar_numero(to)
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            return True
        # En error sí conservamos el código y el detalle que devuelve Meta.
        log_evento("ERROR DE META", f"HTTP {response.status_code} | {response.text[:700]}")
        return False
    except requests.RequestException as exc:
        log_evento("ERROR DE CONEXION CON META", str(exc))
        return False

@app.route("/")
def home():
    return "Bot de WhatsApp de MARQ'S GYM & SPORTS funcionando."

@app.route("/privacy")
def privacy():
    return """<html><head><meta charset="utf-8"><title>Política de Privacidad - MARQ'S GYM & SPORTS</title></head><body style="font-family:sans-serif;max-width:800px;margin:40px auto;padding:0 20px;line-height:1.6">
<h1>Política de Privacidad - MARQ'S GYM &amp; SPORTS</h1>
<p><strong>Última actualización:</strong> 14 de noviembre de 2026</p>
<p>MARQ'S GYM &amp; SPORTS, ubicado en C. Tierra Colorada 29, Tierra Nueva, Azcapotzalco, 02130, Ciudad de México, CDMX, opera un chatbot de WhatsApp para atender consultas sobre horarios, actividades, precios, promociones y ubicación del gimnasio.</p>
<h2>Datos que recopilamos</h2>
<p>Cuando usted escribe al chatbot por WhatsApp, recibimos su número de teléfono y los mensajes que envía, a través de la API de WhatsApp de Meta. No solicitamos contraseñas, datos bancarios ni información sensible.</p>
<h2>Uso de los datos</h2>
<p>Usamos su número y sus mensajes únicamente para responder a sus consultas, mostrarle horarios, precios y promociones, y atenderle en recepción. No vendemos ni compartimos sus datos con terceros, salvo con Meta / WhatsApp, que procesa los mensajes como proveedor del servicio de mensajería.</p>
<h2>Conservación y derechos</h2>
<p>Los mensajes se conservan solo el tiempo necesario para atender su consulta. Usted puede pedir que dejemos de escribirle escribiendo "baja" o contactando a recepción del gimnasio, y puede solicitar la eliminación de su conversación.</p>
<h2>Contacto</h2>
<p>Para cualquier duda sobre esta política, acuda a recepción de MARQ'S GYM &amp; SPORTS, C. Tierra Colorada 29, Tierra Nueva, Azcapotzalco, 02130, Ciudad de México, CDMX, o escriba por WhatsApp al +52 56 4869 1745.</p>
</body></html>"""

@app.route("/terms")
def terms():
    return """<html><head><meta charset="utf-8"><title>Condiciones del Servicio - MARQ'S GYM & SPORTS</title></head><body style="font-family:sans-serif;max-width:800px;margin:40px auto;padding:0 20px;line-height:1.6">
<h1>Condiciones del Servicio - MARQ'S GYM &amp; SPORTS</h1>
<p>El chatbot de WhatsApp de MARQ'S GYM &amp; SPORTS ofrece información de referencia sobre horarios, actividades, precios y promociones. Los horarios y precios pueden cambiar; confirme vigencia y requisitos en recepción del gimnasio, en C. Tierra Colorada 29, Tierra Nueva, Azcapotzalco, 02130, Ciudad de México, CDMX.</p>
<p>El servicio se presta tal cual, sin garantía de disponibilidad continua. Para dudas, escriba al +52 56 4869 1745.</p>
</body></html>"""

if __name__ == "__main__":
    log_evento("BOT INICIADO", f"{GYM_NAME} V9 ACTIVIDADES | puerto 5000")
    if not TOKEN:
        log_evento("ADVERTENCIA", "WHATSAPP_TOKEN no está configurado")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
