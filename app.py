#!/usr/bin/env python3
"""
Épicéa Formation — Backend Flask
Mappings normés : NF C 18-510 + A1 + A2 (juin 2023)
                  NF C 18-550 (VE) + ED 6127 INRS (déc. 2020)
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import json, sqlite3, uuid, os, qrcode, io, base64, unicodedata, re
from datetime import datetime

app = Flask(__name__)
app.secret_key = "epicea-formation-2026"
MOT_DE_PASSE = "lik@m@-yet"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = "/tmp/epicea.db"
JSON_PATH = os.path.join(BASE_DIR, "epicea_enrichi.json")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def charger_corpus():
    try:
        with open(JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [d for d in data if "resume_pedagogique" in d]
    except Exception as e:
        print(f"Erreur chargement corpus: {e}")
        return []

CORPUS = charger_corpus()
CORPUS_INDEX = {d.get("unid"): d for d in CORPUS if d.get("unid")}
print(f"✓ Corpus chargé: {len(CORPUS)} fiches enrichies")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            nom TEXT, client TEXT, date TEXT, habilitation TEXT,
            created_at TEXT, masquee INTEGER DEFAULT 0,
            cas_fixes TEXT, nb_profils_fixes INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS reponses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, prenom TEXT, q1_metier TEXT,
            q2_environnements TEXT, q3_activites TEXT,
            q4_haute_tension TEXT, q5_anciennete TEXT, q6_taches TEXT,
            q7_electrise TEXT, q7_urgences TEXT, q8_contexte TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS cas_utilises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unid TEXT, used_at TEXT
        )
    """)
    conn.commit()
    # Migration : ajouter colonnes si absentes
    for col, definition in [
        ("masquee", "INTEGER DEFAULT 0"),
        ("cas_fixes", "TEXT"),
        ("nb_profils_fixes", "INTEGER DEFAULT 0"),
        ("cas_manuels", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE sessions ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # Colonne existe déjà
    conn.close()

init_db()

# ── Mappings normés NF C 18-510 A2 + NF C 18-550 ────────
MAPPING_TACHES_HABILITATIONS = {
    "remplacement":       ["BS", "BR", "B1", "B1V"],
    "depannage":          ["BR"],
    "direction":          ["B2", "B2V", "BC"],
    "essais":             ["BE essai", "BE mesure", "BE vérification", "BR", "B2V"],
    "voisinage":          ["B0", "H0", "H0V"],
    "fouilles":           ["B0", "H0"],
    "haute_tension":      ["H1", "H1V", "H2", "H2V", "HR", "HC"],
    "electronique":       ["BR", "BE mesure", "BE vérification"],
    "vehicule_electrique":["B1L", "B1VL", "B2L", "B2VL", "BRL", "BCL"],
}

MAPPING_ENVIRONNEMENT_SECTEUR = {
    "industrie":      ["industrie"],
    "btp":            ["BTP"],
    "tertiaire":      ["tertiaire", "services", "commerce"],
    "sensible":       ["tertiaire", "services", "collectivite"],
    "infrastructures":["transport", "industrie"],
}

MAPPING_ACTIVITE_RISQUE = {
    "hors_tension":    ["contact direct BT", "arc électrique", "court-circuit"],
    "intervention_bt": ["contact direct BT", "arc électrique", "court-circuit"],
    "direction":       ["contact direct BT", "arc électrique", "contact direct HTA"],
    "essais":          ["contact direct BT", "arc électrique", "induction"],
    "voisinage":       ["contact direct BT", "contact direct HTA", "induction"],
    "electronique":    ["contact direct BT", "court-circuit"],
    "haute_tension":   ["contact direct HTA", "arc électrique", "induction"],
}

SECTEURS_DOMESTIQUES = ["transport", "services", "tertiaire", "commerce"]
TYPES_NON_ELEC = ["non electrique", "non électrique"]

def get_cas_utilises():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT unid FROM cas_utilises WHERE used_at >= date('now','-30 days')")
        rows = c.fetchall(); conn.close()
        return set(r[0] for r in rows)
    except Exception:
        return set()

def sauvegarder_cas_utilises(unids):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for unid in unids:
            if unid:
                c.execute("INSERT INTO cas_utilises (unid, used_at) VALUES (?,?)",
                          (unid, datetime.now().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass

def calculer_score_electrique(fiche, profils_groupe):
    type_risque   = fiche.get("type_risque", "")
    secteur_fiche = fiche.get("secteur_normalise", "")
    habs_fiche    = fiche.get("habilitations_concernees", [])
    pertinence    = fiche.get("pertinence_electrique", "faible")
    gravite       = fiche.get("gravite", "")
    score = 0

    if pertinence == "faible" or type_risque in TYPES_NON_ELEC:
        return 0

    for profil in profils_groupe:
        ht = profil.get("q4_haute_tension", "non")
        taches = profil.get("q6_taches", [])
        activites = profil.get("q3_activites", [])
        environnements = profil.get("q2_environnements", [])
        anciennete = profil.get("q5_anciennete", "")

        if ht == "non":
            tous_bt = all(p.get("q4_haute_tension","non") == "non" for p in profils_groupe)
            if tous_bt:
                if type_risque in ["contact direct HTA", "vehicule electrique"]:
                    return 0
                if any(h in habs_fiche for h in ["H1","H1V","H2","H2V","HR","HC"]):
                    return 0
        if ht == "voisinage":
            if any(h in habs_fiche for h in ["H2","H2V","HR","HC"]):
                score -= 20

        for env in environnements:
            if secteur_fiche in MAPPING_ENVIRONNEMENT_SECTEUR.get(env, []):
                score += 40; break

        for activite in activites:
            if type_risque in MAPPING_ACTIVITE_RISQUE.get(activite, []):
                score += 30; break

        for tache in taches:
            if any(h in habs_fiche for h in MAPPING_TACHES_HABILITATIONS.get(tache,[])):
                score += 25; break

        if pertinence == "haute":   score += 20
        elif pertinence == "moyenne": score += 5

        if anciennete in ["5a10","plus10"] and gravite == "mortel": score += 10
        if anciennete == "moins2" and gravite == "léger":            score += 10

    if pertinence == "moyenne":
        score = score // 2
    return score

def selectionner_cas_non_electrique(profils, cas_deja_vus=None):
    if cas_deja_vus is None:
        cas_deja_vus = set()
    candidats = [d for d in CORPUS
                 if d.get("type_risque") in TYPES_NON_ELEC
                 and d.get("pertinence_electrique") != "faible"
                 and d.get("resume_pedagogique")]
    if not candidats: return None
    # Séparer frais et déjà vus
    frais = [c for c in candidats if c.get("unid","") not in cas_deja_vus]
    pool = frais if frais else candidats  # fallback si tout vu

    envs = set()
    for p in profils: envs.update(p.get("q2_environnements", []))
    for env in envs:
        matches = [c for c in pool
                   if c.get("secteur_normalise") in MAPPING_ENVIRONNEMENT_SECTEUR.get(env,[])]
        if matches:
            graves = [m for m in matches if m.get("gravite") in ["mortel","grave"]]
            return graves[0] if graves else matches[0]
    dom = [c for c in pool if c.get("secteur_normalise") in SECTEURS_DOMESTIQUES]
    if dom:
        graves = [d for d in dom if d.get("gravite") in ["mortel","grave"]]
        return graves[0] if graves else dom[0]
    return pool[0]

def fiche_vers_dict(fiche, tag_ouverture=False, score=0):
    texte_brut = fiche.get("texte_brut","") or fiche.get("texte_nettoye","")
    idx = texte_brut.find("sume de l")
    if idx > 0:
        debut = texte_brut.find(":", idx) + 1
        fin = texte_brut.find("Revenir", debut)
        if fin < 0: fin = texte_brut.find("Extrait", debut)
        if fin < 0: fin = len(texte_brut)
        candidat = texte_brut[debut:fin].strip()
        texte_propre = candidat if len(candidat) > 100 else texte_brut
    else:
        texte_propre = texte_brut
    return {
        "score": score, "tag_ouverture": tag_ouverture,
        "unid": fiche.get("unid",""),
        "numero": fiche.get("numero","?"),
        "secteur": fiche.get("secteur",""),
        "secteur_normalise": fiche.get("secteur_normalise",""),
        "resume_pedagogique": fiche.get("resume_pedagogique",""),
        "erreur_declenchante": fiche.get("erreur_declenchante",""),
        "type_risque": fiche.get("type_risque",""),
        "domaine_tension": fiche.get("domaine_tension",""),
        "gravite": fiche.get("gravite",""),
        "habilitations_concernees": fiche.get("habilitations_concernees",[]),
        "questions_animation": fiche.get("questions_animation",[]),
        "tags_norme": fiche.get("tags_norme",[]),
        "cause_organisationnelle": fiche.get("cause_organisationnelle",""),
        "texte_accident": texte_propre,
    }

def calculer_recommandations(profils, nb=8):
    if not profils: return []
    cas_deja_vus = get_cas_utilises()
    resultats = []
    cas_ne = selectionner_cas_non_electrique(profils, cas_deja_vus)
    if cas_ne:
        resultats.append(fiche_vers_dict(cas_ne, tag_ouverture=True, score=999))

    niveaux_groupe = set()
    for p in profils:
        for t in p.get("q6_taches", []):
            niveaux_groupe.update(MAPPING_TACHES_HABILITATIONS.get(t, []))

    scores = []
    for fiche in CORPUS:
        if fiche.get("type_risque") in TYPES_NON_ELEC: continue
        s = calculer_score_electrique(fiche, profils)
        if s > 0:
            if fiche.get("unid","") in cas_deja_vus: s = s // 3
            scores.append((s, fiche))
    scores.sort(key=lambda x: x[0], reverse=True)

    niveaux_couverts = set()
    cas_sel = []
    for score, fiche in scores:
        if len(cas_sel) >= (nb - 1): break
        habs = set(fiche.get("habilitations_concernees", []))
        nouveaux = (habs & niveaux_groupe) - niveaux_couverts
        if nouveaux or len(cas_sel) < 4:
            cas_sel.append((score, fiche))
            niveaux_couverts.update(habs & niveaux_groupe)

    if len(cas_sel) < (nb - 1):
        sel_ids = {id(f) for _,f in cas_sel}
        for score, fiche in scores:
            if id(fiche) not in sel_ids:
                cas_sel.append((score, fiche))
            if len(cas_sel) >= (nb - 1): break

    for score, fiche in cas_sel:
        resultats.append(fiche_vers_dict(fiche, score=score))

    sauvegarder_cas_utilises([f.get("unid","") for _,f in cas_sel])
    return resultats

def reconstruire_depuis_unids(unids_json):
    try:
        unids = json.loads(unids_json)
    except Exception:
        return None
    resultats = []
    for unid in unids:
        fiche = CORPUS_INDEX.get(unid)
        if fiche:
            tag = fiche.get("type_risque","") in TYPES_NON_ELEC
            resultats.append(fiche_vers_dict(fiche, tag_ouverture=tag))
    return resultats if resultats else None

# ── Routes ────────────────────────────────────────────────
@app.route("/")
def index(): return render_template("index.html")

@app.route("/login", methods=["GET","POST"])
def login():
    erreur = ""
    if request.method == "POST":
        if request.form.get("password") == MOT_DE_PASSE:
            session["logged_in"] = True
            return redirect(url_for("recherche"))
        erreur = "Mot de passe incorrect"
    return render_template("login.html", erreur=erreur)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/formateur")
@login_required
def formateur(): return render_template("formateur.html")

@app.route("/sondage/<session_id>")
def sondage(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    sess = c.fetchone(); conn.close()
    if not sess: return "Session introuvable", 404
    return render_template("sondage.html", session_id=session_id, session=sess)

@app.route("/resultats/<session_id>")
@login_required
def resultats(session_id): return render_template("resultats.html", session_id=session_id)

# ── API Sessions ──────────────────────────────────────────
@app.route("/api/sessions", methods=["GET"])
@login_required
def get_sessions():
    afficher_masquees = request.args.get("masquees","false") == "true"
    where = "" if afficher_masquees else "WHERE s.masquee = 0"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute(f"""
        SELECT s.id, s.nom, s.client, s.date, s.habilitation,
               s.created_at, s.masquee, COUNT(r.id) as nb_reponses
        FROM sessions s LEFT JOIN reponses r ON s.id = r.session_id
        {where} GROUP BY s.id ORDER BY s.created_at DESC
    """).fetchall()
    conn.close()
    return jsonify([{
        "id":row[0],"nom":row[1],"client":row[2],"date":row[3],
        "habilitation":row[4],"created_at":row[5],
        "masquee":bool(row[6]),"nb_reponses":row[7]
    } for row in rows])

@app.route("/api/sessions", methods=["POST"])
@login_required
def create_session():
    data = request.json
    sid = str(uuid.uuid4())[:8].upper()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO sessions (id,nom,client,date,habilitation,created_at,masquee)
        VALUES (?,?,?,?,?,?,0)
    """, (sid, data.get("nom",""), data.get("client",""),
          data.get("date",""), data.get("habilitation",""),
          datetime.now().isoformat()))
    conn.commit(); conn.close()
    return jsonify({"id": sid})

@app.route("/api/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
    row = c.fetchone(); conn.close()
    if not row: return jsonify({"error":"Session introuvable"}), 404
    return jsonify({"id":row[0],"nom":row[1],"client":row[2],
                    "date":row[3],"habilitation":row[4],"created_at":row[5]})

@app.route("/api/sessions/<session_id>/masquer", methods=["POST"])
@login_required
def masquer_session(session_id):
    data = request.json or {}
    masquee = 1 if data.get("masquee", True) else 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE sessions SET masquee=? WHERE id=?", (masquee, session_id))
    conn.commit(); conn.close()
    return jsonify({"ok":True,"masquee":bool(masquee)})

@app.route("/api/sessions/<session_id>", methods=["DELETE"])
@login_required
def delete_session(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM reponses WHERE session_id=?", (session_id,))
    c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    conn.commit(); conn.close()
    return jsonify({"ok":True})

@app.route("/api/sessions/<session_id>/qr", methods=["GET"])
def get_qr(session_id):
    url = f"{request.host_url.rstrip('/')}/sondage/{session_id}"
    img = qrcode.make(url)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return jsonify({"qr":b64,"url":url})

# ── API Réponses ──────────────────────────────────────────
@app.route("/api/reponses/<session_id>", methods=["POST"])
def save_reponse(session_id):
    data = request.json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO reponses
        (session_id,prenom,q1_metier,q2_environnements,q3_activites,
         q4_haute_tension,q5_anciennete,q6_taches,
         q7_electrise,q7_urgences,q8_contexte,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        session_id, data.get("prenom",""), data.get("q1_metier",""),
        json.dumps(data.get("q2_environnements",[]),ensure_ascii=False),
        json.dumps(data.get("q3_activites",[]),ensure_ascii=False),
        data.get("q4_haute_tension",""), data.get("q5_anciennete",""),
        json.dumps(data.get("q6_taches",[]),ensure_ascii=False),
        data.get("q7_electrise",""), data.get("q7_urgences",""),
        data.get("q8_contexte",""), datetime.now().isoformat()
    ))
    # Invalider les cas fixés pour forcer recalcul
    c.execute("UPDATE sessions SET cas_fixes=NULL, nb_profils_fixes=0 WHERE id=?",
              (session_id,))
    conn.commit(); conn.close()
    return jsonify({"ok":True})

# ── API Résultats ─────────────────────────────────────────
@app.route("/api/resultats/<session_id>", methods=["GET"])
def get_resultats(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    rows = c.execute("SELECT * FROM reponses WHERE session_id=?", (session_id,)).fetchall()
    profils = []
    for row in rows:
        profils.append({
            "prenom":row[2],"q1_metier":row[3],
            "q2_environnements":json.loads(row[4]) if row[4] else [],
            "q3_activites":json.loads(row[5]) if row[5] else [],
            "q4_haute_tension":row[6],"q5_anciennete":row[7],
            "q6_taches":json.loads(row[8]) if row[8] else [],
            "q7_electrise":row[9],"q7_urgences":row[10],"q8_contexte":row[11],
        })
    sess_row = c.execute(
        "SELECT cas_fixes, nb_profils_fixes, cas_manuels FROM sessions WHERE id=?", (session_id,)
    ).fetchone()
    conn.close()

    cas_fixes    = sess_row[0] if sess_row else None
    nb_fixes     = sess_row[1] if sess_row else 0
    cas_manuels_json = sess_row[2] if sess_row else None

    if profils and (cas_fixes is None or len(profils) != nb_fixes):
        recommandations = calculer_recommandations(profils, nb=8)
        unids = [r.get("unid","") for r in recommandations]
        conn2 = sqlite3.connect(DB_PATH)
        c2 = conn2.cursor()
        c2.execute("UPDATE sessions SET cas_fixes=?, nb_profils_fixes=? WHERE id=?",
                      (json.dumps(unids), len(profils), session_id))
        conn2.commit(); conn2.close()
    elif cas_fixes and profils:
        recommandations = reconstruire_depuis_unids(cas_fixes) or \
                          calculer_recommandations(profils, nb=8)
    else:
        recommandations = []

    stats = {
        "nb_stagiaires": len(profils),
        "environnements": {}, "anciennetes": {}, "haute_tension": 0,
        "electrises": len([p for p in profils if p.get("q7_electrise","jamais") != "jamais"]),
        "urgences":   len([p for p in profils if p.get("q7_urgences") == "oui"]),
    }
    for p in profils:
        for env in p.get("q2_environnements",[]):
            stats["environnements"][env] = stats["environnements"].get(env,0)+1
        anc = p.get("q5_anciennete","?")
        stats["anciennetes"][anc] = stats["anciennetes"].get(anc,0)+1
        if p.get("q4_haute_tension","non") != "non": stats["haute_tension"] += 1

    cas_manuels = []
    if cas_manuels_json:
        for unid in json.loads(cas_manuels_json):
            fiche = CORPUS_INDEX.get(unid)
            if fiche:
                cas_manuels.append(fiche_vers_dict(fiche))

    return jsonify({"profils":profils,"recommandations":recommandations,
                    "cas_manuels":cas_manuels,"stats":stats})

@app.route("/api/sessions/<session_id>/cas_manuels", methods=["POST"])
@login_required
def ajouter_cas_manuel(session_id):
    unid = (request.json or {}).get("unid", "")
    if not unid:
        return jsonify({"error": "unid requis"}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = c.execute("SELECT cas_manuels FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Session introuvable"}), 404
    cas = json.loads(row[0]) if row[0] else []
    if unid not in cas:
        cas.append(unid)
    c.execute("UPDATE sessions SET cas_manuels=? WHERE id=?", (json.dumps(cas), session_id))
    conn.commit(); conn.close()
    return jsonify({"ok": True, "nb": len(cas)})

@app.route("/api/sessions/<session_id>/cas_manuels/<unid>", methods=["DELETE"])
@login_required
def supprimer_cas_manuel(session_id, unid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    row = c.execute("SELECT cas_manuels FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Session introuvable"}), 404
    cas = [u for u in (json.loads(row[0]) if row[0] else []) if u != unid]
    c.execute("UPDATE sessions SET cas_manuels=? WHERE id=?", (json.dumps(cas), session_id))
    conn.commit(); conn.close()
    return jsonify({"ok": True})

@app.route("/api/corpus/stats", methods=["GET"])
def corpus_stats():
    return jsonify({
        "total": len(CORPUS),
        "haute_pertinence": len([d for d in CORPUS if d.get("pertinence_electrique")=="haute"]),
        "mortels": len([d for d in CORPUS if d.get("gravite")=="mortel"]),
    })

def _normaliser(t):
    if not t:
        return ""
    t = t.lower()
    t = unicodedata.normalize("NFD", t)
    return "".join(c for c in t if unicodedata.category(c) != "Mn")

@app.route("/recherche")
@login_required
def recherche():
    return render_template("recherche.html")

@app.route("/api/recherche", methods=["GET"])
@login_required
def api_recherche():
    q          = request.args.get("q", "").strip()
    habilitation = request.args.get("habilitation", "")
    secteur    = request.args.get("secteur", "")
    type_risque = request.args.get("type_risque", "")
    gravite    = request.args.get("gravite", "")
    page       = max(1, int(request.args.get("page", 1)))
    par_page   = 20

    termes = [_normaliser(t) for t in q.split() if t]

    resultats = []
    for fiche in CORPUS:
        if habilitation and habilitation not in fiche.get("habilitations_concernees", []):
            continue
        if secteur and fiche.get("secteur_normalise", "") != secteur:
            continue
        if type_risque and fiche.get("type_risque", "") != type_risque:
            continue
        if gravite and fiche.get("gravite", "") != gravite:
            continue

        if termes:
            haystack = _normaliser(" ".join(filter(None, [
                fiche.get("resume_pedagogique", ""),
                fiche.get("erreur_declenchante", ""),
                fiche.get("cause_organisationnelle", ""),
                fiche.get("secteur", ""),
                " ".join(fiche.get("tags_norme", [])),
                " ".join(fiche.get("mots_source", [])),
            ])))
            if not all(t in haystack for t in termes):
                continue

        resultats.append(fiche_vers_dict(fiche))

    total = len(resultats)
    debut = (page - 1) * par_page

    breakdown = {"mortel": 0, "grave": 0, "léger": 0, "inconnu": 0}
    for r in resultats:
        g = r.get("gravite") or "inconnu"
        breakdown[g] = breakdown.get(g, 0) + 1

    return jsonify({
        "total":     total,
        "page":      page,
        "par_page":  par_page,
        "pages":     max(1, (total + par_page - 1) // par_page),
        "breakdown": breakdown,
        "resultats": resultats[debut: debut + par_page],
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
