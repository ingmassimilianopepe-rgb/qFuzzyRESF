# qFuzzyRESF — dalla nuvola di punti al BIM IFC

Plugin **Standard C++/Qt per CloudCompare 2.14 / Qt 6**, con motore Python locale. Dalla nuvola selezionata produce un IFC4 con pareti parametriche, pavimento, soffitto e **spazi interni provvisori**, oltre ai report di evidenza e validazione.

**Versione software 0.1.0 · metodo pilota 0.3.** Il metodo implementato usa proposte di piano lungo due assi ortogonali e un indice fuzzy monotono. Non implementa ancora una ricostruzione multi-orientamento completa, ray tracing delle occlusioni, porte/finestre o modellazione di pareti curve. Il nome Fuzzy-RESF identifica il progetto di ricerca; non implica che tutte le estensioni previste siano operative.

## Utilizzo in CloudCompare

1. Installare una build compatibile del plugin seguendo [BUILD.md](docs/BUILD.md). I binari Qt 6 non sono compatibili con CloudCompare 2.13 / Qt 5.
2. Installare Python **3.12 x64**, poi eseguire `scripts/install_backend.ps1` su Windows oppure `sh scripts/install_backend.sh` su Linux. Lo script stampa il percorso dell'interprete da usare nel plugin.
3. Aprire la nuvola in CloudCompare con **File → Open**. Allineare Z alla verticale e le pareti principali a X/Y entro circa ±10°. Ritagliare un singolo livello con pavimento e soffitto osservati.
4. Selezionare **una nuvola** nell'albero DB, poi **Plugins → qFuzzyRESF: genera BIM IFC…**.
5. Scegliere l'interprete Python, le unità originali (m/cm/mm), il profilo `Fuzzy monotono v0.3` e un nuovo percorso `.ifc`. Premere **Genera IFC**.
6. Aprire il modello in un visualizzatore/editor IFC e verificare geometria, spessori, stanze e confini virtuali. Gli score sono indici di supporto, non probabilità calibrate.

Il plugin ripristina Global Shift/Scale di CloudCompare e invia le coordinate globali al motore in float64. L'elaborazione resta sul computer. Servono spazio temporaneo di circa **24 byte per punto**, oltre agli output, e RAM proporzionata ai voxel; il limite predefinito è 3 milioni. L'annullamento interrompe il processo; un IFC non validato non viene pubblicato. I file esistenti non vengono sovrascritti.

## Output

- `nome.ifc`: IFC4 con `IfcWall`, `IfcSlab`, `IfcCovering`, `IfcSpace`, profili estrusi modificabili, quantità, relazioni spaziali, confini di primo livello e proprietà `ScanToBIM_Evidence`.
- `nome.fuzzyresf/audit.json`: input, hash SHA-256, scala, origine e diagnostica geometrica.
- `nome.fuzzyresf/inference.json`: proposte, membership, score, pareti e poligoni degli spazi.
- `nome.fuzzyresf/validation.json`: controllo schema EXPRESS, triangolazione/chiusura dei solidi, sovrapposizione degli spazi e confini virtuali.
- `nome.fuzzyresf/summary.json`: versioni delle dipendenze, tempi, conteggi e avvertenze.

Un IFC valido descrive la coerenza formale del file: **non certifica la corrispondenza dell'edificio ricostruito al fabbricato reale**. I confini virtuali non rappresentano pareti osservate. Nessun modello BIM di riferimento viene usato per la ricostruzione.

## Esecuzione senza interfaccia

```bash
python backend/run_pipeline.py --check
python backend/run_pipeline.py --input edificio.las --output edificio.ifc --meters-per-unit 1
```

Sono supportati LAS/LAZ e il formato di scambio XYZ64 del plugin. Gli altri formati vanno aperti con CloudCompare. Per un confronto controllato: `--profile geometry_only`, `--profile legacy`, `--profile monotone`. I tre profili condividono le proposte geometriche e differiscono nel criterio di accettazione.

## Documentazione e test

- [Installazione e compilazione](docs/BUILD.md)
- [Algoritmo, parametri e limiti](docs/METHOD.md)
- [Contratto JSON e annullamento](docs/CONTRACT.md)
- [Verifica sperimentale](docs/VALIDATION.md)

```bash
python -m pip install -r backend/requirements.txt
python -m pytest -q
```

La CI verifica il motore su Linux/Windows e compila CloudCompare con il plugin contro un commit fissato. I pacchetti di prova sono scaricabili dagli **Artifacts** del workflow riuscito; non sono release stabili e richiedono comunque l'ambiente Python.

## Autori e licenza

Massimiliano Pepe e Donato Palumbo — Department of Engineering and Geology, “G. d’Annunzio” University of Chieti-Pescara. Corrispondenza: massimiliano.pepe@unich.it.

GPL-3.0-or-later: vedere [LICENSE](LICENSE). Le dipendenze mantengono le rispettive licenze. Non sono inclusi dati dei casi studio o modelli BIM di riferimento.
