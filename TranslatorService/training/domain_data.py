"""Hand-written English / Spanish / French training data for the translator's own domain.

The public corpora (Tatoeba, News, Europarl) contain almost no car-service text and no
conversation with unusual names, so the model mistranslated words like "dealership" and mangled
names it had never seen. This module provides:

  * domain_triplets(rng)  -> (en, es, fr) sentences: car service, greetings, introductions,
                             built from templates and slot lists written by hand
  * name_augment(x, y, rng) -> Tatoeba pairs with "Tom"/"Mary" swapped for random unseen names
                             on both sides, which teaches the model to copy names it never saw

The held-out domain test set (training/domain_test.tsv) is written separately and is
excluded from training by prepare_data.py.
"""

from __future__ import annotations

import random
import re

# ---------------------------------------------------------------- names -----------------------
# Real given names from many cultures (none of them used in domain_test.tsv) ...
_REAL_NAMES = """Aarav Aditi Akash Ananya Arun Bhavna Chitra Devika Dinesh Divya Ganesh Gita Harish
Ishaan Janani Kiran Lavanya Madhavan Manoj Nandini Naveen Pooja Prakash Rahul Rajesh Ramya
Sanjay Shreya Sneha Srinivasan Tarun Uma Varun Vijay Yamini Murugan Selvi Senthil Thilak
Kumaravel Saravanan Jayanthi Ilango Bharathi Elango Kannan Mohan Revathi Sundaram
Ahmed Youssef Leila Karim Nadia Tariq Zainab Hamza Amina Bilal Chen Jing Li Ming Xiu Haruto
Sakura Takeshi Aiko Minh Linh Thanh Jin Hyun Soo Bogdan Katarzyna Piotr Agnieszka Tomasz
Nikolai Svetlana Oksana Andrei Ioana Mihai Dragos Kofi Abena Yaw Emeka Chiamaka Tunde Zanele
Sipho Lerato Mateo Valentina Santiago Camila Joaquin Ximena Thiago Lucia Matteo Giulia
Lorenzo Chiara Hugo Chloe Louis Manon Mathis Lea Jonas Lena Finn Mia Lukas Emma Noah
Oskar Freya Sven Ingrid Aino Eero Siobhan Niamh Padraig Kowalski Nowak Dubois Lefebvre
Moreau Fernandez Gonzalez Rodriguez Martinez Lopez Rossi Bianchi Muller Schmidt Patel
Sharma Reddy Iyer Nair Menon Pillai Gupta Mehta Rao Kapoor Banerjee Mukherjee""".split()

_SYL = ["ka", "ra", "vi", "na", "shi", "ku", "mo", "te", "lu", "pa", "dhi", "ya", "ro", "san",
        "jit", "bha", "zo", "el", "an", "ir", "mi", "go", "sha", "tha", "ven", "dra", "nik",
        "ola", "ume", "bri", "cha", "kav", "lak", "ndi", "pre", "sel", "tor", "vas", "wen", "yu"]


def random_name(rng: random.Random) -> str:
    """A real or made-up given name: real names, plus random syllable strings that are not
    words of any language (so the model must copy them letter by letter)."""
    if rng.random() < 0.5:
        name = rng.choice(_REAL_NAMES)
    else:
        name = "".join(rng.choice(_SYL) for _ in range(rng.randint(2, 4))).capitalize()
        if rng.random() < 0.3:
            name = name[:-1] + rng.choice("hyaeiu")  # unusual endings like "...kyha"
    if rng.random() < 0.15:
        name += " " + rng.choice(_REAL_NAMES)  # first + last name
    return name


_TOM_MARY = re.compile(r"\b(Tom|Mary)\b")


def name_augment(x: str, y: str, rng: random.Random) -> tuple[str, str] | None:
    """Swap Tom / Mary for random names on both sides of a Tatoeba pair, or None when the
    names cannot be matched one-to-one."""
    nx, ny = _TOM_MARY.findall(x), _TOM_MARY.findall(y)
    if not nx or sorted(nx) != sorted(ny):
        return None
    new = {n: random_name(rng) for n in set(nx)}
    if len(new) == 2 and new["Tom"] == new["Mary"]:
        return None
    sub = lambda s: _TOM_MARY.sub(lambda m: new[m.group(1)], s)  # noqa: E731
    return sub(x), sub(y)


