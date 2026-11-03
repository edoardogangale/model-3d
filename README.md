# Model 3D — da foto a personaggio umano 3D con scheletro (GLB)

App web in italiano (usabile da iPad) che porta una o più foto, o una tavola di riferimento, fino a **un unico `.glb`** con mesh texturizzata,
scheletro umanoide **Mixamo** e skinning, validato prima del download.

> **Stato onesto, leggi prima di usarla.** Tutto ciò che sta *dopo* la ricostruzione 3D (pulizia mesh, orientamento, scheletro, pesi, conversione in posa A/T,
> decimazione con UV, esportazione GLB singolo, validazione, prova delle deformazioni, UI, coda di lavori) è implementato **e collaudato**.
> La **ricostruzione 3D dalla foto** però richiede un motore esterno (GPU o servizio a crediti) che **in questo ambiente non era utilizzabile**:
> non ho potuto eseguirla su una foto reale, quindi **la somiglianza con Babu non è stata verificata**. Dettagli in "Parti non collaudate".

## 1. Fattibilità: cosa ho verificato davvero

| Candidato | Esito della verifica (letti codice/README dai repository ufficiali) |
|---|---|
| **TRELLIS.2** (microsoft, licenza MIT) | Image-to-3D a **singola immagine**. Richiede **Linux + GPU NVIDIA ≥ 24 GB** (testato su A100/H100), CUDA 12.4. Non produce un rig. Pesi su Hugging Face (non raggiungibile da questo ambiente). → adattatore fornito (`server/tools/trellis2_worker.py`), **non eseguito**. |
| **UniRig** (VAST-AI, licenza MIT) | Autorigger: serve GPU ≥ 8 GB e Blender; scrive FBX; ha un template di scheletro `mixamo.yaml`. Pesi su Hugging Face. → **non integrato**: servirebbero una GPU per validarlo e un passo di importazione FBX. Al suo posto uso un rigger interno (vedi sotto). |
| **Blender** | Non installato e non scaricabile qui (download.blender.org bloccato). → **non usato**: pulizia/rig/export sono in Python puro (numpy/scipy/trimesh/meshoptimizer), quindi girano su qualunque CPU. |
| **Meshy** (servizio a pagamento) | Image-to-3D e **multi-image-to-3D (max 4 viste)** con texture. Adattatore `server/app/engines/meshy.py` scritto sulla documentazione pubblica, **non collaudato** (nessuna chiave API, rete verso api.meshy.ai bloccata). Costi a crediti, mostrati in UI, nessun acquisto automatico. |
| Higgsfield (connettore dell'assistente) | Ha modelli image-to-3D/rigging, ma è un connettore dell'assistente, **non un'API che l'app possa chiamare**, e l'account aveva 0 crediti. Non integrato. Puoi generare il GLB lì (o ovunque) e usare **"Importa GLB"**. |
| GPU / rete di questo ambiente | Nessuna GPU; solo PyPI/npm raggiungibili (Hugging Face, Blender, Replicate, fal bloccati). |

Nessuno di questi motori produce da solo un umanoide animabile: l'app aggiunge scheletro, pesi e posa.

## 2. Architettura

```
web/ (Vite + Three.js)            interfaccia a 8 fasi, viewer, ritagli touch, validazione nel browser (GLTFLoader)
server/app/
  main.py        API FastAPI (+ serve web/dist), token opzionale
  jobs.py        job su disco + coda con worker; stati reali per fase; ripresa dalle fasi completate
  images.py      ritagli, rettangoli di esclusione (scritte/cornici), rimozione sfondo uniforme
  engines/       image→3D: meshy (remoto), local_cmd (TRELLIS.2 o altro), fixture (SOLO collaudo, mai "pronto")
  meshprep.py    pulizia pavimento/sfondo/frammenti, +Y su, fronte +Z (dai piedi), scala/origine, decimazione con UV
  rigging.py     stima giunti, pesi (distanza geodesica di superficie, max 4), conversione di posa coerente
  skeleton.py    gerarchia Mixamo, FK, IBM
  glbio.py       scrittura/lettura GLB indipendenti (un solo file, texture incorporata)
  validate.py    validazione del file ESPORTATO + validatore Khronos (tools/gltf_validate.mjs)
```

Fasi: 1 carica → 2 viste/ritagli → 3 controllo → 4 genera (motore) → 5 pulizia + posa + scheletro (+ correzione giunti) → 6 deformazioni → 7 ottimizza → 8 validazione e download.
I job sono salvati in `data/jobs/<id>/`; dopo un riavvio le fasi interrotte risultano "Interrotta" e si riprendono da lì.

### Come vengono usate le foto
* Il motore riceve le **viste del corpo** (fronte, retro, profili, tre quarti). Meshy: fino a 4 (multivista). TRELLIS.2: **una sola** (la frontale): le altre **non influenzano la geometria** e l'app lo scrive nel log e in UI.
* Viso/capelli/vestiti/accessori restano **riferimento visivo** (confronto sovrapposto nel viewer): nessun motore integrato li usa come input separato.
* Tavola: si ritaglia **una vista per volta**; i rettangoli "Escludi" coprono scritte/etichette/cornici; lo sfondo uniforme viene rimosso. La UI ricorda che nella vista frontale la **sinistra anatomica** (es. lo smartwatch di Babu) sta a **destra nell'immagine**.

### Scheletro, pesi, posa
* Scheletro Mixamo (23 ossa: le 20 richieste + `HeadTop_End` e `LeftToeBase`/`RightToeBase`), rotazioni locali identità a riposo, traslazioni = offset tra giunti. Prefisso opzionale `mixamorig`.
* I giunti sono stimati da proporzioni anatomiche corrette con le sezioni della mesh. **Non è un autorigger generico**: richiede braccia e gambe distinguibili. Altrimenti segnala l'avviso e **correggi i giunti a mano** (tocca il puntino nel viewer o scegli dall'elenco, ±1 cm, "Salva e ricalcola pesi").
* Pesi: geodesici sulla superficie (il braccio non "prende" il busto per mera vicinanza), 4 influenze, normalizzati, lisciati.
* Posa di riposo A (40°, regolabile) o T: i vertici sono deformati con i **pesi già calcolati**, poi bind pose e inverse bind matrices derivano dallo scheletro **risultante**: restano coerenti per costruzione (il validatore misura ~1e-8). Piedi a y=0, gambe leggermente aperte (5° per lato).
* La decimazione avviene **prima** del rig (con UV preservate), perciò non serve trasferire i pesi.

