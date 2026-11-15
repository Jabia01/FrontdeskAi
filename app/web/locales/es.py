"""Everything the public site says, in Spanish.

Spain is the first market, so this is not a courtesy translation of an English
page - it is the page a clinic in Marbella or a firm on Gran Vía actually
reads. It is written in "tú" throughout, which is what Spanish business
software sounds like now, and the idioms are chosen rather than carried across:
"the maths nobody runs" is "la cuenta que nadie hace", not a literal rendering
of "maths".

Three things are deliberately *not* translated, and taking any of them for an
oversight would be a mistake:

1. **The demo conversations.** They are real captures - see the rule at the top
   of `en.py`. The Dutch patient wrote in Dutch and was answered in Dutch;
   rewriting either side would turn evidence into advertising. Only the chip
   label, which is the name of a language, is in Spanish here.

2. **The language demo's sentences.** Same reason: each one is how a customer
   writing in that language actually phrases "what time do you close today?".

3. **The figures in METRICS.** They were measured on the deployment and mean
   the same number in every language. Only the words around them move, and the
   decimal comma - Spanish writes 3,2.

Reviews stay off here too. `SHOW_REVIEWS` is False in both locales and every
quote is marked `sample`, so a Spanish visitor is no likelier to read an
invented testimonial than an English one.
"""

from __future__ import annotations

from typing import Any

from app.web.locales import en

# --------------------------------------------------------------------- the demo

#: The name of each language, in Spanish. The transcripts themselves are
#: `en.DEMO_CONVERSATIONS` untouched - a real capture is evidence, and evidence
#: does not get localised.
_LANGUAGE_LABELS: dict[str, str] = {
    "ar": "Árabe",
    "de": "Alemán",
    "en": "Inglés",
    "es": "Español",
    "fr": "Francés",
    "nl": "Neerlandés",
    "no": "Noruego",
    "pl": "Polaco",
    "pt": "Portugués",
    "ru": "Ruso",
    "sv": "Sueco",
    "uk": "Ucraniano",
    "ur": "Urdu",
    "zh": "Chino",
}


