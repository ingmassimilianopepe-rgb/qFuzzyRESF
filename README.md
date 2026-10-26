# qFuzzyRESF — dalla nuvola di punti al BIM IFC

Plugin **Standard C++/Qt per CloudCompare 2.14 / Qt 6**, con motore Python locale.

**Versione software 0.2.0 · metodo pilota 0.4.** Due modalità esplicite:

| Modalità | Dati richiesti | IFC prodotto |
|---|---|---|
| Interno | Un livello con pavimento e soffitto rilevati; pareti prevalentemente ortogonali, allineate a XY entro ±10° | Pareti, pavimento, soffitto e spazi provvisori |
| Esterno | Rilievo di un edificio con facciate planari e coperture piane o inclinate; Z verticale | Porzioni di parete e falde candidate, estruse nei rispettivi piani; nessuna stanza o livello inventato |

Il modello esterno è **parziale**: i singoli solidi sono chiusi, ma non costituiscono necessariamente un involucro continuo. Le superfici non sono raccordate, gli spessori sono assunti e le aperture non sono classificate. Il supporto fuzzy non è una probabilità calibrata. Non è dimostrata una superiorità rispetto alla letteratura.

## Utilizzo in CloudCompare

1. Estrarre **tutto** il pacchetto Windows in una nuova cartella. Usare il CloudCompare incluso: non copiare la DLL in una build Qt 5 o diversa.
2. Con Python **3.12 x64** installato, fare doppio clic su **INSTALLA_qFuzzyRESF.cmd** nella cartella CloudCompare. Lo script trova le dipendenze usando il proprio percorso e prepara `%LOCALAPPDATA%\qFuzzyRESF\.venv`. Se questo ambiente è già stato installato, viene riutilizzato. Non serve modificare la policy PowerShell. Su Linux: `sh scripts/install_backend.sh`.
3. Avviare il CloudCompare incluso, aprire la nuvola e selezionarla nell'albero DB.
4. Aprire **Plugins → qFuzzyRESF**. Il Python già configurato viene conservato; altrimenti il plugin cerca l'ambiente installato. Controllare il campo **Eseguibile Python**.
5. Scegliere le unità originali, la modalità **Interno** o **Esterno**, il profilo fuzzy monotono e un nuovo nome `.ifc`. Per `point cloud building.las`, scegliere **Esterno** e **Metri**.
6. Premere **Genera IFC**, poi verificare il risultato in un editor IFC. Gli spazi interni restano candidati da validare.

Il plugin ripristina Global Shift/Scale di CloudCompare e invia le coordinate globali al motore in float64. L'elaborazione resta sul computer. Servono spazio temporaneo di circa **24 byte per punto**, oltre agli output, e RAM proporzionata ai voxel; il limite predefinito è 3 milioni. L'annullamento interrompe il processo; un IFC non validato non viene pubblicato. I file esistenti non vengono sovrascritti.

## Output

- `nome.ifc`: IFC4 con profili estrusi modificabili e proprietà `ScanToBIM_Evidence`. Interno: `IfcWall`, `IfcSlab`, `IfcCovering`, `IfcSpace` e confini di primo livello. Esterno: `IfcWall` e `IfcRoof`, senza `IfcSpace` o piani non osservati.
- `nome.fuzzyresf/audit.json`: input, hash SHA-256, scala, origine e diagnostica geometrica.
- `nome.fuzzyresf/inference.json`: proposte, membership, score, pareti e poligoni degli spazi.
- `nome.fuzzyresf/validation.json`: controllo schema EXPRESS, triangolazione/chiusura dei solidi, sovrapposizione degli spazi e confini virtuali.
- `nome.fuzzyresf/summary.json`: versioni delle dipendenze, tempi, conteggi e avvertenze.

Un IFC valido descrive la coerenza formale del file: **non certifica la corrispondenza dell'edificio ricostruito al fabbricato reale**. I confini virtuali non rappresentano pareti osservate. Nessun modello BIM di riferimento viene usato per la ricostruzione.

## Esecuzione senza interfaccia

```bash
python backend/run_pipeline.py --check
python backend/run_pipeline.py --input edificio.las --output edificio.ifc --meters-per-unit 1 --mode exterior
```

Sono supportati LAS/LAZ e il formato di scambio XYZ64 del plugin. Gli altri formati vanno aperti con CloudCompare. Per un confronto controllato: `--profile geometry_only`, `--profile legacy`, `--profile monotone`. Nella modalità interna i tre profili condividono le proposte geometriche. La modalità esterna supporta `monotone` e `geometry_only`; `legacy` è rifiutato. Se omesso, `--mode` mantiene `interior` per compatibilità.

## Documentazione e test

- [Installazione e compilazione](docs/BUILD.md)
- [Algoritmo, parametri e limiti](docs/METHOD.md)
- [Contratto JSON e annullamento](docs/CONTRACT.md)
- [Verifica sperimentale](docs/VALIDATION.md)

```bash
python -m pip install -r backend/requirements.txt
python -m pytest -q
```

La CI verifica il motore su Linux/Windows e compila CloudCompare con il plugin contro un commit fissato. I pacchetti includono il lettore LAS/LAZ di CloudCompare. I pacchetti di prova sono scaricabili dagli **Artifacts** del workflow riuscito; non sono release stabili e richiedono comunque l'ambiente Python.

## Autori e licenza

Massimiliano Pepe e Donato Palumbo — Department of Engineering and Geology, “G. d’Annunzio” University of Chieti-Pescara. Corrispondenza: massimiliano.pepe@unich.it.

GPL-3.0-or-later: vedere [LICENSE](LICENSE). Le dipendenze mantengono le rispettive licenze. Non sono inclusi dati dei casi studio o modelli BIM di riferimento.
