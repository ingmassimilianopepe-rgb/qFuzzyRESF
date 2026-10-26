# Contratto plugin ↔ motore, versione 1

Il plugin esporta una copia della nuvola in XYZ64: triple X,Y,Z **float64 little-endian**, senza intestazione, 24 byte per punto. Le coordinate sono quelle globali originali (`local/globalScale − globalShift`), ancora nelle unità selezionate dall'utente. Colori, normali già calcolate e attributi LAS non vengono usati. La PCA viene ricalcolata sui voxel.

Invocazione senza shell:

```text
<python> <backend>/run_pipeline.py --request <job.json>
```

Esempio di job:

```json
{
  "schema_version": 1,
  "input": {
    "path": "/percorso/selected.xyz64",
    "format": "xyz64",
    "count": 1000000,
    "name": "Edificio piano terra"
  },
  "output": "/percorso/edificio.ifc",
  "meters_per_unit": 1.0,
  "profile": "monotone",
  "mode": "interior",
  "limits": {"max_voxels": 3000000, "max_raster_cells": 4000000},
  "cloudcompare": {
    "global_shift": [0, 0, 0],
    "global_scale": 1.0,
    "export_coordinates": "GLOBAL"
  }
}
```

`work_dir` opzionale è una cartella temporanea, sullo stesso filesystem dell'output, posseduta dal chiamante. Il plugin la usa per garantire la pulizia anche dopo l'arresto forzato del processo. Il motore standalone crea e rimuove autonomamente la propria cartella temporanea.

`mode` è opzionale e vale `interior` (predefinito, compatibilità schema 1) oppure `exterior`. La modalità è sempre esplicita nella GUI, nei report e nel risultato. Nessun fallback automatico dagli interni agli esterni. `exterior` rifiuta il profilo `legacy` prima di elaborare i punti.

I profili validi sono `monotone`, `legacy`, `geometry_only`. Le unità vengono convertite **una sola volta** in `cloud.analyse`. I limiti voxel e raster vengono controllati prima delle operazioni più onerose; non rappresentano una garanzia universale di memoria disponibile. I percorsi di input e output devono essere distinti e l'output deve essere nuovo.

## Eventi stdout

Una riga JSON UTF-8 per evento; nessun dato binario su stdout.

```json
{"event":"progress","percent":55,"stage":"Proposte di parete e supporto fuzzy"}
{"event":"warning","message":"Gli spazi richiedono verifica manuale."}
{"event":"result","output":"/percorso/edificio.ifc","diagnostics":"/percorso/edificio.fuzzyresf","counts":{"IfcWall":5,"IfcSpace":2}}
```

L'evento `result` reale comprende anche hash, versioni, tempi e avvertenze. `error` e `cancelled` contengono `message`. Codici di uscita: 0 successo, 1 errore, 2 argomenti CLI non validi, 130 interruzione gestita. `--check` restituisce `ready` con versioni delle dipendenze.

## Pubblicazione e annullamento

L'IFC viene costruito in staging, riaperto, validato e tessellato. Solo dopo i controlli si crea la cartella diagnostica con accesso esclusivo e si pubblica il file tramite hard link atomico. Nessun file preesistente viene rimpiazzato. Se il filesystem non supporta hard link, l'operazione fallisce senza creare un IFC parziale.

Durante l'esportazione la GUI resta modale: la selezione non può essere modificata mentre viene letta. Il motore gira in un singolo processo Python esterno. `Annulla` invia terminate e, dopo tre secondi, kill se necessario. Su Windows terminate può essere immediato; la cartella temporanea posseduta da Qt viene comunque rimossa al termine del processo. Un arresto forzato del solo motore standalone può lasciare una cartella nascosta `.qfuzzyresf-*`, ma non un IFC parziale pubblicato. Un'interruzione nell'istante finale può lasciare un IFC già validato; il salvataggio non viene retroattivamente annullato.