# ----------------------------------------------------------- car service ----------------------
# (en, es, fr) with the definite article, usable as a direct object.
PARTS = [
    ("the battery", "la batería", "la batterie"),
    ("the brakes", "los frenos", "les freins"),
    ("the brake pads", "las pastillas de freno", "les plaquettes de frein"),
    ("the engine", "el motor", "le moteur"),
    ("the tyres", "los neumáticos", "les pneus"),
    ("the tires", "los neumáticos", "les pneus"),
    ("the oil", "el aceite", "l'huile"),
    ("the oil filter", "el filtro de aceite", "le filtre à huile"),
    ("the air filter", "el filtro de aire", "le filtre à air"),
    ("the air conditioning", "el aire acondicionado", "la climatisation"),
    ("the windscreen", "el parabrisas", "le pare-brise"),
    ("the windshield", "el parabrisas", "le pare-brise"),
    ("the headlights", "los faros", "les phares"),
    ("the clutch", "el embrague", "l'embrayage"),
    ("the gearbox", "la caja de cambios", "la boîte de vitesses"),
    ("the exhaust", "el tubo de escape", "le pot d'échappement"),
    ("the spark plugs", "las bujías", "les bougies"),
    ("the wipers", "los limpiaparabrisas", "les essuie-glaces"),
    ("the steering", "la dirección", "la direction"),
    ("the radiator", "el radiador", "le radiateur"),
    ("the alternator", "el alternador", "l'alternateur"),
    ("the starter motor", "el motor de arranque", "le démarreur"),
    ("the timing belt", "la correa de distribución", "la courroie de distribution"),
    ("the suspension", "la suspensión", "la suspension"),
    ("the fuel pump", "la bomba de combustible", "la pompe à carburant"),
    ("the seat belt", "el cinturón de seguridad", "la ceinture de sécurité"),
    ("the coolant", "el líquido refrigerante", "le liquide de refroidissement"),
    ("the shock absorbers", "los amortiguadores", "les amortisseurs"),
    ("the rear lights", "las luces traseras", "les feux arrière"),
    ("the horn", "el claxon", "le klaxon"),
    ("the mirrors", "los retrovisores", "les rétroviseurs"),
    ("the spare wheel", "la rueda de repuesto", "la roue de secours"),
    ("the key", "la llave", "la clé"),
    ("the software", "el software", "le logiciel"),
]

PART_TEMPLATES = [
    ("We need to replace {}.", "Tenemos que cambiar {}.", "Nous devons remplacer {}."),
    ("Can you check {}, please?", "¿Puede revisar {}, por favor?", "Pouvez-vous vérifier {}, s'il vous plaît ?"),
    ("The mechanic repaired {}.", "El mecánico reparó {}.", "Le mécanicien a réparé {}."),
    ("The technician will inspect {} tomorrow.", "El técnico revisará {} mañana.", "Le technicien inspectera {} demain."),
    ("We have already changed {}.", "Ya hemos cambiado {}.", "Nous avons déjà changé {}."),
    ("I think there is a problem with {}.", "Creo que hay un problema con {}.", "Je pense qu'il y a un problème avec {}."),
    ("We found a problem with {}.", "Hemos encontrado un problema con {}.", "Nous avons trouvé un problème avec {}."),
    ("The warranty covers {}.", "La garantía cubre {}.", "La garantie couvre {}."),
    ("Does the warranty cover {}?", "¿La garantía cubre {}?", "Est-ce que la garantie couvre {} ?"),
    ("How much does it cost to replace {}?", "¿Cuánto cuesta cambiar {}?", "Combien coûte le remplacement de {} ?"),
    ("Please check {} before the trip.", "Por favor, revise {} antes del viaje.", "Veuillez vérifier {} avant le voyage."),
    ("I would like to have {} checked.", "Quisiera que revisaran {}.", "Je voudrais faire vérifier {}."),
    ("We will clean {} for free.", "Limpiaremos {} gratis.", "Nous nettoierons {} gratuitement."),
    ("Did you check {}?", "¿Ha revisado {}?", "Avez-vous vérifié {} ?"),
    ("You should not use the car until we repair {}.", "No debería usar el coche hasta que reparemos {}.",
     "Vous ne devriez pas utiliser la voiture avant que nous réparions {}."),
]

