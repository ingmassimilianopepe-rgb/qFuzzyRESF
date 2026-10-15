# Pipeline semantica RESF–FUZZY v8

La v8 implementa una ricostruzione ispezionabile dalla nuvola agli oggetti IFC. Il metodo della bozza di paper è una base da sviluppare: regole, soglie e assunzioni sono esplicite e modificabili. Il nuovo percorso usa `semantic_pipeline.py` e i moduli `resf_geometry`, `resf_fuzzy`, `resf_objects`, `resf_ifc`; non richiama i recuperi di involucro delle versioni storiche.

L'output distingue geometria osservata, geometria inferita e ipotesi da verificare. **Un grado fuzzy non è una probabilità di correttezza; un IFC valido non dimostra che la ricostruzione corrisponda all'edificio.**

## Cosa ricostruisce

| Oggetto | Evidenza richiesta e risultato attuale |
| --- | --- |
| Muro | Superficie verticale planare, estensione, continuità, normali e contatti architettonici; eventuale evidenza semantica esterna. `IfcWall` con provenienza e controllo geometrico. |
| Muro parzialmente occluso | Supporto geometrico osservato, continuazione e raggi di scansione che sostengano l'occlusione. La parte inferita rimane dichiarata. |
| Pavimento | Occupazione orizzontale presso il livello inferiore; `IfcSlab` estruso verso il basso con spessore assunto. |
| Soffitto | Occupazione orizzontale presso il livello superiore; `IfcCovering` di tipo `CEILING`, estruso verso l'alto con spessore assunto. |
| Spazio | Poligono chiuso del grafo dei muri accettati; `IfcSpace` con destinazione d'uso sconosciuta. Il confine segue gli assi dei muri, non le superfici interne nette. |
| Apertura generica | Contorno osservato e raggi che attraversano il muro; `IfcOpeningElement` collegato al muro da `IfcRelVoidsElement`. |
| Porta o finestra | Contorno compatibile e probabilità semantiche indipendenti della relativa classe. Apertura più `IfcDoor`/`IfcWindow` con `IfcRelFillsElement`. |

Per porte e finestre si producono posizione, larghezza e altezza. Anta, vetro, montanti, telaio e materiali non sono ricostruiti: l'entità di riempimento non possiede un solido di assemblaggio inventato. Il vuoto nel muro è invece modellato e verificato.

Pilastri, scale, coperture, facciate continue e muri curvi o inclinati sono fuori dal perimetro di questa versione. Non è incluso un classificatore semantico addestrato. Senza probabilità esterne possono essere inferiti muri e superfici orizzontali; una zona senza punti rimane un'ipotesi, salvo evidenza indipendente di passaggio libero.

## Installazione e primo avvio

Dalla radice del repository, con Python 3.10 o successivo:

```bash
python -m pip install -r backend/requirements.txt
python examples/generate_semantic_room.py --output demo_v8
python backend/semantic_resf_cli.py --request demo_v8/request.json
```

Il generatore crea una stanza ruotata di 17°, aperture e un pannello di arredo. `semantics.npz` contiene **etichette sintetiche note al generatore**: serve a controllare la pipeline quando l'evidenza semantica è disponibile, non misura la capacità di riconoscerla da un rilievo reale.

Per una propria nuvola:

```bash
python backend/semantic_resf_cli.py --input rilievo.xyz --output risultati_run_01
```

Ogni esecuzione richiede una nuova cartella di risultato. La pipeline rifiuta cartelle con output di esecuzioni precedenti per evitare di mescolare artefatti. `--no-ifc` mantiene analisi, JSON e CSV senza esportare IFC.

## Contratto di ingresso e coordinate

Sono accettati testo XYZ a colonne separate da spazi, `.npy` con matrice `N × 3`, `.npz` con chiave `points`, `.las` e `.laz`. Per LAS/LAZ la dipendenza aggiuntiva è:

