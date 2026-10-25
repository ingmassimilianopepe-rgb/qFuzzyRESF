# Verifica del porting e piano di collaudo

## Verifiche eseguite localmente

Data: 25 ottobre 2026. Python 3.12, NumPy 2.3.5, SciPy 1.17.0, Shapely 2.2.0, scikit-image 0.25.2, IfcOpenShell 0.9.0.post1, trimesh 5.1.1.

La suite di **13 test** passa. Il test principale costruisce una nuvola sintetica di due stanze adiacenti, comprese le superfici interne. Verifica l'intera pipeline fino a un IFC4 con **5 pareti e 2 spazi**, profili estrusi, nessun errore schema, solidi chiusi e assenza di sovrapposizioni fra gli spazi.

Gli altri controlli riguardano:

- Input in millimetri, origine globale di ordine milioni di metri e mantenimento del posizionamento IFC.
- Protezione di output preesistenti.
- Coordinate NaN, supporto piatto senza livello ricostruibile, file XYZ64 troncati e superamento del budget voxel.
- Errori CLI strutturati e assenza di IFC parziali.
- Interruzione del motore e pulizia dello staging.
- Stesse proposte geometriche nei profili monotono, legacy e solo geometria.

## Regressione CaseStudy6

Con i voxel, le normali, la planarità e l'audit conservati dal CaseStudy6 Grainger Museum, il porting restituisce:

| Misura | Prototipo v0.3 | Motore del plugin |
|---|---:|---:|
| Proposte geometriche | 130 | 130 |
| Pareti | 83 | 83 |
| Spazi provvisori | 12 | 12 |
| Area totale spazi, m² | 204,763353056 | 204,763353056 |

Le proprietà delle proposte e delle pareti coincidono dopo normalizzazione della serializzazione JSON; le aree dei singoli spazi coincidono entro 10⁻⁹ m². Il confronto usa dati intermedi fissati: **non equivale a ripetere da zero l'intera elaborazione della LAS**, né a confrontare il nuovo IFC con il BIM di riferimento. Il pre-processing del plugin aggiunge controlli, origine numerica locale e padding dell'istogramma Z; queste modifiche sono coperte dai test sintetici e possono cambiare i casi al bordo di una soglia.

I dati originali e i modelli di riferimento non sono pubblicati in questo repository. Per ripetere questa regressione occorrono gli intermedi originali, disponibili al gruppo di ricerca.

## CI e verifiche ancora necessarie

Il workflow compila il plugin insieme a CloudCompare fissato a `001bfd04a13101bfee84a28a6eaa3c7a6338b526` e testa il backend su Linux e Windows. Consultare l'esito del workflow per il commit che si intende installare. Una compilazione riuscita non sostituisce un collaudo grafico.

Da completare su una postazione con GUI:

1. Caricamento effettivo del plugin, visibilità nel menu e abilitazione con una sola nuvola.
2. Nuvola con Global Shift/Scale non banali; confronto visivo dell'IFC nello stesso sistema di coordinate.
3. Percorsi con spazi, Unicode e annullamento sia durante l'esportazione sia durante PCA/IFC.
4. Apertura dell'IFC in almeno due applicazioni indipendenti, controllo spazi, quantità e profili con vuoti.
5. Benchmark completo dei sei casi, registrando hardware, tempi, memoria, deviazioni geometriche, copertura e accuratezza topologica rispetto al riferimento.

Non viene dichiarata accuratezza delle stanze reali sulla base della sola validità IFC. Non vengono attribuite prestazioni superiori alla letteratura senza confronto controllato.