# Time expressions (en, es, fr)
TIMES = [
    ("tomorrow morning", "mañana por la mañana", "demain matin"),
    ("tomorrow afternoon", "mañana por la tarde", "demain après-midi"),
    ("this afternoon", "esta tarde", "cet après-midi"),
    ("this evening", "esta noche", "ce soir"),
    ("today", "hoy", "aujourd'hui"),
    ("on Monday", "el lunes", "lundi"),
    ("on Tuesday", "el martes", "mardi"),
    ("on Wednesday", "el miércoles", "mercredi"),
    ("on Thursday", "el jueves", "jeudi"),
    ("on Friday", "el viernes", "vendredi"),
    ("on Saturday", "el sábado", "samedi"),
    ("on Sunday", "el domingo", "dimanche"),
    ("next week", "la semana que viene", "la semaine prochaine"),
    ("in two hours", "dentro de dos horas", "dans deux heures"),
    ("in one hour", "dentro de una hora", "dans une heure"),
    ("at five o'clock", "a las cinco", "à cinq heures"),
    ("at nine o'clock", "a las nueve", "à neuf heures"),
    ("before noon", "antes del mediodía", "avant midi"),
    ("at the end of the day", "al final del día", "en fin de journée"),
]

TIME_TEMPLATES = [
    ("Your car will be ready {}.", "Su coche estará listo {}.", "Votre voiture sera prête {}."),
    ("Your vehicle will be ready {}.", "Su vehículo estará listo {}.", "Votre véhicule sera prêt {}."),
    ("Can I bring my car {}?", "¿Puedo traer mi coche {}?", "Puis-je amener ma voiture {} ?"),
    ("The dealership is closed {}.", "El concesionario está cerrado {}.", "La concession est fermée {}."),
    ("The dealership is open {}.", "El concesionario está abierto {}.", "La concession est ouverte {}."),
    ("Our workshop is closed {}.", "Nuestro taller está cerrado {}.", "Notre atelier est fermé {}."),
    ("I have an appointment {}.", "Tengo una cita {}.", "J'ai un rendez-vous {}."),
    ("The part will arrive {}.", "La pieza llegará {}.", "La pièce arrivera {}."),
    ("We will call you {}.", "Le llamaremos {}.", "Nous vous appellerons {}."),
    ("Can you come back {}?", "¿Puede volver {}?", "Pouvez-vous revenir {} ?"),
    ("The service is scheduled {}.", "El mantenimiento está previsto {}.", "L'entretien est prévu {}."),
]

SUBJECTS = [  # (en, es, fr) subjects that "make a noise"
    ("The engine", "El motor", "Le moteur"),
    ("The car", "El coche", "La voiture"),
    ("My car", "Mi coche", "Ma voiture"),
    ("The vehicle", "El vehículo", "Le véhicule"),
]
NOISES = [
    ("a strange noise", "un ruido extraño", "un bruit étrange"),
    ("a loud noise", "un ruido fuerte", "un bruit fort"),
    ("a squeaking noise", "un chirrido", "un grincement"),
    ("a clicking noise", "un clic", "un cliquetis"),
]
MOMENTS = [
    ("when starting", "al arrancar", "au démarrage"),
    ("when I start the engine", "cuando arranco el motor", "quand je démarre le moteur"),
    ("when braking", "al frenar", "au freinage"),
    ("when turning", "al girar", "dans les virages"),
    ("at high speed", "a alta velocidad", "à grande vitesse"),
    ("in the morning", "por la mañana", "le matin"),
    ("when it is cold", "cuando hace frío", "quand il fait froid"),
    ("on the motorway", "en la autopista", "sur l'autoroute"),
]
NOISE_TEMPLATES = [
    ("{s} makes {n} {m}.", "{s} hace {n} {m}.", "{s} fait {n} {m}."),
    ("{s} is making {n} {m}.", "{s} está haciendo {n} {m}.", "{s} fait {n} {m}."),
]