def _relabelled(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same rows with Spanish language names on the chips.

    A shallow copy per row: the nested `turns` are shared with `en` on purpose,
    because they are the same captured conversation and nothing mutates them.
    """
    return [
        {**row, "label": _LANGUAGE_LABELS.get(row.get("code", ""), row["label"])}
        for row in rows
    ]


DEMO_SHOP = en.DEMO_SHOP
DEMO_CONVERSATIONS: list[dict[str, Any]] = _relabelled(en.DEMO_CONVERSATIONS)
LANGUAGE_DEMO: list[dict[str, str]] = _relabelled(en.LANGUAGE_DEMO)

DEMO_CAPTION = (
    "Conversaciones reales, reproducidas. Capturadas en noviembre de 2026 "
    "contra clínicas y agencias de prueba: cada respuesta es literal y cada "
    "tiempo está medido."
)

OWNER_ALERT = {
    "code": "2F2K",
    "title": "Nueva cita [2F2K]",
    "body": (
        "Consulta de carillas - Sra. Jansen, de Róterdam\n"
        "Jueves 4 de junio, 10:30\n"
        "Responde OK para confirmar, o CAMBIAR si no te encaja."
    ),
}

# -------------------------------------------------------------- the headline

#: Same contract as the English one: the stem holds still, the endings rotate,
#: and they have to stay close in length or the reserved height leaves a gap.
HEADLINE_STEM = "No vuelvas a perder un cliente por"
HEADLINE_TAILS: list[str] = [
    "un teléfono que nadie coge.",
    "un mensaje que leíste tarde.",
    "un buzón que nadie escucha.",
    "el negocio que contestó antes.",
    "un idioma que no hablas.",
    "el domingo que te tomaste.",
]

# ------------------------------------------------------------------- channels

CHANNELS: list[dict[str, str]] = [
    {
        "icon": "phone-answered",
        "label": "Llamadas",
        "note": "contestadas, no grabadas",
    },
    {"icon": "whatsapp", "label": "WhatsApp", "note": "donde ya estás"},
    {"icon": "message", "label": "SMS", "note": "sin instalar nada"},
]

# ------------------------------------------------------------------- the numbers

#: The figures are the English ones because they were measured, not written.
METRICS: list[dict[str, str]] = [
    {
        "value": "3,2",
        "unit": "s",
        "label": "hasta la primera respuesta",
        "note": "medido, 12 nov 2026",
    },
    {
        "value": "24",
        "unit": "/7",
        "label": "incluido un domingo a las dos de la mañana",
        "note": "sin turnos y sin buzón",
    },
    {
        "value": "todos",
        "unit": "",
        "label": "los idiomas en que te escriban",
        "note": "contestado en el mismo, a la primera",
    },
]

# ----------------------------------------------------------------------- reviews

#: Off in every locale until the quotes are real. See `en.py`.
SHOW_REVIEWS = False

REVIEWS: list[dict[str, Any]] = [
    {
        "quote": (
            "Antes perdía la mañana entera al teléfono. Ahora levanto la "
            "cabeza a la hora de comer y hay tres consultas agendadas que no "
            "he tocado."
        ),
        "name": "Nombre de ejemplo",
        "role": "Propietaria, clínica dental de ejemplo",
        "trade": "Dental",
        "sample": True,
    },
    {
        "quote": (
            "La mayoría de nuestros pacientes escriben en alemán o en "
            "neerlandés. Les contesta bien, a la primera, y aquí ya nadie "
            "tiene que hacer de traductor."
        ),
        "name": "Nombre de ejemplo",
        "role": "Gerente, clínica de ejemplo",
        "trade": "Dental",
        "sample": True,
    },
    {
        "quote": (
            "Las dos semanas en que solo te enseña lo que habría contestado: "
            "eso fue lo que me convenció. Le corregí cuatro veces, como "
            "mucho."
        ),
        "name": "Nombre de ejemplo",
        "role": "Propietario, inmobiliaria de ejemplo",
        "trade": "Inmobiliaria",
        "sample": True,
    },
    {
        "quote": (
            "Las consultas del extranjero llegan de madrugada y por la mañana "
            "ya están frías. Ahora reciben respuesta antes de cerrar la "
            "pestaña."
        ),
        "name": "Nombre de ejemplo",
        "role": "Propietaria, inmobiliaria de ejemplo",
        "trade": "Inmobiliaria",
        "sample": True,
    },
    {
        "quote": (
            "Las ausencias cayeron en picado en cuanto empezaron a salir los "
            "recordatorios. Solo con eso ya se paga."
        ),
        "name": "Nombre de ejemplo",
        "role": "Propietario, clínica estética de ejemplo",
        "trade": "Estética",
        "sample": True,
    },
    {
        "quote": (
            "Sabe cuándo callarse, que era justo lo que me preocupaba. Todo "
            "lo que depende del caso concreto me llega directo al móvil."
        ),
        "name": "Nombre de ejemplo",
        "role": "Socio, despacho de extranjería de ejemplo",
        "trade": "Extranjería",
        "sample": True,
    },
]

# ---------------------------------------------------------------------- features

FEATURES: list[dict[str, str]] = [
    {
        "icon": "phone-answered",
        "title": "Coge el teléfono",
        "body": (
            "Una conversación de verdad, no un menú. Conoce tus precios y tu "
            "agenda, y cierra la cita mientras sigues con la persona que "
            "tienes delante."
        ),
    },
    {
        "icon": "languages",
        "title": "Contesta en su idioma",
        "body": (
            "Español, alemán, neerlandés, sueco, árabe - hablado o escrito, "
            "contestado en el mismo idioma a la primera. Sin menús y sin "
            "\"pulse 1 para inglés\"."
        ),
    },
    {
        "icon": "phone-forwarded",
        "title": "Te pasa la llamada",
        "body": (
            "Una duda clínica, una queja, cualquier cosa que no deba tocar: "
            "avisa de que pasa la llamada y te la transfiere en directo a tu "
            "teléfono."
        ),
    },
    {
        "icon": "phone-missed",
        "title": "Devuelve las llamadas perdidas por mensaje",
        "body": (
            "La red de seguridad. Si una llamada se escapa igualmente, quien "
            "llamó tiene un mensaje en segundos, antes de probar con el "
            "siguiente de la lista."
        ),
    },
    {
        "icon": "calendar",
        "title": "Agenda citas de verdad",
        "body": (
            "Solo horas realmente libres en tu agenda. Nunca reserva ni "
            "promete un hueco que no haya comprobado."
        ),
    },
    {
        "icon": "bell",
        "title": "Recuerda la cita el día antes",
        "body": (
            "En su idioma y con la hora que acordasteis. Menos ausencias, y "
            "ni una llamada por tu parte."
        ),
    },
    {
        "icon": "check",
        "title": "Contesta a \"¿cómo va lo mío?\"",
        "body": (
            "Escribes LISTO una vez y quien esté esperando lo sabe en cuanto "
            "pregunte. Para los negocios que llevan el trabajo así - el "
            "expediente de un despacho, la reparación de un taller - y "
            "apagado para los que no."
        ),
    },
    {
        "icon": "hand",
        "title": "Sabe cuándo parar",
        "body": (
            "Te pasa la conversación y se calla. Nunca discutirá con un "
            "cliente enfadado ni dará un precio que no le hayas dado tú."
        ),
    },
    {
        "icon": "sunrise",
        "title": "Un resumen por la mañana",
        "body": (
            "Los números de ayer, las citas de hoy y lo que te está "
            "esperando. Un mensaje, en tu idioma, a tu hora."
        ),
    },
    {
        "icon": "hash",
        "title": "Funciona con tu propio número",
        "body": (
            "El número que tienes en la puerta y en tus anuncios, desviado "
            "cuando nadie lo coge. Tus clientes no tienen por qué notar que "
            "ha cambiado nada."
        ),
    },
]

# --------------------------------------------------------------------------- faq

FAQ: list[dict[str, str]] = [
    {
        "q": "¿Contesta el teléfono de verdad, o solo manda mensajes?",
        "a": (
            "Contesta el teléfono. Una conversación real: escucha lo que "
            "preguntan, en su idioma, mira tu agenda y les da cita. Si aun "
            "así se le escapa una llamada, quien llamó recibe un mensaje en "
            "segundos como red de seguridad."
        ),
    },
    {
        "q": "¿Qué pasa en una llamada que no puede resolver?",
        "a": (
            "Avisa de que pasa la llamada y te la transfiere en directo, con "
            "un mensaje que te explica por qué. Si no puedes cogerla, promete "
            "una devolución de llamada y te dice que lo ha hecho, para que la "
            "promesa no quede en el aire."
        ),
    },
    {
        "q": "¿Finge ser una persona?",
        "a": (
            "No. Dice que es tu asistente, y te pasa la conversación en "
            "cuanto alguien pide hablar con una persona."
        ),
    },
    {
        "q": "¿Y si no sabe algo?",
        "a": (
            "Lo dice y te pasa la pregunta. Solo puede hablar de lo que nos "
            "hayas contado: no puede inventarse un precio, una hora ni una "
            "promesa. Eso está impuesto por el sistema, no confiado a su "
            "buen criterio."
        ),
    },
    {
        "q": "¿Necesito un ordenador?",
        "a": (
            "No. Todo te llega por WhatsApp y contestas ahí mismo. Hay un "
            "panel si lo quieres, pero no hace falta abrirlo nunca."
        ),
    },
    {
        "q": "¿Puedo intervenir en una conversación?",
        "a": (
            "Sí. Responde al aviso y se calla en ese chat hasta que le "
            "devuelvas el turno. Lo que escribas se envía traducido al idioma "
            "del cliente."
        ),
    },
    {
        "q": "¿Y si no me gusta lo que dice?",
        "a": (
            "Para eso son las dos primeras semanas. Redacta, tú apruebas o "
            "reescribes, y lo ajustamos para que suene como tú antes de que "
            "un solo cliente lea una palabra."
        ),
    },
    {
        "q": "¿Tengo que cambiar de número?",
        "a": (
            "No. Mantienes el número que tienes en la puerta y en tus "
            "anuncios, y lo desvías cuando nadie lo coge: la misma opción que "
            "usarías para el buzón de voz. Cógelo tú siempre que puedas; "
            "Chichi solo atiende las llamadas que tú no coges."
        ),
    },
    {
        "q": "¿Cómo lo paro?",
        "a": (
            "Nos lo dices y lo apagamos el mismo día. No hay nada que "
            "desinstalar ni preaviso que cumplir."
        ),
    },
]

# ----------------------------------------------------------------------- sectors

SECTORS: list[dict[str, str]] = [
    {
        "name": "Clínicas dentales y estéticas",
        "line": (
            "El paciente que escribe a las once de la noche, desde otro país "
            "y en otro idioma."
        ),
        "href": "/dental",
    },
    {
        "name": "Inmobiliarias",
        "line": (
            "El comprador que mira tus anuncios desde Estocolmo a medianoche "
            "y pregunta qué sigue disponible."
        ),
        "href": "/inmobiliarias",
    },
    {
        "name": "Despachos de extranjería",
        "line": (
            "El cliente que necesita cita, en árabe, antes de un plazo que le "
            "da miedo."
        ),
        "href": "/extranjeria",
    },
]

# -------------------------------------------------------------------- the page

#: Same keys as `en.COPY`, asserted by a test rather than hoped for: a missing
#: key here would render an empty heading on a live page, and the whole reason
#: the copy moved out of the templates was so that a locale is a file somebody
#: can check.
#:
#: Prices are written the Spanish way throughout - "490 €", the symbol after
#: the amount with a space - which is why `brand.plans` carries the rendered
#: string rather than a number and a symbol for a template to glue together.
COPY: dict[str, Any] = {
    # The name of this language, in this language - what the switcher shows.
    "language_name": "Español",
    "per_month": "mes",
    "cta_primary": "Empezar gratis",
    "pricing_heading": "Dos planes. Todo en los dos.",
    "plan_includes": [
        "Cada mensaje y cada WhatsApp contestado, de día y de noche",
        "Todos los idiomas en que te escriban tus clientes",
        "Respuesta por mensaje a las llamadas perdidas, en segundos",
        "Citas, confirmaciones y recordatorios",
        "Tu resumen de la mañana, y un aviso en cuanto algo te necesite",
    ],
    "shadow_line": (
        "Primero 14 días en modo sombra: tú apruebas cada respuesta antes de "
        "que salga"
    ),
    "setup_fee_line": (
        "Alta única de 300-500 €, según lo que haya que cargar."
    ),
    "billing_line": (
        "Se empieza a facturar cuando lo enciendes, no cuando te das de alta. "
        "Puedes cancelar cuando quieras."
    ),
    "hero_price": "Desde 490 € al mes",
    "see_pricing": "Ver precios",
    "niche_does": "Qué hace",
    "niche_refuses": "Qué no va a hacer",
    "niche_limits_note": (
        "No son ajustes que tengas que acordarte de activar. Están dentro de "
        "lo que el asistente tiene permitido decir, y hay pruebas que se "
        "ejecutan antes de cada publicación."
    ),
    "nav": {
        "how": "Cómo funciona",
        "features": "Qué hace",
        "languages": "Idiomas",
        "pricing": "Precios",
        "faq": "Preguntas",
        "cta": "Empezar",
        "home_label": "inicio",
    },
    "hero": {
        "lede": (
            "Te llaman o te escriben a tu número, en el idioma que hablen. "
            "{brand} contesta en ese idioma - tus precios, tu horario, tu "
            "agenda - y cierra la cita antes de que prueben con el siguiente "
            "de la lista."
        ),
        "cta": "Empieza gratis {days} días",
        "secondary": "Ver cómo funciona",
        "channels_label": "Cómo te llegan los clientes",
        "points": [
            {
                "lead": "Contesta en segundos",
                "rest": ", a las dos de la mañana, en cualquier idioma",
            },
            {
                "lead": "No pierde ni una consulta",
                "rest": " de las que ya has pagado por conseguir",
            },
        ],
        "price_point": ", cancela cuando quieras, sin instalar nada",
        "note": "Nada le llega a un cliente hasta que tú lo has aprobado.",
        "answering_in": "contestando en",
        "owner_phone": "Tu móvil",
        "owner_reply": "OK",
    },
    "problem": {
        "eyebrow": "La cuenta que nadie hace",
        "heading": "El cliente que perdiste",
        "heading_accent": "esta mañana.",
        "beats": [
            {
                "time": "23:40",
                "text": (
                    "Alguien escribe desde el extranjero preguntando cuánto "
                    "cuesta un tratamiento. Tú estás dormido, o con un "
                    "paciente, o en una vista. Contestas por la mañana."
                ),
            },
            {
                "time": "23:42",
                "text": (
                    "Escribe al siguiente negocio de la lista. Ese sí "
                    "contesta."
                ),
            },
            {
                "time": "09:15",
                "text": (
                    "Contestas tú. Reservaron en otro sitio hace nueve horas."
                ),
            },
        ],
        "close": (
            "No es que seas lento. Es que estabas <em>trabajando</em>, o "
            "durmiendo, y ellos no. {brand} contesta en los noventa segundos "
            "que no tienes."
        ),
    },
    "how": {
        "eyebrow": "Cómo funciona",
        "heading": "Tres pasos. El del medio",
        "heading_accent": "es el que convence.",
        "steps": [
            {
                "tag": "",
                "title": "Aprendemos tu negocio",
                "body": (
                    "Tu horario, tus servicios, tus rangos de precio y tu "
                    "forma de hablar. No se inventa nada: si un precio no "
                    "está en tu lista, lo dice y ofrece una consulta en lugar "
                    "de arriesgar una cifra."
                ),
            },
            {
                "tag": "esto no lo hace nadie más",
                "title": "Dos semanas a tu sombra",
                "body": (
                    "Él redacta la respuesta y tú la apruebas por WhatsApp. "
                    "Ves exactamente lo que le habría dicho a un cliente real "
                    "antes de que nadie lo lea. Reescríbela y sale la tuya."
                ),
            },
            {
                "tag": "",
                "title": "Contesta",
                "body": (
                    "El teléfono, WhatsApp y los SMS, de día y de noche, en "
                    "su idioma. Todo lo que no debe tocar - una duda clínica, "
                    "un caso que depende de los datos de cada uno, una queja "
                    "- se detiene, y en una llamada en curso te la pasa "
                    "directamente."
                ),
            },
        ],
    },
    "features_head": {
        "eyebrow": "Qué hace",
        "heading": "Una recepción que",
        "heading_accent": "no cierra nunca.",
    },
    "meet": {
        "eyebrow": "Te presentamos a {brand}",
        "heading": "Ya está",
        "heading_accent": "en el mostrador.",
        "lede": (
            "{brand} no se va a comer, no deja a nadie en espera y no pierde "
            "la nota que alguien apuntó en el reverso de un sobre. Contesta, "
            "agenda, y cuando algo necesita a una persona se detiene y te "
            "toca el hombro."
        ),
        "note": (
            "Sabe tus precios porque se los has dicho. Sabe tu horario porque "
            "se lo has dicho. Todo lo demás te lo pasa a ti."
        ),
    },
    "languages_head": {
        "eyebrow": "Idiomas",
        "heading": "Tus clientes no",
        "heading_accent": "hablan todos español.",
        "lede": (
            "La mayoría de los sistemas obligan a elegir idioma antes de "
            "poder preguntar nada. {brand} lee lo que han escrito y contesta "
            "en ese idioma - a la primera, siempre."
        ),
        "note": (
            "Si cambian de idioma a mitad de la conversación, va detrás. El "
            "primer mensaje incluye siempre el inglés, y siempre les dice que "
            "pueden escribir como quieran. Tu horario y tus precios los "
            "escribes una vez, en tu idioma."
        ),
    },
    "who": {
        "eyebrow": "Para quién es",
        "heading": "Si tu próximo cliente",
        "heading_accent": "no es de aquí.",
    },
    "reviews_head": {
        "heading": "Lo que dicen.",
        "sample_flag": "Texto de ejemplo - no son citas de clientes reales",
    },
    "pricing_eyebrow": "Precios",
    "foot": {
        "product": "Producto",
        "who": "Para quién es",
        "legal": "Legal",
        "privacy": "Política de privacidad",
        "gdpr": "Protección de datos",
        "dpa": "Contrato de encargado del tratamiento",
        "terms": "Condiciones del servicio",
        "messaging": "Condiciones de mensajería",
        # The documents behind those five links are in English, and will stay
        # that way until a lawyer has been over them: a machine-translated
        # processor agreement is worse than an English one somebody can take
        # to their own abogado. Saying so here beats letting them click.
        "legal_note": "Estos documentos están en inglés por ahora.",
        "talk": "Habla con nosotros",
        "shop_login": "Acceso para negocios",
        "rights": "Todos los derechos reservados.",
    },
    "faq_head": {
        "eyebrow": "Preguntas",
        "heading": "Las que de verdad",
        "heading_accent": "nos hacen.",
    },
    "thanks": {
        "eyebrow": "Recibido",
        "heading": "Te escribimos",
        "heading_accent": "en menos de un día.",
        "lede": (
            "Por WhatsApp, al número que nos has dado - que es justo de lo "
            "que va el producto, así que sería raro contestarte por correo. "
            "Si es urgente, responde en cuanto te llegue."
        ),
        "back": "Volver al principio",
    },
    "signup": {
        "heading": "Ponlo en tu número y míralo trabajar.",
        "lede": (
            "Damos de alta a mano, de pocos en pocos, para que el asistente "
            "conozca tus precios y tus normas antes de contestar a nadie."
        ),
        "business_name": "Nombre del negocio",
        "niche": "¿Qué tipo de negocio es?",
        "niche_hint": (
            "Esto decide lo que el asistente tiene permitido decir. Se puede "
            "cambiar después; no es una etiqueta de una factura."
        ),
        "niche_placeholder": "Elige uno",
        "trade": "En tus propias palabras",
        "trade_hint": "Solo si arriba has elegido \"Otra cosa\".",
        "country": "País",
        "country_placeholder": "Elige uno",
        "country_hint": (
            "¿No está en la lista? Déjalo en blanco y pon el número de abajo "
            "completo."
        ),
        "city": "Ciudad",
        "contact_name": "Tu nombre",
        "phone": "Número de WhatsApp",
        "phone_hint": (
            "Con el prefijo del país. Aquí te contestamos, y aquí te manda el "
            "asistente su resumen de la mañana."
        ),
        "email": "Correo",
        "email_hint": "Opcional.",
        "languages": "¿En qué idiomas te escriben tus clientes?",
        "languages_hint": (
            "Marca todos los que recibas. El asistente contesta en el que "
            "haya usado cada uno, y luego puedes añadir más."
        ),
        "crm_tool": "¿Con qué agendas hoy?",
        "crm_tool_hint": (
            "Una agenda, un programa, un cuaderno de papel: lo que sea de "
            "verdad."
        ),
        "site_url": "Web",
        "site_url_hint": (
            "Opcional. Es la forma más rápida de que nos informemos sobre ti."
        ),
        "message": "¿Algo que debamos saber?",
        "submit": "Enviar",
        "consent": (
            "Acepto que {brand} se ponga en contacto conmigo por WhatsApp o "
            "por correo para configurar este asistente."
        ),
        "consent_detail": (
            "Guardamos lo que has escrito arriba y nada más. Sin datos de "
            "pago y sin contrato. Puedes pedirnos que lo borremos cuando "
            "quieras: nuestra {privacy} explica cómo."
        ),
        "privacy_link": "política de privacidad",
    },
}

# ------------------------------------------------------------------ niche pages

#: `slug`, `path`, `niche` and `demo` are structural and shared with `en` - the
#: URLs stay the same in every language, because /dental and /inmobiliarias are
#: already the words a Spanish visitor would expect, and the demo filter picks
#: conversations by language code.
NICHE_PAGES: dict[str, dict[str, Any]] = {
    "dental": {
        **{k: en.NICHE_PAGES["dental"][k] for k in ("slug", "path", "niche", "demo")},
        "eyebrow": "Clínicas dentales y estéticas",
        "headline": (
            "Tu próximo paciente extranjero escribe a las once de la noche en "
            "alemán. Chichi contesta en alemán y agenda la consulta."
        ),
        "lede": (
            "Casi todas las consultas que importan llegan cuando ya has "
            "cerrado, de alguien que está en otro país decidiendo entre tu "
            "clínica y otra a dos calles. Chichi contesta en su idioma, da "
            "solo los precios que has fijado tú, y deja la cita en la agenda."
        ),
        "does": [
            "Contesta en el idioma del paciente, de día y de noche",
            (
                "Da solo los precios \"desde\" que has introducido: nunca una "
                "cifra calculada por su cuenta"
            ),
            "Toma las fechas del viaje y ofrece solo horas dentro de la estancia",
            "Agenda la consulta y envía tu enlace de reserva",
            "Manda la revisión cuando toca",
        ],
        "refuses": [
            "Nunca diagnostica, ni dice a qué puede deberse un síntoma",
            (
                "Nunca aconseja sobre medicación, anestesia ni sobre si un "
                "tratamiento le conviene a alguien"
            ),
            "Nunca estima cuánto durará el tratamiento o la recuperación",
            "No pide fotografías ni historial médico",
            (
                "Dolor, sangrado, inflamación o cualquier cosa posterior a una "
                "intervención van directos a tu equipo clínico"
            ),
        ],
    },
    "inmobiliarias": {
        **{
            k: en.NICHE_PAGES["inmobiliarias"][k]
            for k in ("slug", "path", "niche", "demo")
        },
        "eyebrow": "Inmobiliarias",
        "headline": (
            "Un comprador en Estocolmo está viendo tus anuncios a medianoche. "
            "Chichi contesta en sueco y organiza la visita."
        ),
        "lede": (
            "Los compradores extranjeros buscan cuando tu oficina está "
            "cerrada y hacen las mismas cinco preguntas. Chichi las contesta "
            "en el idioma del comprador, con las palabras de tu propio "
            "anuncio, y le pasa a un agente una consulta ya cualificada."
        ),
        "does": [
            "Contesta en el idioma del comprador, a partir de tu propio anuncio",
            (
                "Recoge presupuesto, zona, plazos, si es al contado o con "
                "hipoteca, y si tiene NIE"
            ),
            "Envía inmuebles que encajan: solo los que tienes publicados",
            "Organiza visitas y hace seguimiento durante el mes siguiente",
        ],
        "refuses": [
            "Nunca confirma que un inmueble siga disponible",
            "Nunca negocia, ni dice en qué punto está una oferta",
            "Nunca se inventa metros, año de construcción ni situación de licencia",
            (
                "Nunca dice que comprar una vivienda dé residencia: la golden "
                "visa española ha terminado, y la portuguesa ya no incluye "
                "inmuebles"
            ),
            "Las preguntas fiscales, hipotecarias y jurídicas van a una persona",
        ],
    },
    "extranjeria": {
        **{
            k: en.NICHE_PAGES["extranjeria"][k]
            for k in ("slug", "path", "niche", "demo")
        },
        "eyebrow": "Despachos de extranjería",
        "headline": (
            "Un cliente escribe en árabe sobre su arraigo, con miedo a un "
            "plazo. Chichi contesta en árabe y agenda la consulta."
        ),
        "lede": (
            "Tus clientes escriben en el idioma en que están más cómodos, "
            "muchas veces fuera de horario y muchas veces preocupados. Chichi "
            "recoge los datos, agenda la consulta y sabe exactamente dónde "
            "pararse."
        ),
        "does": [
            (
                "Contesta en el idioma del cliente: árabe, español, francés, "
                "ruso, urdu y más"
            ),
            (
                "Clasifica la consulta: arraigo, nacionalidad, renovación de "
                "TIE, visado de nómada digital, reagrupación familiar"
            ),
            "Envía solo la lista de documentos que ha publicado tu despacho",
            "Agenda y cobra la provisión de una consulta de pago",
            "Informa del estado del expediente tal y como lo ha fijado tu despacho",
        ],
        "refuses": [
            "Nunca dice si alguien cumple los requisitos, ni qué va a pasar",
            "Nunca predice plazos, resultados ni vencimientos",
            "Nunca monta una lista de documentos por su cuenta",
            "Nunca pide un pasaporte, un NIE ni un número de expediente",
            (
                "Dice en el primer mensaje que esto no es asesoramiento "
                "jurídico"
            ),
            (
                "Una orden de expulsión, una detención o un plazo dentro de "
                "diez días te llegan de inmediato, antes que cualquier otra "
                "cosa"
            ),
        ],
    },
}