### Validazione (`validate.py` + browser)
Strutturali (bloccanti): contenitore GLB, nessuna risorsa esterna, SkinnedMesh + skin, indici e range, valori finiti, triangoli ≤ 50.000 (obiettivo 30.000), 1–3 materiali, texture ≤ 2048, UV in [0,1], normali, trasformazioni, gerarchia Mixamo, pesi normalizzati e senza vertici scoperti, max 4 influenze, **IBM coerenti con la posa**, piedi a y=0, sinistra a +X, +Y su, +Z fronte, **deformazione reale** con 5 pose di prova calcolate dai dati del file, assenza di esplosioni, **gltf-validator Khronos**.
Nel browser: riapertura con **GLTFLoader**, pesi/indici, bind↔IBM, deformazione effettiva (`applyBoneTransform`), localizzazione dei pesi, **visibilità a 360°** (8 render).
Tre categorie distinte: *strutturali* (automatiche) · *compatibilità animazioni* (da provare: carica una tua animazione GLB nella fase 6) · *qualità/somiglianza* (a occhio).
**Il download "avatar pronto" è bloccato** se un controllo strutturale fallisce **o** se il modello non deriva da una ricostruzione reale (manichino di prova): in quel caso c'è solo la **BOZZA** (`*_BOZZA.glb`).

## 3. Installazione e avvio
Verificato con Python 3.13 e Node 22. Versioni: `requirements.txt` e `web/package.json` (three 0.170.0, vite 6.0.7, gltf-validator 2.0.0-dev.3.10).

```bash
./start.sh            # crea .venv, installa, compila l'interfaccia, avvia su http://127.0.0.1:8000
cp .env.example .env  # poi imposta MESHY_API_KEY e/o MODEL3D_LOCAL_CMD (mai committare .env)
```
Test: `. .venv/bin/activate && python -m pytest -q tests` (10 test). Test browser: `python tests/make_board.py && NODE_PATH=<percorso playwright> node tests/e2e_browser.cjs` con il server già avviato.

### Uso da iPad
1. Sul PC/server: in `.env` imposta `MODEL3D_TOKEN=<passphrase lunga>`, poi `./start.sh --lan` (rifiuta di partire senza token).
2. Dall'iPad (stessa rete Wi-Fi) apri Safari su `http://<IP-del-PC>:8000` e inserisci il token. Il traffico è HTTP in chiaro sulla LAN: usa una rete fidata oppure un reverse proxy HTTPS (Caddy/Tailscale).
3. "Aggiungi a Home" per lo schermo intero. Ritagli e viewer sono touch (un dito ruota, due dita zoom/pan, maniglie da 30 px). Il caricamento usa la libreria foto di iOS.

### Requisiti hardware effettivi
* App + rig + validazione: **CPU qualsiasi**, pochi GB di RAM (manichino da 30k triangoli: circa 1 s di rig). Nessuna GPU.
* Ricostruzione: **Meshy** = nessuna GPU ma crediti e Internet; **TRELLIS.2** = GPU NVIDIA ≥ 24 GB, Linux, CUDA 12.4 (dichiarato dai suoi autori).

## 3bis. TRELLIS.2 su un PC con GPU (motore locale)

