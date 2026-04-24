#!/usr/bin/env python3
"""
Épicéa Formation — Backend Flask
"""

from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
from functools import wraps
import json
import sqlite3
import uuid
import os
import qrcode
import io
import base64
from datetime import datetime

app = Flask(__name__)
app.secret_key = "epicea-formation-2026"
MOT_DE_PASSE = "lik@m@-yet"

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "epicea.db")
JSON_PATH = os.path.join(BASE_DIR, "epicea_enrichi.json")

# ── Chargement du corpus ──────────────────────────────────
def charger_corpus():
    try:
        with open(JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # Garder uniquement les fiches enrichies
        return [d for d in data if "resume_pedagogique" in d]
    except Exception as e:
        print(f"Erreur chargement corpus: {e}")
        return []

CORPUS = charger_corpus()
print(f"✓ Corpus chargé: {len(CORPUS)} fiches enrichies")

# ── Base de données ───────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            nom TEXT,
            client TEXT,
            date TEXT,
            habilitation TEXT,
            created_at TEXT
        )
    """)
    c.execute("DROP TABLE IF EXISTS reponses")
    c.execute("""
        CREATE TABLE reponses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            prenom TEXT,
            q1_metier TEXT,
            q2_environnements TEXT,
            q3_activites TEXT,
            q4_haute_tension TEXT,
            q5_anciennete TEXT,
            q6_taches TEXT,
            q7_electrise TEXT,
            q7_urgences TEXT,
            q8_contexte TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cas_utilises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client TEXT,
            unid TEXT,
            used_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ── Moteur de matching ────────────────────────────────────
MAPPING_TACHES_HABILITATIONS = {
    "remplacement": ["B1", "B1V", "BR"],
    "depannage": ["BR", "B2", "B2V"],
    "direction": ["B2", "B2V", "BC"],
    "essais": ["BE mesure", "BE essai", "BE vérification"],
    "voisinage": ["B0", "H0", "BF", "HF"],
    "haute_tension": ["H1", "H1V", "H2", "H2V", "HR", "HC"],
    "electronique": ["BR", "BE mesure"],
}

MAPPING_ENVIRONNEMENT_SECTEUR = {
    "industrie": ["industrie"],
    "btp": ["BTP"],
    "tertiaire": ["tertiaire", "services", "commerce"],
    "sensible": ["tertiaire", "services", "collectivite"],
    "infrastructures": ["transport", "industrie"],
}

MAPPING_ACTIVITE_RISQUE = {
    "hors_tension": ["contact direct BTA", "arc électrique", "court-circuit"],
    "intervention_bt": ["contact direct BTA", "arc électrique", "court-circuit"],
    "direction": ["contact direct BTA", "arc électrique", "contact direct HTA"],
    "essais": ["contact direct BTA", "arc électrique", "induction"],
    "voisinage": ["contact direct BTA", "contact direct HTA", "induction"],
    "electronique": ["contact direct BTA", "court-circuit"],
    "haute_tension": ["contact direct HTA", "arc électrique", "induction"],
}

SECTEURS_DOMESTIQUES = ["transport", "services", "tertiaire", "commerce"]