# Introductions with names
NAME_TEMPLATES = [
    ("My name is {}.", "Me llamo {}.", "Je m'appelle {}."),
    ("Hi, my name is {}.", "Hola, me llamo {}.", "Salut, je m'appelle {}."),
    ("Hello, my name is {}.", "Hola, me llamo {}.", "Bonjour, je m'appelle {}."),
    ("Hi, I am {}.", "Hola, soy {}.", "Salut, je suis {}."),
    ("Hello, I'm {}.", "Hola, soy {}.", "Bonjour, je suis {}."),
    ("I am {}, your service advisor.", "Soy {}, su asesor de servicio.", "Je suis {}, votre conseiller service."),
    ("This is {}, our mechanic.", "Este es {}, nuestro mecánico.", "Voici {}, notre mécanicien."),
    ("{} will call you back.", "{} le devolverá la llamada.", "{} vous rappellera."),
    ("Please ask for {} at the reception.", "Por favor, pregunte por {} en la recepción.",
     "Veuillez demander {} à l'accueil."),
    ("{} is waiting for you.", "{} le está esperando.", "{} vous attend."),
    ("Is {} here today?", "¿Está {} hoy aquí?", "Est-ce que {} est là aujourd'hui ?"),
    ("Thank you, {}.", "Gracias, {}.", "Merci, {}."),
    ("Good morning, {}.", "Buenos días, {}.", "Bonjour, {}."),
    ("Nice to meet you, {}.", "Encantado de conocerle, {}.", "Enchanté, {}."),
    ("{} repaired your car.", "{} reparó su coche.", "{} a réparé votre voiture."),
    ("I spoke with {} yesterday.", "Hablé con {} ayer.", "J'ai parlé avec {} hier."),
]