Il motore locale lancia il worker **sulla stessa macchina dove gira l'app**, perché le viste vengono passate come percorso di file.
Quindi **l'app va avviata sul PC con GPU**, non nel Codespace (che non ha GPU). Dal tuo iPad apri l'app con `./start.sh --lan` e il token.

Requisiti: Linux, GPU NVIDIA da 24 GB, driver NVIDIA, CUDA 12.4 (o compatibile), Miniconda/Miniforge, Git, circa 30-60 minuti per la compilazione e alcuni GB di pesi.

```bash
git clone https://github.com/edoardogangale/model-3d && cd model-3d
bash server/tools/trellis2_setup.sh          # clona TRELLIS.2 in ~/TRELLIS.2 e installa tutto (script ufficiale setup.sh)
cp .env.example .env                         # poi sostituisci /home/USER con il tuo percorso e imposta MODEL3D_TOKEN
./start.sh --lan
```

Test manuale del worker prima dell'app: `conda activate trellis2 && python ~/TRELLIS.2/model3d_worker.py --image ~/TRELLIS.2/assets/example_image/T.png --out /tmp/t.glb`.

Stato: lo script di installazione e il worker sono scritti sulla documentazione ufficiale di TRELLIS.2, ma **non sono stati eseguiti**: l'ambiente di sviluppo non aveva GPU né accesso ai pesi su Hugging Face. Il cablaggio del motore locale è coperto da test con un finto worker.

## 4. Verifiche eseguite (in questo ambiente, 03/11/2026)
* `pytest`: 10/10 (inclusi 3 test del motore locale con finto worker). Stima dei giunti sul manichino (errore max < 7 cm su proporzioni volutamente diverse da quelle statistiche), pesi normalizzati ≤ 4, posa A a 40° ± 1°, flusso API completo, blocco del download "pronto" per il manichino, rilettura dei byte del GLB (nessuna URI esterna, skin presente), **rifiuto di un GLB statico, di un file corrotto e di pesi azzerati**, autenticazione a token.
* Browser reale (Chromium headless con Playwright, touch): carica tavola → ritaglio con maniglie → vista "Fronte" → rimozione sfondo → motore manichino → pulizia/rig → anteprima → 4 pose di prova → validazione. **GLTFLoader apre il file**: 23 ossa, IBM coerenti (2.8e-8), pesi ok, deformazione reale, 360° ok. Screenshot ispezionati.
* **gltf-validator Khronos: 0 errori, 0 avvisi** sul GLB esportato (1,19 MB, 30.000 triangoli, 1 materiale, texture 512×1024).
* Il validatore ha intercettato bug veri durante lo sviluppo (nodo mesh non radice; lati quasi nulli che falsavano la misura di stiramento).

## 5. Parti NON collaudate / limiti noti
1. **Ricostruzione da foto reale: mai eseguita.** Meshy e TRELLIS.2 sono adattatori scritti su documentazione/codice ufficiali ma non provati. Il caso **Babu non è stato generato**: la tavola allegata non era nel repository (l'unica immagine in `smile-coach/assets` è il logo Smilum). Somiglianza, capelli ricci con punte caramello, camicetta floreale, jeans, scarpe e smartwatch **non sono verificati**.
2. Il collaudo di rig ed esportazione usa un **manichino sintetico**: dimostra l'esportazione, non la ricostruzione.
3. **Rigger euristico**: testato solo su una figura sintetica con braccia e gambe separate. Su mesh reali (capelli lunghi sulle spalle, braccia attaccate ai fianchi, vestiti larghi) aspettati giunti da correggere a mano. Niente dita: mani e piedi hanno un solo osso (più la punta del piede).
4. **Capelli, vestiti e accessori come oggetti separati: non implementato.** L'output è una sola mesh, un materiale, una texture; i capelli seguono i pesi geodesici della mesh unica.
5. **Orientamento frontale** stimato dai piedi (con correzione automatica di 180°): se non è determinabile lo segnala e c'è la rotazione manuale. Non riconosce un viso.
6. **Compatibilità con il tuo gioco non verificata**: il repository del gioco non è collegato. Uso rotazioni locali identità e nomi Mixamo senza prefisso (opzione `mixamorig`). Gli orientamenti locali delle ossa Mixamo originali sono diversi: prova con una tua animazione nella fase 6 prima di fidarti.
7. Rimozione sfondo = colore uniforme stimato dal bordo (non segmentazione semantica): un indumento chiaro su sfondo chiaro può sparire (successo nel test). Regola la tolleranza o escludi a mano.
8. Nessuna compressione (Draco/Meshopt/KTX2), come richiesto. Nessuna animazione esportata.
9. Il repository `smile-coach` non è stato toccato. L'inserimento in `public/avatars/models/babu.glb` e il collegamento a "Corpo completo 3D" **non sono stati fatti** (repository del gioco non disponibile).