def calculer_score_electrique(fiche, profils_groupe):
    score = 0
    type_risque = fiche.get("type_risque", "")
    secteur_fiche = fiche.get("secteur_normalise", "")
    habs_fiche = fiche.get("habilitations_concernees", [])
    pertinence = fiche.get("pertinence_electrique", "faible")
    gravite = fiche.get("gravite", "")

    if pertinence == "faible":
        return 0
    if type_risque in ["non electrique", "non électrique"]:
        return 0

    for profil in profils_groupe:
        ht = profil.get("q4_haute_tension", "non")
        taches = profil.get("q6_taches", [])
        activites = profil.get("q3_activites", [])
        environnements = profil.get("q2_environnements", [])
        anciennete = profil.get("q5_anciennete", "")

        if ht == "non":
            # Exclure HTA seulement si TOUS les profils sont BT
            tous_bt = all(p.get("q4_haute_tension", "non") == "non" for p in profils_groupe)
            if tous_bt:
                if type_risque in ["contact direct HTA", "vehicule electrique"]:
                    return 0
                if any(h in habs_fiche for h in ["H1", "H1V", "H2", "H2V", "HR", "HC"]):
                    return 0

        if ht == "voisinage":
            if any(h in habs_fiche for h in ["H2", "H2V", "HR", "HC"]):
                score -= 20

        for env in environnements:
            secteurs_cibles = MAPPING_ENVIRONNEMENT_SECTEUR.get(env, [])
            if secteur_fiche in secteurs_cibles:
                score += 40
                break

        for activite in activites:
            risques_cibles = MAPPING_ACTIVITE_RISQUE.get(activite, [])
            if type_risque in risques_cibles:
                score += 30
                break

        for tache in taches:
            habs_cibles = MAPPING_TACHES_HABILITATIONS.get(tache, [])
            for h in habs_cibles:
                if h in habs_fiche:
                    score += 25
                    break

        if pertinence == "haute":
            score += 20
        elif pertinence == "moyenne":
            score += 5

        if anciennete in ["5a10", "plus10"] and gravite == "mortel":
            score += 10
        if anciennete == "moins2" and gravite == "léger":
            score += 10

    if pertinence == "moyenne":
        score = score // 2

    return score


def selectionner_cas_non_electrique(profils):
    candidats = [
        d for d in CORPUS
        if d.get("type_risque") in ["non electrique", "non électrique"]
        and d.get("pertinence_electrique") != "faible"
        and d.get("resume_pedagogique")
    ]
    if not candidats:
        return None

    environnements_groupe = set()
    for p in profils:
        environnements_groupe.update(p.get("q2_environnements", []))

    for env in environnements_groupe:
        secteurs_cibles = MAPPING_ENVIRONNEMENT_SECTEUR.get(env, [])
        matches = [c for c in candidats if c.get("secteur_normalise") in secteurs_cibles]
        if matches:
            graves = [m for m in matches if m.get("gravite") in ["mortel", "grave"]]
            return graves[0] if graves else matches[0]

    domestiques = [c for c in candidats if c.get("secteur_normalise") in SECTEURS_DOMESTIQUES]
    if domestiques:
        graves = [d for d in domestiques if d.get("gravite") in ["mortel", "grave"]]
        return graves[0] if graves else domestiques[0]

    return candidats[0]


def get_cas_utilises():
    """Récupère les UNIDs utilisés ce mois-ci."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT unid FROM cas_utilises
        WHERE used_at >= date('now', '-30 days')
    """)
    rows = c.fetchall()
    conn.close()
    return set(r[0] for r in rows)