```bash
python -m pip install "laspy[lazrs]"
```

La pipeline assume verticale lungo **+Z**. Il rilevamento delle direzioni orizzontali non richiede un edificio allineato agli assi X/Y. La gravità non viene stimata: occorre orientare il rilievo prima dell'elaborazione.

Il sistema interno usa metri:

```text
coordinate_locali_m = coordinate_input × scale_to_m − coordinate_origin
```

`scale_to_m` vale `1.0` per default; per coordinate in millimetri usare `0.001`. `coordinate_origin` è espresso in metri dopo la conversione. Se omesso, viene scelto il minimo XYZ dei punti finiti. Questa trasformazione, gli hash SHA-256 degli ingressi e la configurazione vengono registrati. Gli intervalli `storeys` devono essere espressi nel sistema locale risultante.

L'IFC usa normalmente coordinate locali. `restore_global_coordinates: true` applica l'origine al posizionamento del sito; non assegna un sistema cartografico o un codice EPSG. I file JSON/CSV restano nel sistema locale descritto nei metadati.

Un esempio di `config.json` è:

```json
{
  "scale_to_m": 1.0,
  "voxel_m": 0.04,
  "normal_neighbors": 24,
  "plane_tolerance_m": 0.04,
  "plane_spacing_m": 0.02,
  "raster_cell_m": 0.10,
  "max_points": 500000,
  "decision_threshold": 0.55,
  "geometric_feedback_threshold": 0.55,
  "assumed_wall_thickness_m": 0.20,
  "pair_wall_faces": false
}
```

```bash
python backend/semantic_resf_cli.py --input rilievo.xyz --config config.json --output risultati_run_02
```

Se i livelli non sono riconosciuti correttamente, aggiungere per esempio `"storeys": [[0.0, 3.0], [3.3, 6.3]]`, dopo aver verificato le quote locali. I livelli automatici derivano da superfici orizzontali osservate; in loro assenza si conserva un intervallo verticale a bassa confidenza, senza suddivisioni periodiche inventate.

Il filtraggio elimina coordinate non finite e seleziona campioni originali tramite voxelizzazione e limite di punti. `source_indices` mantiene il collegamento esatto alle righe originali. Il diradamento può perdere dettagli: una cella più piccola non aumenta l'informazione disponibile nel rilievo.

## Evidenza semantica e origini degli scanner

Le probabilità semantiche si forniscono in un NPZ con vettori di lunghezza `N`, riferiti **alle righe originali prima di filtraggio e campionamento**. Le classi attualmente utilizzate sono `wall`, `furniture`, `door`, `window`. I valori devono essere finiti, compresi in `[0,1]` e la somma delle classi fornite deve essere al massimo uno per punto. Le classi assenti rimangono sconosciute.

```python
import numpy as np

# p_wall, p_furniture, p_door, p_window provengono da un classificatore
# indipendente o da annotazioni verificate; ciascuno ha forma (N,).
np.savez_compressed("semantics.npz", wall=p_wall, furniture=p_furniture,
                    door=p_door, window=p_window)
```

Le origini scanner sono un `.npy` di forma `(N, 3)`, con l'origine di acquisizione di ciascun ritorno, oppure `(3,)` solo se tutti i punti appartengono davvero alla stessa stazione. Devono avere unità e sistema di riferimento dell'input. Per una scansione mobile o una nuvola multistazione, una singola origine arbitraria produrrebbe un'evidenza di visibilità non valida.

```bash
python backend/semantic_resf_cli.py --input rilievo.xyz --output risultati_run_03 --semantics semantics.npz --scanner-origins scanner_origins.npy --catalog examples/family_catalog.json
```

Le origini, se disponibili, distinguono raggi terminati davanti al piano candidato da raggi che lo attraversano. La copertura non osservata rimane sconosciuta. Riflessi, trasparenze, ritorni multipli, registrazione e associazione errata tra punto e stazione possono compromettere questa distinzione.

