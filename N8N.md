# n8n — Piloter ZtudioZ depuis l'UI n8n

Le moteur (pipeline Python) tourne sur l'**hôte** : le container n8n (image alpine hardened) n'a ni `python3` ni `apk`/`apt`. n8n le pilote donc via :

- **Nœud SSH** : exécute les steps du pipeline sur l'hôte avec le venv du repo
  (`/home/smag/dev/ZtudioZ/.venv/bin/python`). Aucune modification du code/pipeline :
  chaque step appelle l'outillage existant (`pipeline.ztudioz_toolkit` /
  `pipeline.orchestrator`).
- **API preview `:8000`** : le FastAPI du repo (`api/`) sert l'aperçu des assets
  (images/vidéos/audio) dans les formulaires d'approbation + l'édition des plans
  storyboard (PUT).

## Container n8n (lancer avec)

```bash
docker run -it --rm \
 --name n8n \
 -p 5678:5678 \
 -e GENERIC_TIMEZONE="Europe/Paris" \
 -e TZ="Europe/Paris" \
 -e N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true \
 -e N8N_RUNNERS_ENABLED=true \
 -e NODES_EXCLUDE=[] \
 -v n8n_data:/home/node/.n8n \
 -v $HOME/shared:/home/node/.n8n-files \
 -v $HOME/dev/ZtudioZ:/home/node/ZtudioZ \
 docker.n8n.io/n8nio/n8n
```

## Prérequis (une fois)

1. **Clé SSH n8n → hôte** : le nœud SSH doit pouvoir exécuter du python sur l'hôte.
   Le container n8n se connecte à l'hôte via la passerelle du réseau Docker
   (`172.17.0.1:22`, confirmé joignable). Procédure complète :

   **a. Générer une paire de clés dédiée sur l'hôte** (utilisateur hôte : `smag`) :
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_ztudioz_n8n -C "n8n-ztudioz" -N ""
   # crée ~/.ssh/id_ed25519_ztudioz_n8n (privée) et ... .pub (publique)
   ```

   **b. Autoriser la clé publique pour `smag`** :
   ```bash
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   cat ~/.ssh/id_ed25519_ztudioz_n8n.pub >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   # s'assurer que sshd accepte la clé : PasswordAuthentication/pubkey ok,
   # pas de restriction AllowUsers/authorized_keys altérant ~/.ssh.
   ```

   **c. (Optionnel) Tester la connexion depuis l'hôte** :
   ```bash
   ssh -i ~/.ssh/id_ed25519_ztudioz_n8n -p 22 smag@172.17.0.1 'echo OK'
   ```
   (La porte 172.17.0.1:22 est joignable depuis le container : vérifié via `nc`.)

   **d. Créer la credential SSH dans n8n** : **Credentials → Add credential → SSH**
   → type **Private Key** :
   - **Host** : `172.17.0.1`
   - **Port** : `22`
   - **Username** : `smag`
   - **Private Key** : coller le contenu de `~/.ssh/id_ed25519_ztudioz_n8n` (fichier
     privé, *pas* le `.pub`).
   Enregistrer, puis sélectionner cette credential sur chaque nœud « Step * » du
   workflow.

   - (Alternative : credential « Password » avec un compte local hôte autorisant le
     login SSH par mot de passe.)

   Les nœuds « Step * » sont préconfigurés (credential `SSH Private Key account`,
   `cwd: /home/smag/dev/ZtudioZ`, champ **command** en mode **Expression**) :
   il ne faut **pas** repasser un champ en « Fixed ». Si un import perd la
   credential, la re-sélectionner sur chaque nœud SSH.
2. **API preview** (aperçu des assets dans les formulaires) :
   ```bash
   cd ~/dev/ZtudioZ && nohup .venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 >/tmp/ztudioz_api.log 2>&1 &
   ```

## Import du workflow

**Workflows → ⋯ → Import from File** → sélectionner `ZtudioZ.json` (racine du repo).
Activer le workflow, puis ouvrir l'URL du formulaire : nœud **Form Lancement** →
« Open Form ».

## Usage (non-dev)

1. Ouvrir l'URL du formulaire → renseigner **slug** (projet) et **numéro de scène**.
2. Le workflow enchaîne : `parse → fiches → sheets → storyboard` → (approbation) →
   `visualize` → (approbation) → `keyframes` → `produce` → (approbation) → `audio` →
   (approbation).
3. Chaque approbation affiche les assets produits (via `:8000`) + choix
   **Continuer / Arrêter (skip)** + remarque libre (journalisée dans l'exécution).

## Notes

- Respect des caches : les sheets/shots/vidéos existants sont réutilisés (cache MD5,
  vidéos >5KB non écrasées). Ré-exécuter le workflow pour une autre scène.
- Granularité : **une scène par run**. Le mode keyframes respecte
  `production.keyframes.enabled` de `config.yaml` (le `produce` i2v suit, en sautant
  les vidéos déjà générées).
- Le lancement manuel (bouton Execute) utilise le slug par défaut du nœud
  **Preparer Parametres** (`test_proj`) — penser à l'adapter pour un test rapide.
