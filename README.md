# Épicéa Formation — Guide de déploiement

## Structure du projet

```
epicea_app/
├── app.py                  # Backend Flask
├── requirements.txt        # Dépendances Python
├── Procfile               # Config Railway
├── epicea_enrichi.json    # Corpus (à ajouter)
└── templates/
    ├── index.html         # Redirection
    ├── formateur.html     # Interface formateur
    ├── sondage.html       # Sondage stagiaires
    └── resultats.html     # Résultats + cas recommandés
```

## Déploiement sur Railway

### 1. Préparer le dossier

Copie `epicea_enrichi.json` dans le dossier `epicea_app/`.

### 2. Créer un dépôt GitHub

```bash
cd epicea_app
git init
git add .
git commit -m "Initial commit"
```

Puis sur github.com : créer un nouveau dépôt public, et pousser :

```bash
git remote add origin https://github.com/TON_COMPTE/epicea-formation.git
git push -u origin main
```

### 3. Déployer sur Railway

1. Va sur railway.app
2. Connecte-toi avec GitHub
3. "New Project" → "Deploy from GitHub repo"
4. Sélectionne ton dépôt
5. Railway détecte automatiquement le Procfile et déploie

### 4. Utiliser l'application

- Interface formateur : https://TON_APP.railway.app/formateur
- Sondage stagiaire : https://TON_APP.railway.app/sondage/SESSION_ID
- Résultats : https://TON_APP.railway.app/resultats/SESSION_ID

## Utilisation

1. Ouvre l'interface formateur
2. Crée une session (nom, client, date, habilitation)
3. Un QR code s'affiche — projette-le ou partage le lien
4. Les stagiaires scannent et remplissent le sondage (2 min)
5. Tu vois les profils arriver en temps réel
6. Les 8 cas Épicéa les plus pertinents pour ce groupe s'affichent
7. Clique sur un cas pour voir les questions d'animation

## Notes

- La base de données SQLite (epicea.db) est créée automatiquement au premier lancement
- Les sessions et réponses sont persistées entre les redémarrages
- L'actualisation des résultats est automatique toutes les 15 secondes