In alternativa è possibile raccogliere tutto in una richiesta JSON:

```json
{
  "version": "8.0-stepwise",
  "input": "rilievo.xyz",
  "output": "risultati_run_04",
  "semantics": "semantics.npz",
  "scanner_origins": "scanner_origins.npy",
  "family_catalog_path": "catalogo.json",
  "scale_to_m": 1.0,
  "export_ifc": true
}
```

```bash
python backend/semantic_resf_cli.py --request request.json
```

I percorsi contenuti nella richiesta sono relativi alla cartella del file JSON. I percorsi passati direttamente da riga di comando sono relativi alla cartella corrente. Le opzioni esplicite della riga di comando prevalgono sulla richiesta.

## Fasi e artefatti verificabili

Ogni fase completata crea `steps/NN_nome.json`. Le fasi con array producono anche un NPZ. `manifest.json` registra configurazione, ingressi, tempi e stato `running`, `partial`, `complete` o `failed`.

| Fase | Contenuto e controllo |
| --- | --- |
| `01_input` | Sistema di coordinate, numero di righe, hash degli ingressi e disponibilità dei dati ausiliari. |
| `02_preprocess` | Punti mantenuti e mappa `source_indices` nel NPZ; conteggi di filtraggio e campionamento. |
| `03_features` | Normali PCA non orientate e planarità nel NPZ. |
| `04_storeys` | Intervalli di piano e confidenza, con possibilità di livelli espliciti. |
| `05_resf` | Ipotesi multiple per direzione, piani raffinati, estensioni e diagnostica dell'estrazione. Un picco non è ancora un muro. |
| `06_evidence` | A*, E_H, E_V, C, N, contatti e grafo; semantica/visibilità solo quando disponibili; indici del supporto. |
| `07_fuzzy` | Regole attivate e saltate, antecedenti, centroidi, supporti decisionali, classe e motivi. |
| `08_instances` | Muri candidati consolidati, spessori assunti o facce abbinate, provenienza e correzioni limitate degli estremi. |
| `09_feedback` | Distanze, copertura e decisioni ricalcolate; muri accettati e muri da rivedere. |
| `10_objects` | Aperture, spazi, superfici orizzontali e motivazioni delle classificazioni o astensioni. |
| `11_export` | Percorsi degli output finali e risultati della validazione IFC, se richiesto. |

Per fermarsi dopo l'inferenza:

```bash
python backend/semantic_resf_cli.py --input rilievo.xyz --output revisione_fuzzy --stop-after fuzzy
```

Sono ammessi tutti i nomi di fase riportati nella tabella senza numero. L'arresto produce `status: partial` e nessun IFC. **Non è un sistema di ripresa dagli artefatti intermedi**: per cambiare parametri e continuare, eseguire di nuovo in una nuova cartella. I JSON intermedi documentano l'elaborazione e non sono una coda di modifiche manuali applicate automaticamente.

Gli output finali comprendono `bim_model.json`, `object_decisions.json`, CSV per muri, aperture, spazi, superfici, candidati e muri incerti, `result.json`, eventuali `fuzzy_resf.ifc` e `ifc_validation.json`. Il JSON conserva provenienza, anelli interni e informazioni aggiuntive che i CSV non rappresentano completamente. Un esito tecnico `success` può contenere zero muri: verificare conteggi e avvisi.

## Regole fuzzy e feedback

L'inferenza usa appartenenze trapezoidali, congiunzione minimo, implicazione per troncamento, aggregazione massimo e defuzzificazione per centroide. Per ciascuna classe il supporto decisionale è limitato dall'attivazione massima delle regole: un conseguente con centroide alto non può trasformare una regola debolmente attivata in una decisione forte.

