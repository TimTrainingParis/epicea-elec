#!/usr/bin/env python3
"""
Épicéa Formation — Backend Flask
"""

from flask import Flask, render_template, request, jsonify, send_from_directory
import json
import sqlite3
import uuid
import os
import qrcode
import io
import base64
from datetime import datetime

app = Flask(__name__)
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS reponses (
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
    conn.commit()
    conn.close()

init_db()

# ── Moteur de matching ────────────────────────────────────
MAPPING_ACTIVITE_HABILITATION = {
    "remplacement": ["B1", "B1V", "B2", "BR"],
    "depannage": ["BR", "B2", "B2V"],
    "direction": ["B2", "B2V", "BC"],
    "essais": ["BE mesure", "BE essai", "BE vérification"],
    "voisinage": ["B0", "H0"],
    "haute_tension": ["H1", "H1V", "H2", "H2V", "HR", "HC"],
    "electronique": ["BR", "BE mesure"],
}

MAPPING_ENVIRONNEMENT_SECTEUR = {
    "industrie": ["industrie"],
    "btp": ["BTP"],
    "tertiaire": ["tertiaire", "services"],
    "sensible": ["tertiaire", "services", "collectivite"],
    "infrastructures": ["transport", "industrie"],
    "mixte": None,
}

def calculer_score(fiche, profils_groupe):
    """Calcule un score de pertinence pour une fiche selon les profils du groupe."""
    score = 0

    # Pertinence électrique de base
    p = fiche.get("pertinence_electrique", "faible")
    if p == "haute":
        score += 30
    elif p == "moyenne":
        score += 10

    for profil in profils_groupe:
        # Match habilitations
        habs_fiche = fiche.get("habilitations_concernees", [])
        taches = profil.get("q6_taches", "")
        for tache, habs in MAPPING_ACTIVITE_HABILITATION.items():
            if tache in taches:
                for h in habs:
                    if h in habs_fiche:
                        score += 15

        # Match environnement / secteur
        env = profil.get("q3_environnement", "")
        secteurs_cibles = MAPPING_ENVIRONNEMENT_SECTEUR.get(env, None)
        secteur_fiche = fiche.get("secteur_normalise", "")
        if secteurs_cibles and secteur_fiche in secteurs_cibles:
            score += 20

        # Haute tension
        ht = profil.get("q4_haute_tension", "non")
        if ht != "non" and fiche.get("type_risque", "").startswith("contact direct HTA"):
            score += 15

        # Électronique
        if "electronique" in taches and "electronique" in fiche.get("type_risque", ""):
            score += 20

        # Ancienneté — les expérimentés → accidents par banalisation
        anciennete = profil.get("q5_anciennete", "")
        if "10" in anciennete and fiche.get("gravite") == "mortel":
            score += 10

    return score

def calculer_recommandations(profils, nb=8):
    """Retourne les N fiches les mieux scorées pour un groupe."""
    if not profils:
        return []

    scores = []
    for fiche in CORPUS:
        s = calculer_score(fiche, profils)
        if s > 0:
            scores.append((s, fiche))

    scores.sort(key=lambda x: x[0], reverse=True)

    resultats = []
    for score, fiche in scores[:nb]:
        resultats.append({
            "score": score,
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

    return resultats

# ── Routes ────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/formateur")
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
def resultats(session_id):
    return render_template("resultats.html", session_id=session_id)

# ── API ───────────────────────────────────────────────────

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
        (session_id, prenom, q1_metier, q2_activite, q3_environnement,
         q4_haute_tension, q5_anciennete, q6_taches, created_at)
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
