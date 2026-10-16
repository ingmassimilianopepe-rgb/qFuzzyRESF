# qFuzzyRESF

`qFuzzyRESF` è un plugin Standard di CloudCompare per sviluppare il metodo Scan-to-BIM RESF–FUZZY di Massimiliano Pepe e Donato Palumbo. Il plugin C++/Qt gestisce selezione, interfaccia e scambio dati; il backend Python esegue la ricostruzione.

## Pipeline semantica v8

Il preset **`Fuzzy-RESF-BIM 8.0 (Stepwise semantic)`** introduce una pipeline modificabile con undici fasi ispezionabili: preparazione, normali, livelli, ipotesi RESF multiple, evidenze, inferenza Mamdani, istanze, feedback geometrico, oggetti e IFC4. JSON/NPZ intermedi conservano supporto osservato, regole attivate, dati mancanti, assunzioni e decisioni da rivedere.

Sono trattati muri planari verticali, pavimenti, soffitti, spazi e aperture rettangolari. Porte e finestre richiedono evidenza semantica indipendente; la geometria dell'assemblaggio non viene inventata. Non sono inclusi un classificatore addestrato, pilastri, scale, coperture o muri curvi. Gli score fuzzy sono supporti non calibrati, non probabilità di correttezza.

Avvio riproducibile dalla radice del repository:

```bash
python -m pip install -r backend/requirements.txt
python examples/generate_semantic_room.py --output demo_v8
python backend/semantic_resf_cli.py --request demo_v8/request.json
```

Per una propria nuvola in metri, con verticale Z:

```bash
python backend/semantic_resf_cli.py --input rilievo.xyz --output risultati_run_01
```

La CLI accetta anche probabilità semantiche per punto, origini scanner e un abaco dimensionale delle aperture. Il caso sintetico verifica il funzionamento del codice; non dimostra l'accuratezza su rilievi reali. Ogni esecuzione scrive in una nuova cartella.

La [guida della pipeline semantica](docs/SEMANTIC_PIPELINE.md) descrive contratti degli ingressi, arresto per fase, parametri, abaco, coordinate, limiti metodologici e protocollo di validazione proposto per il paper.

## CloudCompare e percorsi storici

Il nuovo preset passa esplicitamente alla v8 attraverso `backend/fuzzy_resf_cli.py`. I preset precedenti mantengono i backend e i test storici, utili per confronti e regressioni. La nuova pipeline non è vincolata al codice delle versioni precedenti e non ne rivendica automaticamente i risultati sperimentali.

Per vedere il nuovo preset occorre compilare/installare il plugin aggiornato: [istruzioni di build](docs/BUILD.md). Il [contratto storico del backend](docs/BACKEND_CONTRACT.md) resta disponibile; il contratto operativo della v8 è descritto nella nuova guida. L'interfaccia CloudCompare passa la nuvola selezionata; i dati ausiliari si forniscono tramite CLI.

Il pacchetto Windows v8 assemblato contiene CloudCompare e il plugin compilati insieme. Dopo avere estratto tutto lo ZIP, eseguire `SETUP_QFUZZYRESF.cmd` (Python 3.10–3.12 x64 e Internet al primo setup) e poi `START_QFUZZYRESF.cmd`. `TEST_QFUZZYRESF.cmd` esegue una prova del plugin reale su una stanza sintetica. Gli script sono in `packaging/windows/`; `packaging/build_portable.py` assembla l'artefatto binario GitHub con questi helper e verifica il backend contro il commit di compilazione. Una DLL isolata richiede una versione CloudCompare compatibile: usare il programma incluso nel pacchetto.

## Requisiti

- CloudCompare 2.14+, Qt 6 e CMake 3.20+ per il plugin nativo.
- Python 3.10+ per il backend.
- NumPy, SciPy, Shapely e IfcOpenShell: versioni in `backend/requirements.txt`.
- `laspy[lazrs]` opzionale per leggere direttamente LAS/LAZ dalla CLI.

## Struttura

| Percorso | Contenuto |
| --- | --- |
| `src/`, `include/`, `resources/` | Plugin C++/Qt CloudCompare. |
| `backend/semantic_pipeline.py` | Orchestrazione v8 e artefatti per fase. |
| `backend/semantic_resf_cli.py` | CLI della pipeline semantica. |
| `backend/resf_*.py` | Geometria, inferenza fuzzy, oggetti e IFC4 della v8. |
| `backend/` | Anche backend storici e contratti compatibili. |
| `examples/` | Scena sintetica riproducibile e abaco di esempio. |
| `tests/` | Verifiche geometriche, decisionali, IFC e regressioni. |
| `docs/` | Guida operativa e build. |

## Autori

- Massimiliano Pepe — Dipartimento di Ingegneria e Geologia, Università “G. d’Annunzio” di Chieti-Pescara.
- Donato Palumbo — Dipartimento di Ingegneria e Geologia, Università “G. d’Annunzio” di Chieti-Pescara.

Autore corrispondente: massimiliano.pepe@unich.it

## Licenza

GPL-3.0-or-later. Vedere `LICENSE`.