Le classi dei muri sono `Permanent`, `OccludedPermanent`, `Clutter`, `Uncertain`. Le classi delle aperture sono `OpenVoid`, `Door`, `Window`, `Uncertain`. Sono supporti indipendenti, non una distribuzione di probabilità. Un antecedente mancante salta la relativa regola e compare in `missing_evidence`/`skipped_rules`.

Le soglie iniziali sono ipotesi ingegneristiche: accettazione `0.55`, margine di ambiguità `0.12`, limite del supporto geometrico `0.85`, limite della confidenza incerta `0.49`. I valori non sono stati calibrati per uno specifico edificio. Le definizioni complete sono in `resf_fuzzy.py`; tramite `config.fuzzy` si possono modificare `memberships`, `output_memberships`, `wall_rules`, `opening_rules` e soglie. Le regole sostitutive rimpiazzano l'intera lista della rispettiva famiglia e vengono validate.

Restano controlli espliciti su supporto geometrico, disponibilità dell'occlusione, conflitto con arredi e semantica di porte/finestre. Il feedback verifica il supporto delle facce osservate dopo il consolidamento, riportando distanze nelle due direzioni e copertura. Un valore di `geometric_support` inferiore a `geometric_feedback_threshold` impedisce l'accettazione del muro. La geometria nascosta non viene conteggiata come superficie misurata.

Lo spessore di un muro con una sola faccia è un'assunzione, e la faccia viene usata come asse del solido. Ciò introduce un'incertezza sul posizionamento fisico delle due superfici finali. L'abbinamento `pair_wall_faces: true` usa facce parallele compatibili, ma **due piani vicini possono appartenere a due partizioni diverse**: il default è disattivato. Anche con l'abbinamento attivo la relazione “stesso muro” rimane un'ipotesi dichiarata.

## Abaco delle famiglie: utilità e limiti

Un abaco è utile perché restringe gli intervalli dimensionali plausibili, evidenzia anomalie e propone famiglie compatibili. Nella v8 il confronto automatico riguarda le **dimensioni delle aperture**: larghezza, altezza e quota di davanzale. L'esempio completo è `examples/family_catalog.json`; il formato minimo è:

```json
{
  "families": [
    {
      "id": "PORTA_P01",
      "class": "Door",
      "width_m": [0.80, 1.00],
      "height_m": [2.00, 2.20],
      "sill_m": [0.00, 0.05]
    },
    {
      "id": "FINESTRA_F01",
      "class": "Window",
      "width_m": [1.00, 1.30],
      "height_m": [1.10, 1.50],
      "sill_m": [0.80, 1.10]
    }
  ]
}
```

Le dimensioni possono essere intervalli `[min,max]`, valori singoli o oggetti `{"min": ..., "max": ...}`. Le corrispondenze ordinate restano in `catalog_suggestions`. Il catalogo sostiene una classe già supportata da evidenza indipendente; non trasforma un buco da occlusione in una porta.

Non vengono importate famiglie Revit `.rfa`, assegnati materiali o creati automaticamente tipi costruttivi parametrici. Gli spessori dei muri e delle superfici orizzontali restano parametri della configurazione, salvo l'abbinamento esplicito di facce. Per estendere il metodo all'abaco dei muri servono corrispondenze tra stratigrafie, spessori, epoca/fase, vincoli geometrici e osservazioni, con gestione delle famiglie assenti e delle eccezioni. Un catalogo incompleto deve consentire la classe “sconosciuta”, senza forzare ogni oggetto nel tipo più vicino.

## CloudCompare

Il preset **`Fuzzy-RESF-BIM 8.0 (Stepwise semantic)`** seleziona il nuovo percorso tramite il contratto JSON del plugin. I preset storici mantengono i propri percorsi. È necessario ricompilare/installare il plugin aggiornato secondo [BUILD.md](BUILD.md); modificare solo Python non aggiunge il preset a una DLL precedente.