# Fixed sentences: greetings, conversation, service desk
PHRASES = [
    ("Hello.", "Hola.", "Bonjour."),
    ("Hi.", "Hola.", "Salut."),
    ("Hi!", "¡Hola!", "Salut !"),
    ("Hello, how are you?", "Hola, ¿cómo estás?", "Bonjour, comment allez-vous ?"),
    ("Hi, how are you?", "Hola, ¿qué tal?", "Salut, ça va ?"),
    ("How are you?", "¿Cómo estás?", "Comment allez-vous ?"),
    ("How are you doing?", "¿Qué tal estás?", "Comment ça va ?"),
    ("I'm fine, thank you.", "Estoy bien, gracias.", "Je vais bien, merci."),
    ("I am fine, thanks.", "Estoy bien, gracias.", "Je vais bien, merci."),
    ("And you?", "¿Y tú?", "Et toi ?"),
    ("Good morning.", "Buenos días.", "Bonjour."),
    ("Good afternoon.", "Buenas tardes.", "Bon après-midi."),
    ("Good evening.", "Buenas noches.", "Bonsoir."),
    ("Good night.", "Buenas noches.", "Bonne nuit."),
    ("Goodbye.", "Adiós.", "Au revoir."),
    ("Bye!", "¡Adiós!", "Salut !"),
    ("See you soon.", "Hasta pronto.", "À bientôt."),
    ("See you tomorrow.", "Hasta mañana.", "À demain."),
    ("See you later.", "Hasta luego.", "À plus tard."),
    ("Thank you very much.", "Muchas gracias.", "Merci beaucoup."),
    ("Thanks a lot.", "Muchas gracias.", "Merci beaucoup."),
    ("You're welcome.", "De nada.", "De rien."),
    ("Please.", "Por favor.", "S'il vous plaît."),
    ("Excuse me.", "Disculpe.", "Excusez-moi."),
    ("Sorry.", "Lo siento.", "Désolé."),
    ("Yes, of course.", "Sí, claro.", "Oui, bien sûr."),
    ("No, thank you.", "No, gracias.", "Non, merci."),
    ("Okay.", "Vale.", "D'accord."),
    ("No problem.", "No hay problema.", "Pas de problème."),
    ("Have a nice day.", "Que tenga un buen día.", "Bonne journée."),
    ("Welcome to our dealership.", "Bienvenido a nuestro concesionario.", "Bienvenue dans notre concession."),
    ("Welcome to our workshop.", "Bienvenido a nuestro taller.", "Bienvenue dans notre atelier."),
    ("Where is the nearest dealership?", "¿Dónde está el concesionario más cercano?",
     "Où se trouve la concession la plus proche ?"),
    ("The dealership is next to the station.", "El concesionario está al lado de la estación.",
     "La concession est à côté de la gare."),
    ("I bought my car at this dealership.", "Compré mi coche en este concesionario.",
     "J'ai acheté ma voiture dans cette concession."),
    ("The dealer will contact you.", "El concesionario se pondrá en contacto con usted.",
     "Le concessionnaire vous contactera."),
    ("I would like to book a service.", "Quisiera reservar un mantenimiento.", "Je voudrais réserver un entretien."),
    ("I would like to make an appointment.", "Quisiera pedir una cita.", "Je voudrais prendre rendez-vous."),
    ("My car does not start.", "Mi coche no arranca.", "Ma voiture ne démarre pas."),
    ("The car won't start.", "El coche no arranca.", "La voiture ne démarre pas."),
    ("The engine warning light is on.", "El testigo del motor está encendido.", "Le voyant moteur est allumé."),
    ("A warning light came on.", "Se ha encendido un testigo.", "Un voyant s'est allumé."),
    ("I have a flat tyre.", "Tengo una rueda pinchada.", "J'ai un pneu crevé."),
    ("The battery is flat.", "La batería está descargada.", "La batterie est à plat."),
    ("The car is under warranty.", "El coche está en garantía.", "La voiture est sous garantie."),
    ("The warranty has expired.", "La garantía ha caducado.", "La garantie a expiré."),
    ("Here is your invoice.", "Aquí tiene su factura.", "Voici votre facture."),
    ("You can pay by card.", "Puede pagar con tarjeta.", "Vous pouvez payer par carte."),
    ("Do you need a replacement car?", "¿Necesita un coche de sustitución?",
     "Avez-vous besoin d'un véhicule de remplacement ?"),
    ("We will wash your car for free.", "Lavaremos su coche gratis.", "Nous laverons votre voiture gratuitement."),
    ("Please leave the keys at the reception.", "Por favor, deje las llaves en la recepción.",
     "Veuillez laisser les clés à l'accueil."),
    ("The repair will take two days.", "La reparación durará dos días.", "La réparation prendra deux jours."),
    ("The spare part is out of stock.", "La pieza de repuesto está agotada.", "La pièce de rechange est en rupture de stock."),
    ("We have ordered the part.", "Hemos pedido la pieza.", "Nous avons commandé la pièce."),
    ("The car needs an oil change.", "El coche necesita un cambio de aceite.", "La voiture a besoin d'une vidange."),
    ("When was the last service?", "¿Cuándo fue el último mantenimiento?", "À quand remonte le dernier entretien ?"),
    ("How many kilometres has the car done?", "¿Cuántos kilómetros tiene el coche?",
     "Combien de kilomètres la voiture a-t-elle parcourus ?"),
    ("The test drive is free.", "La prueba de conducción es gratuita.", "L'essai sur route est gratuit."),
    ("Would you like a test drive?", "¿Quiere hacer una prueba de conducción?", "Voulez-vous faire un essai ?"),
    ("The new model is available at the dealership.", "El nuevo modelo está disponible en el concesionario.",
     "Le nouveau modèle est disponible en concession."),
    ("Our sales advisor will help you.", "Nuestro asesor comercial le ayudará.", "Notre conseiller commercial vous aidera."),
    ("Thank you for your patience.", "Gracias por su paciencia.", "Merci de votre patience."),
    ("Thank you for your visit.", "Gracias por su visita.", "Merci de votre visite."),
]


def casual(text: str) -> str:
    return re.sub(r"[\s.!?;:,]+$", "", text.lower()).lstrip("¿¡")


def domain_triplets(rng: random.Random) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for en, es, fr in PART_TEMPLATES:
        for p in PARTS:
            out.append((en.format(p[0]), es.format(p[1]), fr.format(p[2])))
    for en, es, fr in TIME_TEMPLATES:
        for t in TIMES:
            out.append((en.format(t[0]), es.format(t[1]), fr.format(t[2])))
    for en, es, fr in NOISE_TEMPLATES:
        for s in SUBJECTS:
            for n in NOISES:
                for m in MOMENTS:
                    out.append((en.format(s=s[0], n=n[0], m=m[0]), es.format(s=s[1], n=n[1], m=m[1]),
                                fr.format(s=s[2], n=n[2], m=m[2])))
    for en, es, fr in NAME_TEMPLATES:
        for _ in range(60):
            name = random_name(rng)
            out.append((en.format(name), es.format(name), fr.format(name)))
    out += PHRASES * 4  # fixed phrases are few, so repeat them
    return out