def sauvegarder_cas_utilises(unids):
    """Sauvegarde les UNIDs utilisés."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    from datetime import datetime as dt
    for unid in unids:
        c.execute("""
            INSERT INTO cas_utilises (client, unid, used_at)
            VALUES (?, ?, ?)
        """, ("", unid, dt.now().isoformat()))
    conn.commit()
    conn.close()

def calculer_recommandations(profils, nb=8, client=None):
    if not profils:
        return []
    
    cas_deja_vus = get_cas_utilises()

    resultats = []

    cas_non_elec = selectionner_cas_non_electrique(profils)
    if cas_non_elec:
        resultats.append({
            "score": 999,
            "tag_ouverture": True,
            "numero": cas_non_elec.get("numero", "?"),
            "secteur": cas_non_elec.get("secteur", ""),
            "secteur_normalise": cas_non_elec.get("secteur_normalise", ""),
            "resume_pedagogique": cas_non_elec.get("resume_pedagogique", ""),
            "erreur_declenchante": cas_non_elec.get("erreur_declenchante", ""),
            "type_risque": cas_non_elec.get("type_risque", ""),
            "gravite": cas_non_elec.get("gravite", ""),
            "habilitations_concernees": cas_non_elec.get("habilitations_concernees", []),
            "questions_animation": cas_non_elec.get("questions_animation", []),
            "tags_norme": cas_non_elec.get("tags_norme", []),
            "cause_organisationnelle": cas_non_elec.get("cause_organisationnelle", ""),
        })

    niveaux_groupe = set()
    for p in profils:
        for tache in p.get("q6_taches", []):
            niveaux_groupe.update(MAPPING_TACHES_HABILITATIONS.get(tache, []))

    scores = []
    for fiche in CORPUS:
        if fiche.get("type_risque") in ["non electrique", "non électrique"]:
            continue
        s = calculer_score_electrique(fiche, profils)
        if s > 0:
            scores.append((s, fiche))
    # Pénaliser les cas déjà utilisés avec ce client
    scores_ajustes = []
    for s, fiche in scores:
        unid = fiche.get("unid", "")
        if unid in cas_deja_vus:
            s = s // 3  # Pénalité 66% — toujours utilisable si pas d'alternative
        scores_ajustes.append((s, fiche))
    scores = scores_ajustes
    scores.sort(key=lambda x: x[0], reverse=True)

    niveaux_couverts = set()
    cas_selectionnes = []

    for score, fiche in scores:
        if len(cas_selectionnes) >= (nb - 1):
            break
        habs_fiche = set(fiche.get("habilitations_concernees", []))
        nouveaux_niveaux = habs_fiche & niveaux_groupe - niveaux_couverts
        if nouveaux_niveaux or len(cas_selectionnes) < 4:
            cas_selectionnes.append((score, fiche))
            niveaux_couverts.update(habs_fiche & niveaux_groupe)

    if len(cas_selectionnes) < (nb - 1):
        for score, fiche in scores:
            if (score, fiche) not in cas_selectionnes:
                cas_selectionnes.append((score, fiche))
            if len(cas_selectionnes) >= (nb - 1):
                break

    for score, fiche in cas_selectionnes:
        resultats.append({
            "score": score,
            "tag_ouverture": False,
            "numero": fiche.get("numero", "?"),
            "secteur": fiche.get("secteur", ""),
            "secteur_normalise": fiche.get("secteur_normalise", ""),
            "resume_pedagogique": fiche.get("resume_pedagogique", ""),
            "erreur_declenchante": fiche.get("erreur_declenchante", ""),
            "type_risque": fiche.get("type_risque", ""),
            "gravite": fiche.get("gravite", ""),
            "habilitations_concernees": fiche.get("habilitations_concernees", []),
            "questions_animation": fiche.get("questions_animation", []),
            "tags_norme": fiche.get("tags_norme", []),
            "cause_organisationnelle": fiche.get("cause_organisationnelle", ""),
        })

    # Sauvegarder les cas utilisés pour la mémoire client
    unids_utilises = [r.get("unid", "") for r in resultats if r.get("unid")]
    # unid non présent dans resultats — on le récupère depuis les fiches
    sauvegarder_cas_utilises([
        f.get("unid","") for _, f in cas_selectionnes
    ])

    return resultats



@app.route("/")
def index():
    return render_template("index.html")

@app.route("/formateur")
@login_required
def formateur():
    return render_template("formateur.html")

@app.route("/sondage/<session_id>")
def sondage(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    session = c.fetchone()
    conn.close()
    if not session:
        return "Session introuvable", 404
    return render_template("sondage.html", session_id=session_id, session=session)

@app.route("/resultats/<session_id>")
@login_required
def resultats(session_id):
    return render_template("resultats.html", session_id=session_id)

@app.route("/login", methods=["GET", "POST"])
def login():
    erreur = ""
    if request.method == "POST":
        if request.form.get("password") == MOT_DE_PASSE:
            session["logged_in"] = True
            return redirect(url_for("formateur"))
        erreur = "Mot de passe incorrect"
    return render_template("login.html", erreur=erreur)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/api/sessions", methods=["GET"])
def get_sessions():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT s.*, COUNT(r.id) as nb_reponses
        FROM sessions s
        LEFT JOIN reponses r ON s.id = r.session_id
        GROUP BY s.id
        ORDER BY s.created_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    sessions = []
    for row in rows:
        sessions.append({
            "id": row[0], "nom": row[1], "client": row[2],
            "date": row[3], "habilitation": row[4],
            "created_at": row[5], "nb_reponses": row[6]
        })
    return jsonify(sessions)

@app.route("/api/sessions", methods=["POST"])
def create_session():
    data = request.json
    session_id = str(uuid.uuid4())[:8].upper()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO sessions (id, nom, client, date, habilitation, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        data.get("nom", ""),
        data.get("client", ""),
        data.get("date", ""),
        data.get("habilitation", ""),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()
    return jsonify({"id": session_id})

@app.route("/api/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Session introuvable"}), 404
    return jsonify({
        "id": row[0], "nom": row[1], "client": row[2],
        "date": row[3], "habilitation": row[4], "created_at": row[5]
    })

@app.route("/api/sessions/<session_id>/qr", methods=["GET"])
def get_qr(session_id):
    base_url = request.host_url.rstrip("/")
    url = f"{base_url}/sondage/{session_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return jsonify({"qr": b64, "url": url})

@app.route("/api/reponses/<session_id>", methods=["POST"])
def save_reponse(session_id):
    data = request.json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO reponses
        (session_id, prenom, q1_metier, q2_environnements, q3_activites,
         q4_haute_tension, q5_anciennete, q6_taches, q7_electrise, q7_urgences, q8_contexte, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        data.get("prenom", ""),
        data.get("q1_metier", ""),
        json.dumps(data.get("q2_environnements", []), ensure_ascii=False),
        json.dumps(data.get("q3_activites", []), ensure_ascii=False),
        data.get("q4_haute_tension", ""),
        data.get("q5_anciennete", ""),
        json.dumps(data.get("q6_taches", []), ensure_ascii=False),
        data.get("q7_electrise", ""),
        data.get("q7_urgences", ""),
        data.get("q8_contexte", ""),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/resultats/<session_id>", methods=["GET"])
def get_resultats(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM reponses WHERE session_id=?", (session_id,))
    rows = c.fetchall()
    conn.close()

    profils = []
    for row in rows:
        profils.append({
            "prenom": row[2],
            "q1_metier": row[3],
            "q2_environnements": json.loads(row[4]) if row[4] else [],
            "q3_activites": json.loads(row[5]) if row[5] else [],
            "q4_haute_tension": row[6],
            "q5_anciennete": row[7],
            "q6_taches": json.loads(row[8]) if row[8] else [],
            "q7_electrise": row[9],
            "q7_urgences": row[10],
            "q8_contexte": row[11],
        })

    recommandations = calculer_recommandations(profils, nb=8)

    # Stats groupe
    stats = {
        "nb_stagiaires": len(profils),
        "environnements": {},
        "anciennetes": {},
        "haute_tension": 0,
        "electrises": len([p for p in profils if p.get("q7_electrise", "jamais") != "jamais"]),
        "urgences": len([p for p in profils if p.get("q7_urgences") == "oui"]),
    }
    for p in profils:
        for env in p.get("q2_environnements", []):
            stats["environnements"][env] = stats["environnements"].get(env, 0) + 1
        anc = p.get("q5_anciennete", "?")
        stats["anciennetes"][anc] = stats["anciennetes"].get(anc, 0) + 1
        if p.get("q4_haute_tension", "non") != "non":
            stats["haute_tension"] += 1

    return jsonify({
        "profils": profils,
        "recommandations": recommandations,
        "stats": stats,
    })

@app.route("/api/corpus/stats", methods=["GET"])
def corpus_stats():
    return jsonify({
        "total": len(CORPUS),
        "haute_pertinence": len([d for d in CORPUS if d.get("pertinence_electrique") == "haute"]),
        "mortels": len([d for d in CORPUS if d.get("gravite") == "mortel"]),
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