La selezione CloudCompare deve essere in metri, con verticale Z e scala globale uguale a 1. La v8 interrompe l'esecuzione se la scala globale è diversa da 1: normalizzare prima le coordinate oppure usare la CLI con `scale_to_m`. Il plugin passa le coordinate locali memorizzate e conserva global shift/scale come metadati per l'allineamento dell'importazione; non converte automaticamente una nuvola locale in millimetri. L'interfaccia attuale non acquisisce file di probabilità, origini scanner o abachi: per queste evidenze usare la CLI. La casella “occlusion” è disabilitata nella v8 nativa perché le origini non sono disponibili.

Le entità importate in CloudCompare sono visualizzazioni della ricostruzione; il file IFC e i JSON contengono relazioni e provenienza. La verifica completa richiede anche il confronto con la nuvola nel sistema di coordinate dichiarato.

## Come migliorare e validare il paper

La bozza dovrebbe separare chiaramente estrazione di ipotesi, classificazione semantica, ricostruzione delle istanze, completamento delle parti nascoste e verifica dell'export. Questa implementazione rende la separazione operativa, ma non elimina l'ambiguità del dato.

1. **Definire ciò che è osservabile.** Specificare formule, denominatori, celle e intervalli usati per A*, E_H, E_V, C e N; dichiarare quando O è disponibile. Documentare unità, scala, orientamento, densità e registrazione del rilievo. Un'assenza di punti non equivale a spazio libero.
2. **Aggiungere evidenza semantica indipendente.** Definire origine, addestramento e calibrazione del classificatore, oppure protocollo di annotazione. Una parete e il fianco di un mobile possono essere geometricamente indistinguibili. Evitare che gli stessi indizi geometrici vengano presentati anche come probabilità semantiche indipendenti.
3. **Validare livelli e topologia.** Solai parziali, controsoffitti, soppalchi, muri non passanti e scansioni incomplete possono alterare i livelli e le chiusure. I contatti del grafo sono evidenze, non prove definitive della funzione architettonica.
4. **Separare misura e prior dimensionale.** Dichiarare spessori assunti, assi inferiti, completamenti e margini di tolleranza. Valutare separatamente l'errore su superfici osservate e l'incertezza sulle parti nascoste.
5. **Misurare oggetti e relazioni.** Oltre alle distanze geometriche, usare precisione/recall/F1 per classe e istanza, errori su dimensioni e quote, correttezza dei legami host–apertura e degli spazi, tasso di astensione e lavoro di revisione. La validazione IFC riguarda schema e geometria del file, non l'esattezza semantica del modello.
6. **Usare edifici indipendenti per validazione.** Separare calibrazione delle regole, scelta delle soglie e test; includere orientamenti obliqui, densità variabile, clutter, occlusioni, casi multipiano e oggetti fuori catalogo. Riportare fallimenti e limiti di memoria/tempo.
7. **Eseguire ablation study controllati.** Confrontare RESF geometrico, aggiunta della topologia, semantica indipendente, visibilità reale, abaco e feedback. Valutare l'abaco anche quando è incompleto o sbagliato, non solo quando contiene la famiglia corretta.

I test automatici e la stanza sintetica verificano proprietà del codice e regressioni. Non giustificano percentuali di accuratezza, robustezza sul patrimonio costruito o equivalenza con risultati precedentemente riportati nel paper. Tali affermazioni richiedono un dataset annotato indipendente e un protocollo pubblicato.

## Verifica del software

```bash
python -m pip install pytest
python -m pytest tests/test_resf_geometry.py tests/test_resf_fuzzy.py tests/test_resf_objects.py tests/test_resf_ifc.py -q
python -m pytest -q
```

L'esportatore riapre l'IFC4 con IfcOpenShell, controlla schema/regole EXPRESS e tessella i solidi; la sottrazione delle aperture viene controllata geometricamente. Gli oggetti incerti restano nei report e non vengono promossi a oggetti BIM accettati per ottenere un modello apparentemente completo.
